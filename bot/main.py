import asyncio
import base64
import csv
import io
import json
import logging
import os
from uuid import uuid4

import redis.asyncio as aioredis
try:
    from celery import Celery
except ImportError:  # Позволяет старому контейнеру восстановить основной бот до rebuild.
    Celery = None
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.enums import ParseMode
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from aiogram.types import BufferedInputFile, FSInputFile
from aiogram.exceptions import TelegramNetworkError

from config import (
    ADMIN_TG_ID,
    BOT_TOKEN,
    PROXY_URL,
    REDIS_URL,
    TELEGRAM_API_TIMEOUT,
    TELEGRAM_POLLING_TIMEOUT,
)
from database import close_db_pool, db_connection, init_db_pool
from telegram_ops import answer_callback, edit_message
from views import (
    PAGE_SIZE,
    STATUS_CODES,
    format_full_card,
    format_tender_list,
    get_docs_keyboard,
    get_list_keyboard,
    get_parser_keyboard,
    get_status_keyboard,
)
from background import redis_listener_task, scheduled_notifications_task

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

bot: Bot | None = None
dp = Dispatcher()
parser_client = Celery("tender_bot_control", broker=REDIS_URL) if Celery else None


def create_bot(proxy_url: str = "") -> Bot:
    session = (
        AiohttpSession(api=TelegramAPIServer.from_base(proxy_url), timeout=TELEGRAM_API_TIMEOUT)
        if proxy_url
        else AiohttpSession(timeout=TELEGRAM_API_TIMEOUT)
    )
    return Bot(token=BOT_TOKEN, session=session)


async def connect_telegram() -> Bot:
    """Подключается к Telegram без падения контейнера при сбое DNS прокси."""
    candidates = [("прокси", PROXY_URL)] if PROXY_URL else []
    candidates.append(("напрямую", ""))
    attempt = 0

    while True:
        label, proxy_url = candidates[attempt % len(candidates)]
        candidate = create_bot(proxy_url)
        try:
            await candidate.delete_webhook(drop_pending_updates=False)
            logging.info("Telegram подключён %s", label)
            return candidate
        except TelegramNetworkError as error:
            await candidate.session.close()
            attempt += 1
            delay = min(30, 2 ** min(attempt, 4))
            logging.warning("Telegram недоступен %s: %s. Повтор через %s с.", label, error, delay)
            await asyncio.sleep(delay)


