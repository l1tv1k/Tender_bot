import asyncio
import redis.asyncio as aioredis
from celery import Celery
from core.browser_manager import BrowserManager
from platforms.eis_parser import EisParser
from core.database import (
    AsyncSessionLocal,
    delete_tender_documents,
    get_expired_tenders_with_documents,
    get_tender_by_reestr,
    get_tenders_with_documents,
    save_or_update_tender,
    tender_has_documents,
)
from search_config import DEFAULT_KEYWORDS, normalize_search_params
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

KEYWORDS = DEFAULT_KEYWORDS


async def purge_expired_documents(session, parser: EisParser) -> int:
    """Освобождает диск после дедлайна, не удаляя тендер, статусы и ИИ-анализ."""
    expired_tenders = await get_expired_tenders_with_documents(session)
    removed = 0
    for tender in expired_tenders:
        try:
            parser.remove_local_documents(tender.reestr_number)
            await delete_tender_documents(session, tender.id)
            removed += 1
            logger.info("Удалена документация неактуального тендера %s", tender.reestr_number)
        except Exception as error:
            logger.error("Не удалось очистить документацию %s: %s", tender.reestr_number, error)
    return removed


async def purge_inactive_tender_documents(session, parser: EisParser, tender_id, reestr_number: str) -> None:
    parser.remove_local_documents(reestr_number)
    await delete_tender_documents(session, tender_id)
    logger.info("Удалена документация тендера %s: подача заявок больше не открыта", reestr_number)


async def reconcile_document_lifecycle(session, parser: EisParser) -> int:
    """Удаляет документы при отмене или завершении закупки до её дедлайна."""
    limit = int(os.getenv("EIS_STATUS_RECHECK_LIMIT", "100"))
    tenders = await get_tenders_with_documents(session, limit)
    removed = 0
    for stored_tender in tenders:
        try:
            current_card = await parser.get_card(stored_tender.source_url)
            await save_or_update_tender(session, current_card)
            if not parser.is_application_open(current_card.status):
                await purge_inactive_tender_documents(
                    session,
                    parser,
                    stored_tender.id,
                    stored_tender.reestr_number,
                )
                removed += 1
        except Exception as error:
            logger.warning("Не удалось сверить статус тендера %s: %s", stored_tender.reestr_number, error)
    return removed


