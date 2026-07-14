import asyncio
import logging
import json
import asyncpg

from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.enums import ParseMode
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer

# Импортируем из наших модулей!
from config import BOT_TOKEN, PROXY_URL, ADMIN_TG_ID, DB_CONFIG
from views import format_full_ai_card, get_status_keyboard, STATUS_MAP
from background import redis_listener_task

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Настройка прокси
session = AiohttpSession(api=TelegramAPIServer.from_base(PROXY_URL))
bot = Bot(token=BOT_TOKEN, session=session)
dp = Dispatcher()


@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    role = "Администратор" if message.from_user.id == ADMIN_TG_ID else "Менеджер"
    await message.answer(f"👋 Привет! Вы авторизованы как {role}.", parse_mode=ParseMode.HTML)


@dp.callback_query(F.data.startswith("status:"))
async def process_status_callback(callback: types.CallbackQuery):
    _, status_key, reestr_number = callback.data.split(":")
    new_status_label = STATUS_MAP[status_key]

    await callback.answer(f"Статус изменен на: {new_status_label}")

    old_text = callback.message.html_text
    try:
        # Небольшой хак для обновления строки со статусом
        new_text = "\n".join(
            [line if "Статус:" not in line else f"📊 Статус: {new_status_label}" for line in old_text.split('\n')])
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

    # Запускаем фоновый слушатель Redis, чтобы работали push-уведомления!
    asyncio.create_task(redis_listener_task(bot))

    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())