async def ensure_schema():
    async with db_connection() as conn:
        await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS digest_enabled BOOLEAN DEFAULT TRUE")
        await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS digest_hour INT DEFAULT 9")
        await conn.execute("ALTER TABLE notification_log ADD COLUMN IF NOT EXISTS dedupe_key VARCHAR(255)")
        await conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_notification_log_dedupe
            ON notification_log(user_id, tender_id, type, dedupe_key)
            """
        )
async def get_or_create_admin_user(conn, telegram_id: int):
    if telegram_id == ADMIN_TG_ID:
        return await conn.fetchrow(
            """
            INSERT INTO users (telegram_id, role)
            VALUES ($1, 'admin')
            ON CONFLICT (telegram_id) DO UPDATE SET role = EXCLUDED.role
            RETURNING id, telegram_id, role
            """,
            telegram_id,
        )

    return await conn.fetchrow(
        "SELECT id, telegram_id, role FROM users WHERE telegram_id = $1",
        telegram_id,
    )


async def require_user(telegram_id: int):
    async with db_connection() as conn:
        return await get_or_create_admin_user(conn, telegram_id)


async def enqueue_parser_run(debug: bool) -> str:
    """Отправляет задачу parser worker-у, не выполняя парсинг внутри бота."""
    task_kwargs = {"debug": debug, "max_pages": 1 if debug else None}
    if parser_client is not None:
        result = await asyncio.to_thread(
            parser_client.send_task,
            "tasks.run_eis_parser",
            kwargs=task_kwargs,
        )
        return result.id

    task_id, message = build_celery_message("tasks.run_eis_parser", task_kwargs)
    redis_client = aioredis.from_url(REDIS_URL)
    try:
        await redis_client.lpush("celery", message)
    finally:
        await redis_client.close()
    return task_id


def build_celery_message(task_name: str, task_kwargs: dict) -> tuple[str, str]:
    """Минимальный Celery protocol v2 для fallback без пакета celery в bot."""
    task_id = str(uuid4())
    reply_to = str(uuid4())
    body = json.dumps([[], task_kwargs, {
        "callbacks": None,
        "errbacks": None,
        "chain": None,
        "chord": None,
    }], ensure_ascii=False).encode("utf-8")
    message = {
        "body": base64.b64encode(body).decode("ascii"),
        "content-encoding": "utf-8",
        "content-type": "application/json",
        "headers": {
            "lang": "py",
            "task": task_name,
            "id": task_id,
            "shadow": None,
            "eta": None,
            "expires": None,
            "group": None,
            "group_index": None,
            "retries": 0,
            "timelimit": [None, None],
            "root_id": task_id,
            "parent_id": None,
            "argsrepr": "()",
            "kwargsrepr": repr(task_kwargs),
            "origin": "tender_bot",
            "ignore_result": False,
            "replaced_task_nesting": 0,
            "stamped_headers": None,
            "stamps": {},
        },
        "properties": {
            "correlation_id": task_id,
            "reply_to": reply_to,
            "delivery_mode": 2,
            "delivery_info": {"exchange": "", "routing_key": "celery"},
            "priority": 0,
            "body_encoding": "base64",
            "delivery_tag": str(uuid4()),
        },
    }
    return task_id, json.dumps(message, ensure_ascii=False)


async def fetch_tenders_page(user_id: int, page: int):
    offset = max(page, 0) * PAGE_SIZE
    async with db_connection() as conn:
        total = await conn.fetchval("SELECT COUNT(*) FROM tenders")
        rows = await conn.fetch(
            """
            SELECT
                t.reestr_number,
                t.title,
                t.nmck,
                t.region,
                t.submission_deadline,
                COALESCE(ts.name, '⚪ Не указан') AS status_name
            FROM tenders t
            LEFT JOIN user_tender_status uts
                ON uts.tender_id = t.id AND uts.user_id = $1
            LEFT JOIN tender_statuses ts ON ts.code = COALESCE(uts.status_code, 6)
            ORDER BY t.submission_deadline NULLS LAST, t.first_seen_at DESC
            LIMIT $2 OFFSET $3
            """,
            user_id,
            PAGE_SIZE + 1,
            offset,
        )
        has_next = len(rows) > PAGE_SIZE
        return rows[:PAGE_SIZE], page, total, has_next


async def fetch_card(user_id: int, reestr_number: str):
    async with db_connection() as conn:
        row = await conn.fetchrow(
            """
            SELECT
                t.id,
                t.reestr_number,
                t.platform,
                t.law_type,
                t.title,
                t.customer_name,
                t.customer_inn,
                t.nmck,
                t.submission_deadline,
                t.region,
                t.status_on_platform,
                t.source_url,
                COALESCE(ts.name, '⚪ Не указан') AS status_name,
                ta.summary,
                ta.analysis_status
            FROM tenders t
            LEFT JOIN user_tender_status uts
                ON uts.tender_id = t.id AND uts.user_id = $1
            LEFT JOIN tender_statuses ts ON ts.code = COALESCE(uts.status_code, 6)
            LEFT JOIN tender_analysis ta ON ta.tender_id = t.id
            WHERE t.reestr_number = $2
            """,
            user_id,
            reestr_number,
        )
        if not row:
            return None, 0
        docs_count = await conn.fetchval("SELECT COUNT(*) FROM tender_documents WHERE tender_id = $1", row["id"])
        return row, docs_count


async def send_tenders_page(target, user_id: int, page: int, edit: bool = False):
    rows, page, total, has_next = await fetch_tenders_page(user_id, page)
    text = format_tender_list(rows, page, total)
    keyboard = get_list_keyboard(rows, page, has_next) if rows else None
    if edit:
        await edit_message(target, text, reply_markup=keyboard, parse_mode=ParseMode.HTML)
    else:
        await target.answer(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)


async def render_callback_error(callback: types.CallbackQuery, text: str) -> None:
    """Leaves a visible result after an already-acknowledged callback fails."""
    if callback.message is None:
        return
    try:
        await edit_message(callback.message, f"⚠️ {text}", disable_web_page_preview=True)
    except Exception:
        logging.exception("Не удалось показать пользователю ошибку callback")


async def authorize_callback(callback: types.CallbackQuery, *, admin_only: bool = False):
    try:
        user = await require_user(callback.from_user.id)
    except Exception:
        logging.exception("Ошибка проверки доступа пользователя %s", callback.from_user.id)
        await answer_callback(callback)
        await render_callback_error(callback, "Сервис базы данных временно недоступен. Попробуйте ещё раз.")
        return None

    if not user:
        await answer_callback(callback, "Нет доступа", show_alert=True)
        return None
    if admin_only and user["role"] != "admin":
        await answer_callback(callback, "Запуск доступен только администратору", show_alert=True)
        return None

    await answer_callback(callback)
    return user


def resolve_document_path(file_path: str) -> str:
    if os.path.exists(file_path):
        return file_path
    if file_path.startswith("/data/tenders/"):
        local_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", file_path.lstrip("/")))
        if os.path.exists(local_path):
            return local_path
    return file_path


@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    user = await require_user(message.from_user.id)
    if not user:
        await message.answer("⛔ У вас нет доступа к этому боту.")
        return

    role = "Администратор" if user["role"] == "admin" else "Менеджер"
    await message.answer(
        f"👋 Привет! Вы авторизованы как {role}.\n\n"
        "Команды:\n"
        "/tenders — список тендеров\n"
        "/digest — сводка за сутки\n"
        "/export — CSV-выгрузка\n"
        "/search — запустить поиск ЕИС",
        reply_markup=get_parser_keyboard() if user["role"] == "admin" else None,
        parse_mode=ParseMode.HTML,
    )


async def run_parser_from_message(message: types.Message, user, debug: bool) -> None:
    if user["role"] != "admin":
        await message.answer("⛔ Запуск парсера доступен только администратору.")
        return

    try:
        task_id = await enqueue_parser_run(debug)
    except Exception as error:
        logging.exception("Не удалось поставить задачу поиска ЕИС")
        await message.answer(f"Не удалось поставить поиск в очередь: {error}")
        return

    if debug:
        await message.answer(
            "🧪 Тестовый поиск ЕИС запущен на одной странице. Результаты будут только в логах "
            "контейнера `parser`: БД, ИИ и уведомления не затрагиваются.\n"
            f"Задача: <code>{task_id}</code>",
            parse_mode=ParseMode.HTML,
        )
    else:
        await message.answer(
            "🔎 Поиск ЕИС поставлен в очередь. Новые закупки пройдут обычный рабочий конвейер.\n"
            f"Задача: <code>{task_id}</code>",
            parse_mode=ParseMode.HTML,
        )


@dp.message(Command("search"))
async def cmd_search(message: types.Message):
    user = await require_user(message.from_user.id)
    if not user:
        await message.answer("⛔ У вас нет доступа к этому боту.")
        return
    await run_parser_from_message(message, user, debug=False)


@dp.callback_query(F.data.startswith("parser:"))
async def parser_callback(callback: types.CallbackQuery):
    user = await authorize_callback(callback, admin_only=True)
    if user is None:
        return

    debug = callback.data == "parser:debug"
    try:
        await run_parser_from_message(callback.message, user, debug=debug)
    except Exception:
        logging.exception("Не удалось запустить задачу парсера из callback")
        await render_callback_error(callback, "Не удалось поставить поиск в очередь. Попробуйте ещё раз.")


@dp.message(Command("tenders"))
async def cmd_tenders(message: types.Message):
    user = await require_user(message.from_user.id)
    if not user:
        await message.answer("⛔ У вас нет доступа к этому боту.")
        return
    await send_tenders_page(message, user["id"], 0)


@dp.callback_query(F.data.startswith("list:"))
async def list_callback(callback: types.CallbackQuery):
    user = await authorize_callback(callback)
    if user is None:
        return
    page = int(callback.data.split(":")[1])
    try:
        await send_tenders_page(callback.message, user["id"], page, edit=True)
    except Exception:
        logging.exception("Не удалось открыть страницу тендеров")
        await render_callback_error(callback, "Не удалось загрузить список тендеров. Попробуйте ещё раз.")


@dp.callback_query(F.data.startswith("card:"))
async def card_callback(callback: types.CallbackQuery):
    user = await authorize_callback(callback)
    if user is None:
        return

    reestr_number = callback.data.split(":", 1)[1]
    try:
        row, docs_count = await fetch_card(user["id"], reestr_number)
        if not row:
            await render_callback_error(callback, "Тендер больше не найден в базе данных.")
            return

        await edit_message(
            callback.message,
            format_full_card(row, docs_count),
            reply_markup=get_status_keyboard(reestr_number),
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
    except Exception:
        logging.exception("Не удалось открыть карточку тендера %s", reestr_number)
        await render_callback_error(callback, "Не удалось загрузить карточку тендера. Попробуйте ещё раз.")


@dp.callback_query(F.data.startswith("status:"))
async def process_status_callback(callback: types.CallbackQuery):
    user = await authorize_callback(callback)
    if user is None:
        return

    _, status_key, reestr_number = callback.data.split(":")
    status_code = STATUS_CODES[status_key]
    try:
        async with db_connection() as conn:
            tender = await conn.fetchrow("SELECT id FROM tenders WHERE reestr_number = $1", reestr_number)
            if not tender:
                await render_callback_error(callback, "Тендер больше не найден в базе данных.")
                return

            await conn.execute(
                """
                INSERT INTO user_tender_status (user_id, tender_id, status_code, updated_at)
                VALUES ($1, $2, $3, NOW())
                ON CONFLICT (user_id, tender_id) DO UPDATE SET
                    status_code = EXCLUDED.status_code,
                    updated_at = NOW()
                """,
                user["id"],
                tender["id"],
                status_code,
            )

        row, docs_count = await fetch_card(user["id"], reestr_number)
        await edit_message(
            callback.message,
            format_full_card(row, docs_count),
            reply_markup=get_status_keyboard(reestr_number),
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
    except Exception:
        logging.exception("Не удалось сохранить статус тендера %s", reestr_number)
        await render_callback_error(callback, "Не удалось сохранить статус. Попробуйте ещё раз.")


@dp.callback_query(F.data.startswith("docs:"))
async def docs_callback(callback: types.CallbackQuery):
    user = await authorize_callback(callback)
    if user is None:
        return

    reestr_number = callback.data.split(":", 1)[1]
    try:
        async with db_connection() as conn:
            rows = await conn.fetch(
                """
                SELECT td.id::text AS id, td.file_name, td.file_path
                FROM tender_documents td
                JOIN tenders t ON t.id = td.tender_id
                WHERE t.reestr_number = $1
                ORDER BY td.version DESC, td.downloaded_at DESC
                """,
                reestr_number,
            )

        if not rows:
            await render_callback_error(callback, "Документы для этого тендера не найдены.")
            return

        await edit_message(
            callback.message,
            "📁 Документы тендера:",
            reply_markup=get_docs_keyboard(rows, reestr_number),
            parse_mode=ParseMode.HTML,
        )
    except Exception:
        logging.exception("Не удалось открыть документы тендера %s", reestr_number)
        await render_callback_error(callback, "Не удалось загрузить документы. Попробуйте ещё раз.")


@dp.callback_query(F.data.startswith("senddoc:"))
async def send_document_callback(callback: types.CallbackQuery):
    user = await authorize_callback(callback)
    if user is None:
        return

    _, document_id = callback.data.split(":", 1)
    try:
        async with db_connection() as conn:
            row = await conn.fetchrow("SELECT file_name, file_path FROM tender_documents WHERE id = $1::uuid", document_id)

        if not row:
            await render_callback_error(callback, "Документ больше не найден в базе данных.")
            return

        path = resolve_document_path(row["file_path"])
        if not os.path.exists(path):
            await render_callback_error(callback, "Файл документа отсутствует на диске.")
            return

        if os.path.getsize(path) > 49 * 1024 * 1024:
            await render_callback_error(callback, "Файл больше лимита Telegram 50 МБ.")
            return

        # sendDocument is intentionally not retried: a timed-out response can still mean a delivered file.
        await callback.message.answer_document(FSInputFile(path, filename=row["file_name"]))
    except Exception:
        logging.exception("Не удалось отправить документ %s", document_id)
        await render_callback_error(callback, "Не удалось отправить документ. Попробуйте ещё раз.")


@dp.callback_query(F.data.startswith("reanalyze:"))
async def reanalyze_callback(callback: types.CallbackQuery):
    user = await authorize_callback(callback)
    if user is None:
        return

    reestr_number = callback.data.split(":", 1)[1]
    try:
        async with db_connection() as conn:
            tender_id = await conn.fetchval("SELECT id::text FROM tenders WHERE reestr_number = $1", reestr_number)

        if not tender_id:
            await render_callback_error(callback, "Тендер больше не найден в базе данных.")
            return

        redis = aioredis.from_url(REDIS_URL)
        try:
            await redis.lpush("analysis_tenders", tender_id)
        finally:
            await redis.close()
        await edit_message(callback.message, "🧠 Повторный анализ поставлен в очередь.")
    except Exception:
        logging.exception("Не удалось поставить тендер %s на повторный анализ", reestr_number)
        await render_callback_error(callback, "Не удалось поставить повторный анализ в очередь. Попробуйте ещё раз.")


async def export_csv(message_or_callback, user_id: int):
    async with db_connection() as conn:
        rows = await conn.fetch(
            """
            SELECT
                t.reestr_number,
                t.title,
                t.customer_name,
                t.nmck,
                t.region,
                t.law_type,
                t.submission_deadline,
                COALESCE(ts.name, '⚪ Не указан') AS status_name,
                t.source_url
            FROM tenders t
            LEFT JOIN user_tender_status uts
                ON uts.tender_id = t.id AND uts.user_id = $1
            LEFT JOIN tender_statuses ts ON ts.code = COALESCE(uts.status_code, 6)
            ORDER BY t.submission_deadline NULLS LAST, t.first_seen_at DESC
            """,
            user_id,
        )

    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";")
    writer.writerow(["reestr_number", "title", "customer", "nmck", "region", "law_type", "deadline", "status", "url"])
    for row in rows:
        writer.writerow([
            row["reestr_number"],
            row["title"],
            row["customer_name"],
            row["nmck"],
            row["region"],
            row["law_type"],
            row["submission_deadline"],
            row["status_name"],
            row["source_url"],
        ])

    content = buffer.getvalue().encode("utf-8-sig")
    file = BufferedInputFile(content, filename="tenders.csv")
    target = message_or_callback.message if isinstance(message_or_callback, types.CallbackQuery) else message_or_callback
    await target.answer_document(file)


@dp.message(Command("export"))
async def cmd_export(message: types.Message):
    user = await require_user(message.from_user.id)
    if not user:
        await message.answer("⛔ У вас нет доступа к этому боту.")
        return
    await export_csv(message, user["id"])


@dp.callback_query(F.data == "export:csv")
async def export_callback(callback: types.CallbackQuery):
    user = await authorize_callback(callback)
    if user is None:
        return
    try:
        # CSV is sent once without automatic retry for the same reason as tender documents.
        await export_csv(callback, user["id"])
    except Exception:
        logging.exception("Не удалось сформировать CSV-выгрузку")
        await render_callback_error(callback, "Не удалось подготовить CSV. Попробуйте ещё раз.")


@dp.message(Command("digest"))
async def cmd_digest(message: types.Message):
    user = await require_user(message.from_user.id)
    if not user:
        await message.answer("⛔ У вас нет доступа к этому боту.")
        return

    async with db_connection() as conn:
        new_count = await conn.fetchval("SELECT COUNT(*) FROM tenders WHERE first_seen_at >= NOW() - INTERVAL '1 day'")
        hot_count = await conn.fetchval(
            """
            SELECT COUNT(*)
            FROM tenders t
            JOIN user_tender_status uts ON uts.tender_id = t.id AND uts.user_id = $1
            WHERE uts.status_code IN (4, 7)
              AND t.submission_deadline BETWEEN NOW() AND NOW() + INTERVAL '3 days'
            """,
            user["id"],
        )

    await message.answer(
        f"📬 Дайджест за сутки\n\n"
        f"Новых тендеров: {new_count}\n"
        f"Горящих дедлайнов по целевым/сомнительным: {hot_count}"
    )


async def main():
    global bot
    if not BOT_TOKEN:
        logging.error("TELEGRAM_BOT_TOKEN не задан!")
        return

    await init_db_pool()
    bot = await connect_telegram()
    await ensure_schema()
    logging.info("Запуск бота: транспорт Telegram выбран по результату подключения")

    asyncio.create_task(redis_listener_task(bot))
    asyncio.create_task(scheduled_notifications_task(bot))

    try:
        await dp.start_polling(bot, polling_timeout=TELEGRAM_POLLING_TIMEOUT)
    finally:
        await bot.session.close()
        await close_db_pool()


if __name__ == "__main__":
    asyncio.run(main())
