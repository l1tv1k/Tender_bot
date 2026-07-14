import asyncio
import logging
import os
import json
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.enums import ParseMode
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from aiogram.utils.keyboard import InlineKeyboardBuilder
from dotenv import load_dotenv
import asyncpg

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
PROXY_URL = os.getenv("TELEGRAM_PROXY_URL")
ADMIN_TG_ID = int(os.getenv("ADMIN_TG_ID", 0))

# Настройки БД
DB_CONFIG = {
    "user": os.getenv("DB_USER"),
    "password": os.getenv("DB_PASS"),
    "host": os.getenv("DB_HOST"),
    "database": os.getenv("DB_NAME")
}

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- НАСТРОЙКА ПРОКСИ ДЛЯ ТЕЛЕГРАМ ---
# Перенаправляем все запросы бота через ваш Cloudflare Worker
session = AiohttpSession(
    api=TelegramAPIServer.from_base(PROXY_URL)
)
bot = Bot(token=BOT_TOKEN, session=session)
dp = Dispatcher()

# --- СЛОВАРЬ СТАТУСОВ ---
STATUS_MAP = {
    "target": "🔷 Целевой",
    "doubt": "🟡 Под сомнением",
    "work": "✅ В работе",
    "reject": "❌ Отказ"
}

def get_status_keyboard(reestr_number: str) -> types.InlineKeyboardMarkup:
    """Генерация клавиатуры со статусами для конкретного тендера"""
    builder = InlineKeyboardBuilder()
    for callback_data, label in STATUS_MAP.items():
        # callback_data будет иметь вид: "status:target:00000000000"
        builder.button(text=label, callback_data=f"status:{callback_data}:{reestr_number}")

    # Кнопка скачивания документов (заглушка для будущего функционала)
    builder.button(text="📁 Скачать ТЗ", callback_data=f"doc:{reestr_number}")

    # Настраиваем сетку кнопок: 2 в ряд для статусов, 1 для документов
    builder.adjust(2, 2, 1)
    return builder.as_markup()

def format_tender_card(reestr_number: str, data: dict, current_status: str = "Не обработан") -> str:
    """Формирование компактной карточки, чтобы не пробить лимит в 4096 символов"""
    nmck = data.get('costs', {}).get('total_nmck', '—')
    posts = data.get('labor_costs', {}).get('guard_posts', '—')

    msg = (
        f"🚨 Тендер: {reestr_number}\n"
        f"📊 Статус: {current_status}\n\n"
        f"💰 НМЦК: {nmck} руб.\n"
        f"👮 Посты: {posts}\n\n"
        f"📝 Сводка от ИИ:\n{data.get('summary', 'Нет данных')}\n\n"
        f"🔗 Открыть в ЕИС"
    )
    return msg

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    role = "Администратор" if user_id == ADMIN_TG_ID else "Менеджер"

    await message.answer(
        f"👋 Привет! Вы авторизованы как {role}.\n"
        f"Используйте команду /latest для просмотра последних тендеров.",
        parse_mode=ParseMode.HTML
    )

@dp.message(Command("latest"))
async def cmd_latest(message: types.Message):
    """Тестовая команда для вывода последнего тендера с клавиатурой"""
    conn = await asyncpg.connect(**DB_CONFIG)
    record = await conn.fetchrow("SELECT reestr_number, analysis_data FROM tender_analysis ORDER BY created_at DESC LIMIT 1")
    await conn.close()

    if record:
        reestr_number = record['reestr_number']
        analysis_data = json.loads(record['analysis_data'])

        # Рендерим карточку и прикрепляем клавиатуру
        text = format_tender_card(reestr_number, analysis_data)
        keyboard = get_status_keyboard(reestr_number)

        await message.answer(text, reply_markup=keyboard, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
    else:
        await message.answer("В базе нет проанализированных тендеров.")

@dp.callback_query(F.data.startswith("status:"))
async def process_status_callback(callback: types.CallbackQuery):
    """Обработка нажатий на кнопки статуса"""
    _, status_key, reestr_number = callback.data.split(":")
    new_status_label = STATUS_MAP[status_key]

    # Здесь в будущем будет логика обновления поля status в БД
    # await db.update_tender_status(reestr_number, status_key)

    await callback.answer(f"Статус изменен на: {new_status_label}")

    # Обновляем текст сообщения, чтобы отразить новый статус
    # (В реальной жизни мы бы достали JSON из базы еще раз, тут для примера просто меняем строку)
    old_text = callback.message.html_text

    try:
        # Небольшой хак для обновления строки со статусом
        new_text = "\n".join([line if "Статус:" not in line else f"📊 Статус: {new_status_label}" for line in old_text.split('\n')])
        await callback.message.edit_text(
            new_text,
            reply_markup=get_status_keyboard(reestr_number),
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True
        )
    except Exception as e:
        logging.error(f"Не удалось обновить сообщение: {e}")

async def main():
    if not PROXY_URL:
        logging.error("TELEGRAM_PROXY_URL не задан!")
        return

    logging.info(f"Запуск бота через прокси: {PROXY_URL}")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())