import asyncio
import json
import logging
import redis.asyncio as aioredis
from aiogram import Bot
from aiogram.enums import ParseMode

from config import REDIS_URL, ADMIN_TG_ID
from database import db_connection
from telegram_ops import edit_bot_message
from views import format_primary_card, format_full_ai_card, get_status_keyboard


def format_money(value):
    if value is None:
        return "—"
    return f"{float(value):,.2f}".replace(",", " ")


def parse_jsonb(value, default):
    if value is None:
        return default
    if isinstance(value, str):
        return json.loads(value)
    return value


def build_ai_payload(record):
    return {
        "labor_costs": parse_jsonb(record["labor"], {}),
        "costs": parse_jsonb(record["pricing"], {}),
        "protected_object": parse_jsonb(record["object_info"], {}),
        "requirements": parse_jsonb(record["requirements"], {}),
        "financials": parse_jsonb(record["financial_terms"], {}),
        "risks": parse_jsonb(record["risks"], []),
        "summary": record["summary"],
        "source_url": record["source_url"],
    }


async def redis_listener_task(bot: Bot):
    # Настраиваем пул соединений с оптимизацией под Windows / Docker Desktop
    # health_check_interval раз в 10 секунд не дает сокету "уснуть"
    connection_pool = aioredis.ConnectionPool.from_url(
        REDIS_URL,
        socket_timeout=60.0,         # Даем сокету больше времени на ожидание ответа
        socket_connect_timeout=10.0, # Таймаут только на физическое подключение
        health_check_interval=10     # Автоматический пинг каждые 10 секунд
    )
    redis = aioredis.Redis(connection_pool=connection_pool)
    logging.info("Фоновый слушатель Redis запущен (очереди: new_tenders, ai_completed, tender_updated)")

    while True:
        try:
            # Задаем таймаут (timeout=30 секунд).
            # Теперь brpop не будет зависать бесконечно, а будет перезапускаться каждые 30 секунд.
            result = await redis.brpop(["new_tenders", "ai_completed", "tender_updated"], timeout=30)

            # Если за 30 секунд новых задач не появилось, result будет None — уходим на новый круг
            if result is None:
                continue

            queue_name_bytes, tender_id_bytes = result
            queue_name = queue_name_bytes.decode("utf-8")
            reestr_number = tender_id_bytes.decode("utf-8")

            if queue_name == "new_tenders":
                logging.info(f"⚡️ Парсер нашел тендер: {reestr_number}")

                async with db_connection() as conn:
                    record = await conn.fetchrow(
                        """
                        SELECT title, nmck, region, submission_deadline, source_url
                        FROM tenders
                        WHERE reestr_number = $1
                        """,
                        reestr_number,
                    )

                basic_data = {}
                if record:
                    basic_data = {
                        "title": record["title"],
                        "nmck": format_money(record["nmck"]),
                        "region": record["region"],
                        "submission_deadline": record["submission_deadline"],
                        "source_url": record["source_url"],
                    }

                text = format_primary_card(reestr_number, basic_data)
                msg = await bot.send_message(
                    chat_id=ADMIN_TG_ID,
                    text=text,
                    reply_markup=get_status_keyboard(reestr_number),
                    parse_mode=ParseMode.HTML,
                    disable_web_page_preview=True
                )
                await redis.setex(f"msg_id:{reestr_number}", 604800, msg.message_id)
                await log_notification(reestr_number, ADMIN_TG_ID, "new_tender", "initial")

            elif queue_name == "ai_completed":
                logging.info(f"🧠 ИИ завершил анализ: {reestr_number}")
                msg_id_bytes = await redis.get(f"msg_id:{reestr_number}")

                if not msg_id_bytes:
                    logging.warning(f"Сообщение для {reestr_number} не найдено, ИИ не может обновить карточку.")
                    continue

                msg_id = int(msg_id_bytes.decode("utf-8"))

                async with db_connection() as conn:
                    record = await conn.fetchrow(
                        """
                        SELECT
                            ta.labor,
                            ta.pricing,
                            ta.object_info,
                            ta.requirements,
                            ta.financial_terms,
                            ta.risks,
                            ta.summary,
                            t.source_url
                        FROM tender_analysis ta
                        JOIN tenders t ON t.id = ta.tender_id
                        WHERE t.reestr_number = $1
                        """,
                        reestr_number,
                    )

                if record:
                    ai_data = build_ai_payload(record)
                    text = format_full_ai_card(reestr_number, ai_data, "🔵 ИИ Обработал")
                    keyboard = get_status_keyboard(reestr_number)

                    await edit_bot_message(
                        bot,
                        chat_id=ADMIN_TG_ID,
                        message_id=msg_id,
                        text=text,
                        reply_markup=keyboard,
                        parse_mode=ParseMode.HTML,
                        disable_web_page_preview=True
                    )
                    await log_notification(reestr_number, ADMIN_TG_ID, "ai_completed", "completed")

            elif queue_name == "tender_updated":
                async with db_connection() as conn:
                    record = await conn.fetchrow(
                        """
                        SELECT title, status_on_platform, submission_deadline
                        FROM tenders
                        WHERE reestr_number = $1
                        """,
                        reestr_number,
                    )

                if not record:
                    continue

                dedupe_key = f"{record['status_on_platform']}:{record['submission_deadline']}"
                if await log_notification(reestr_number, ADMIN_TG_ID, "platform_update", dedupe_key):
                    await bot.send_message(
                        chat_id=ADMIN_TG_ID,
                        text=(
                            f"🔔 Изменение по тендеру <code>{reestr_number}</code>\n\n"
                            f"{record['title']}\n"
                            f"Статус площадки: {record['status_on_platform'] or '—'}\n"
                            f"Дедлайн: {record['submission_deadline'] or '—'}"
                        ),
                        reply_markup=get_status_keyboard(reestr_number),
                        parse_mode=ParseMode.HTML,
                    )

        except Exception as e:
            logging.error(f"Ошибка в фоновой задаче Redis: {e}")
            await asyncio.sleep(5)


