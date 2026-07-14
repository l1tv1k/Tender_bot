import asyncio
import json
import logging
import asyncpg
import redis.asyncio as aioredis
from aiogram import Bot
from aiogram.enums import ParseMode

from config import REDIS_URL, DB_CONFIG, ADMIN_TG_ID
from views import format_primary_card, format_full_ai_card, get_status_keyboard


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
    logging.info("Фоновый слушатель Redis запущен (очереди: new_tenders, ai_completed)")

    while True:
        try:
            # Задаем таймаут (timeout=30 секунд).
            # Теперь brpop не будет зависать бесконечно, а будет перезапускаться каждые 30 секунд.
            result = await redis.brpop(["new_tenders", "ai_completed"], timeout=30)

            # Если за 30 секунд новых задач не появилось, result будет None — уходим на новый круг
            if result is None:
                continue

            queue_name_bytes, tender_id_bytes = result
            queue_name = queue_name_bytes.decode("utf-8")
            reestr_number = tender_id_bytes.decode("utf-8")

            if queue_name == "new_tenders":
                logging.info(f"⚡️ Парсер нашел тендер: {reestr_number}")
                basic_data = {"nmck": "1 500 000", "region": "Москва"}

                text = format_primary_card(reestr_number, basic_data)
                msg = await bot.send_message(
                    chat_id=ADMIN_TG_ID,
                    text=text,
                    parse_mode=ParseMode.HTML,
                    disable_web_page_preview=True
                )
                await redis.setex(f"msg_id:{reestr_number}", 604800, msg.message_id)

            elif queue_name == "ai_completed":
                logging.info(f"🧠 ИИ завершил анализ: {reestr_number}")
                msg_id_bytes = await redis.get(f"msg_id:{reestr_number}")

                if not msg_id_bytes:
                    logging.warning(f"Сообщение для {reestr_number} не найдено, ИИ не может обновить карточку.")
                    continue

                msg_id = int(msg_id_bytes.decode("utf-8"))

                conn = await asyncpg.connect(**DB_CONFIG)
                record = await conn.fetchrow("SELECT analysis_data FROM tender_analysis WHERE reestr_number = $1",
                                             reestr_number)
                await conn.close()

                if record:
                    ai_data = json.loads(record['analysis_data'])
                    text = format_full_ai_card(reestr_number, ai_data, "🔵 ИИ Обработал")
                    keyboard = get_status_keyboard(reestr_number)

                    await bot.edit_message_text(
                        chat_id=ADMIN_TG_ID,
                        message_id=msg_id,
                        text=text,
                        reply_markup=keyboard,
                        parse_mode=ParseMode.HTML,
                        disable_web_page_preview=True
                    )

        except Exception as e:
            logging.error(f"Ошибка в фоновой задаче Redis: {e}")
            await asyncio.sleep(5)