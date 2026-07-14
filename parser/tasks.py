import asyncio
import redis
import redis.asyncio as aioredis # Добавляем этот импорт наверх
from celery import Celery
from celery.schedules import crontab
from core.browser_manager import BrowserManager
from platforms.eis_parser import EisParser
from core.database import AsyncSessionLocal, save_or_update_tender
import logging
import os

logger = logging.getLogger(__name__)
app = Celery('tender_parser', broker='redis://redis:6379/0')
# ... ваш код с app = Celery ...

app.conf.beat_schedule = {
    'parse-eis-every-hour': {
        'task': 'tasks.run_eis_parser',
        'schedule': 3600.0,  # 3600 секунд = 1 час
    },
}
app.conf.timezone = 'Europe/Moscow' # Или ваш часовой пояс

KEYWORDS = ["охрана объекта", "физическая охрана", "пультовая охрана", "пожарная сигнализация"]
OKPD2_CODES = ["80.10", "80.20", "43.21"]


async def async_parse_eis():
    manager = BrowserManager(use_camoufox=True)
    parser = EisParser(manager)

    await manager.start()
    try:
        urls = await parser.search_tenders(keywords=KEYWORDS, okpd2_codes=OKPD2_CODES)

        # Открываем сессию к базе данных
        async with AsyncSessionLocal() as session:
            for url in urls:
                try:
                    # 1. Получаем данные и документы
                    tender_card = await parser.get_card(url)
                    await parser.download_docs(tender_card)

                    # 2. Сохраняем в PostgreSQL (Дедупликация внутри)
                    db_tender_id, is_existing = await save_or_update_tender(session, tender_card)

                    # 3. Маршрутизация событий в Redis
                    if not is_existing:
                        logger.info(f"Найден НОВЫЙ тендер {tender_card.tender_id}, отправляем в AI-агент.")

                        try:
                            # В Docker адрес будет redis://redis:6379/0, локально - 127.0.0.1
                            redis_url = os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0")
                            redis_client = aioredis.from_url(redis_url)

                            # Кладем реестровый номер тендера в нашу единую очередь
                            await redis_client.lpush("new_tenders", tender_card.tender_id)
                            await redis_client.close()

                            logger.info(
                                f"✅ Сигнал 'new_tenders' для {tender_card.tender_id} успешно отправлен в очередь!")
                        except Exception as e:
                            logger.error(f"Ошибка отправки сигнала в Redis: {e}")
                    else:
                        logger.info(f"Тендер {tender_card.tender_id} уже в базе, обновили статусы.")

                except Exception as e:
                    logger.error(f"Ошибка обработки тендера {url}: {e}")

    finally:
        await manager.stop()


@app.task
def run_eis_parser():
    # Создаем новый цикл событий для каждого выполнения задачи
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        # Запускаем нашу асинхронную функцию в этом цикле
        loop.run_until_complete(async_parse_eis())
    finally:
        # Корректно закрываем цикл, чтобы не было утечек памяти
        loop.close()