async def get_user_id_by_telegram(conn, telegram_id: int):
    return await conn.fetchval("SELECT id FROM users WHERE telegram_id = $1", telegram_id)


async def log_notification(reestr_number: str, telegram_id: int, notification_type: str, dedupe_key: str) -> bool:
    async with db_connection() as conn:
        tender_id = await conn.fetchval("SELECT id FROM tenders WHERE reestr_number = $1", reestr_number)
        user_id = await get_user_id_by_telegram(conn, telegram_id)
        if not tender_id or not user_id:
            return False

        result = await conn.execute(
            """
            INSERT INTO notification_log (tender_id, user_id, type, dedupe_key)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (user_id, tender_id, type, dedupe_key) DO NOTHING
            """,
            tender_id,
            user_id,
            notification_type,
            dedupe_key,
        )
        return result.endswith("1")


async def scheduled_notifications_task(bot: Bot):
    while True:
        try:
            await send_deadline_notifications(bot)
            await send_digest_notifications(bot)
        except Exception as e:
            logging.error("Ошибка плановых уведомлений: %s", e)
        await asyncio.sleep(3600)


async def send_deadline_notifications(bot: Bot):
    async with db_connection() as conn:
        rows = await conn.fetch(
            """
            SELECT
                u.id AS user_id,
                u.telegram_id,
                t.id AS tender_id,
                t.reestr_number,
                t.title,
                t.submission_deadline,
                ts.name AS status_name,
                CASE
                    WHEN t.submission_deadline BETWEEN NOW() + INTERVAL '23 hours' AND NOW() + INTERVAL '25 hours' THEN '1d'
                    WHEN t.submission_deadline BETWEEN NOW() + INTERVAL '71 hours' AND NOW() + INTERVAL '73 hours' THEN '3d'
                    ELSE NULL
                END AS window_key
            FROM tenders t
            JOIN user_tender_status uts ON uts.tender_id = t.id AND uts.status_code IN (4, 7)
            JOIN users u ON u.id = uts.user_id
            JOIN tender_statuses ts ON ts.code = uts.status_code
            WHERE t.submission_deadline BETWEEN NOW() AND NOW() + INTERVAL '4 days'
            """
        )

    for row in rows:
        if not row["window_key"]:
            continue
        dedupe_key = f"{row['window_key']}:{row['submission_deadline']}"
        if await log_notification(row["reestr_number"], row["telegram_id"], "deadline", dedupe_key):
            await bot.send_message(
                chat_id=row["telegram_id"],
                text=(
                    f"⏰ Дедлайн близко: <code>{row['reestr_number']}</code>\n\n"
                    f"{row['title']}\n"
                    f"Статус: {row['status_name']}\n"
                    f"Подача до: {row['submission_deadline']}"
                ),
                reply_markup=get_status_keyboard(row["reestr_number"]),
                parse_mode=ParseMode.HTML,
            )


async def send_digest_notifications(bot: Bot):
    async with db_connection() as conn:
        users = await conn.fetch(
            """
            SELECT id, telegram_id
            FROM users
            WHERE digest_enabled IS TRUE
              AND digest_hour = EXTRACT(HOUR FROM NOW() AT TIME ZONE 'Europe/Moscow')::int
            """
        )
        for user in users:
            already_sent = await conn.fetchval(
                """
                SELECT 1
                FROM notification_log
                WHERE user_id = $1
                  AND type = 'digest'
                  AND dedupe_key = TO_CHAR(NOW() AT TIME ZONE 'Europe/Moscow', 'YYYY-MM-DD')
                """,
                user["id"],
            )
            if already_sent:
                continue

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
            await conn.execute(
                """
                INSERT INTO notification_log (user_id, type, dedupe_key)
                VALUES ($1, 'digest', TO_CHAR(NOW() AT TIME ZONE 'Europe/Moscow', 'YYYY-MM-DD'))
                """,
                user["id"],
            )
            await bot.send_message(
                chat_id=user["telegram_id"],
                text=f"📬 Дайджест за сутки\n\nНовых тендеров: {new_count}\nГорящих дедлайнов: {hot_count}",
            )