async def async_parse_eis(
    keywords: list[str] | None = None,
    max_pages: int | None = None,
    debug: bool = False,
):
    """Запускает поиск ЕИС.

    В debug-режиме задача читает только публичную выдачу и печатает карточки
    в лог worker-а. Она не пишет в PostgreSQL и не публикует события для ИИ
    или Telegram.
    """
    manager = BrowserManager()
    parser = EisParser(manager)
    redis_client = None
    keywords, max_pages = normalize_search_params(keywords, max_pages)
    await manager.start()
    try:
        urls = await parser.search_tenders(
            keywords=keywords,
            max_pages=max_pages,
            debug=debug,
        )

        if debug:
            logger.info("[DEBUG EIS] Найдено уникальных ссылок: %s", len(urls))
            for index, url in enumerate(urls, start=1):
                try:
                    card = await parser.get_card(url)
                    logger.info("[DEBUG EIS] %s. %s", index, card.model_dump_json())
                except Exception as error:
                    logger.exception("[DEBUG EIS] Не удалось прочитать карточку %s: %s", url, error)
            return {"mode": "debug", "found": len(urls)}

        redis_url = os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0")
        redis_client = aioredis.from_url(redis_url)

        # Открываем сессию к базе данных
        async with AsyncSessionLocal() as session:
            await purge_expired_documents(session, parser)
            await reconcile_document_lifecycle(session, parser)
            seen_reestr_numbers = set()
            for url in urls:
                try:
                    # 1. Карточка нужна всегда: по ней обновляется статус площадки.
                    tender_card = await parser.get_card(url)
                    if tender_card.tender_id in seen_reestr_numbers:
                        logger.info("Тендер %s уже встречался в этой выдаче, пропускаем дубль.", tender_card.tender_id)
                        continue
                    seen_reestr_numbers.add(tender_card.tender_id)

                    existing_tender = await get_tender_by_reestr(session, tender_card.tender_id)
                    is_open = parser.is_application_open(tender_card.status)
                    if not parser.is_target_tender(tender_card):
                        if existing_tender:
                            db_tender_id, _, status_changed, deadline_changed = await save_or_update_tender(session, tender_card)
                            if not is_open:
                                await purge_inactive_tender_documents(session, parser, existing_tender.id, tender_card.tender_id)
                            elif status_changed or deadline_changed:
                                await redis_client.lpush("tender_updated", tender_card.tender_id)
                        logger.info(
                            "Тендер %s пропущен: статус=%r, НМЦК=%s (нужна подача заявок и НМЦК от %.0f).",
                            tender_card.tender_id, tender_card.status, tender_card.price, parser.profile.min_price,
                        )
                        continue

                    # Не перекачиваем документацию у уже сохранённого реестрового номера.
                    if existing_tender:
                        documents_present = await tender_has_documents(session, existing_tender.id)
                        if not documents_present:
                            logger.info("Повторно скачиваем документацию ранее найденного тендера %s.", tender_card.tender_id)
                            await parser.download_docs(tender_card)

                        db_tender_id, _, status_changed, deadline_changed = await save_or_update_tender(session, tender_card)
                        if tender_card.documents:
                            await redis_client.lpush("analysis_tenders", db_tender_id)
                        logger.info("Тендер %s уже в базе; документы найдены: %s.", tender_card.tender_id, bool(tender_card.documents))
                        if status_changed or deadline_changed:
                            await redis_client.lpush("tender_updated", tender_card.tender_id)
                        continue

                    # Только для нового тендера скачиваем документацию и затем сохраняем запись.
                    await parser.download_docs(tender_card)

                    # 2. В БД сохраняется уникальный реестровый номер.
                    db_tender_id, is_existing, status_changed, deadline_changed = await save_or_update_tender(session, tender_card)

                    # 3. Маршрутизация событий в Redis
                    if not is_existing:
                        logger.info(f"Найден НОВЫЙ тендер {tender_card.tender_id}, отправляем в AI-агент.")

                        try:
                            # Боту нужен реестровый номер для карточки, ИИ-агенту - UUID записи в БД.
                            await redis_client.lpush("new_tenders", tender_card.tender_id)
                            if tender_card.documents:
                                await redis_client.lpush("analysis_tenders", db_tender_id)
                            else:
                                logger.warning(
                                    "Тендер %s сохранён без документации; ИИ-анализ не ставим в очередь.",
                                    tender_card.tender_id,
                                )

                            logger.info(
                                f"✅ Сигналы для {tender_card.tender_id} успешно отправлены в Redis!")
                        except Exception as e:
                            logger.error(f"Ошибка отправки сигнала в Redis: {e}")
                    else:
                        logger.info(f"Тендер {tender_card.tender_id} уже в базе, обновили статусы.")
                        if status_changed or deadline_changed:
                            await redis_client.lpush("tender_updated", tender_card.tender_id)

                except Exception as e:
                    logger.error(f"Ошибка обработки тендера {url}: {e}")

    finally:
        if redis_client:
            try:
                await redis_client.close()
            except Exception:
                pass
        await manager.stop()


@app.task(name="tasks.run_eis_parser")
def run_eis_parser(
    keywords: list[str] | None = None,
    max_pages: int | None = None,
    debug: bool = False,
):
    # Создаем новый цикл событий для каждого выполнения задачи
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        # Запускаем нашу асинхронную функцию в этом цикле
        return loop.run_until_complete(
            async_parse_eis(keywords, max_pages, debug)
        )
    finally:
        # Корректно закрываем цикл, чтобы не было утечек памяти
        loop.close()
