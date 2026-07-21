import asyncio
import asyncpg
import logging
import os
from pydantic import ValidationError
from dotenv import load_dotenv
import json
import redis.asyncio as aioredis
from document_extractor import TenderDocumentExtractor
from client import MistralClient
from models import TenderAnalysisResult

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)
load_dotenv()

# Настройки базы данных (в Docker берутся из переменных окружения)
DB_USER = os.getenv("DB_USER")
DB_PASS = os.getenv("DB_PASS")
DB_HOST = os.getenv("DB_HOST")
DB_NAME = os.getenv("DB_NAME")
MISTRAL_API_KEY = os.getenv("MISTRAL_API_KEY")

# Системный промпт задает жесткие правила для нейросети


async def init_db():
    """Подключение к БД. Схема создается только через db/init.sql."""
    return await asyncpg.create_pool(
        user=DB_USER, password=DB_PASS, host=DB_HOST, database=DB_NAME
    )


def resolve_document_path(file_path: str) -> str:
    """Поддерживает пути из БД как в Docker (/data), так и при локальном запуске."""
    if os.path.exists(file_path):
        return file_path

    if file_path.startswith("/data/tenders/"):
        docker_compose_path = file_path.replace("/data/tenders/", "/app/data/tenders/", 1)
        if os.path.exists(docker_compose_path):
            return docker_compose_path

        local_path = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", file_path.lstrip("/"))
        )
        if os.path.exists(local_path):
            return local_path

    return file_path


async def load_tender_context(pool, tender_db_id: str):
    async with pool.acquire() as conn:
        tender = await conn.fetchrow(
            """
            SELECT id, reestr_number, title, nmck, submission_deadline
            FROM tenders
            WHERE id = $1::uuid
            """,
            tender_db_id,
        )
        if not tender:
            return None, []

        documents = await conn.fetch(
            """
            SELECT file_name, file_path
            FROM tender_documents
            WHERE tender_id = $1::uuid
            ORDER BY version DESC, downloaded_at DESC
            """,
            tender_db_id,
        )
        return tender, documents


async def save_analysis(pool, tender_db_id: str, analysis_result: TenderAnalysisResult):
    payload = analysis_result.model_dump(mode="json")
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO tender_analysis (
                tender_id,
                labor,
                pricing,
                object_info,
                requirements,
                financial_terms,
                risks,
                summary,
                analysis_status,
                analyzed_at
            )
            VALUES (
                $1::uuid,
                $2::jsonb,
                $3::jsonb,
                $4::jsonb,
                $5::jsonb,
                $6::jsonb,
                $7::jsonb,
                $8,
                'completed',
                NOW()
            )
            ON CONFLICT (tender_id) DO UPDATE SET
                labor = EXCLUDED.labor,
                pricing = EXCLUDED.pricing,
                object_info = EXCLUDED.object_info,
                requirements = EXCLUDED.requirements,
                financial_terms = EXCLUDED.financial_terms,
                risks = EXCLUDED.risks,
                summary = EXCLUDED.summary,
                analysis_status = 'completed',
                analyzed_at = NOW();
            """,
            tender_db_id,
            json.dumps(payload["labor_costs"], ensure_ascii=False),
            json.dumps(payload["costs"], ensure_ascii=False),
            json.dumps(payload["protected_object"], ensure_ascii=False),
            json.dumps(payload["requirements"], ensure_ascii=False),
            json.dumps(payload["financials"], ensure_ascii=False),
            json.dumps(payload.get("risks"), ensure_ascii=False),
            payload["summary"],
        )


async def mark_analysis_failed(pool, tender_db_id: str, error_text: str):
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO tender_analysis (tender_id, analysis_status, summary, analyzed_at)
            VALUES ($1::uuid, 'failed', $2, NOW())
            ON CONFLICT (tender_id) DO UPDATE SET
                analysis_status = 'failed',
                summary = EXCLUDED.summary,
                analyzed_at = NOW();
            """,
            tender_db_id,
            error_text[:2000],
        )


async def process_tender(pool, redis_client, tender_db_id: str, mistral: MistralClient, extractor: TenderDocumentExtractor):
    logger.info(f"Начинаем анализ тендера: {tender_db_id}")

    tender, documents = await load_tender_context(pool, tender_db_id)
    if not tender:
        logger.error(f"Тендер с UUID {tender_db_id} не найден в БД.")
        return

    reestr_number = tender["reestr_number"]
    document_files = [
        resolve_document_path(row["file_path"])
        for row in documents
        if os.path.splitext(row["file_name"])[1].casefold() in TenderDocumentExtractor.supported_suffixes
    ]

    if not document_files:
        logger.warning(f"У тендера {reestr_number} нет поддерживаемых документов для анализа.")
        await mark_analysis_failed(pool, tender_db_id, "Нет поддерживаемых документов для анализа")
        return

    # 2. Вытаскиваем "мясо" из документов с помощью нашего умного парсера
    full_text = ""
    for file_path in document_files:
        if not os.path.exists(file_path):
            logger.warning(f"Документ из БД не найден на диске: {file_path}")
            continue

        logger.info(f"Извлекаем текст из: {file_path}")
        extracted = extractor.extract(file_path)
        full_text += extracted + "\n\n"

    if not full_text.strip():
        logger.error("Не удалось извлечь текст. Прерываем анализ.")
        await mark_analysis_failed(pool, tender_db_id, "Не удалось извлечь текст из документов")
        return

    # 3. Формируем запрос к ИИ
    # Динамически генерируем схему из нашей Pydantic-модели
    json_schema = json.dumps(TenderAnalysisResult.model_json_schema(), ensure_ascii=False, indent=2)

    dynamic_system_prompt = f"""Ты — старший тендерный аналитик. 
    Твоя задача — извлечь ключевые факты из Технического задания к госконтракту.
    Верни результат СТРОГО в формате JSON. Твой JSON должен идеально соответствовать этой схеме:

    {json_schema}

    Если информации по какому-то пункту нет в тексте, возвращай null. Не придумывай данные!
        """

    prompt = f"{dynamic_system_prompt}\n\nТекст Технического задания:\n{full_text}"

    last_validation_error = None
    for attempt in range(1, 4):
        try:
            logger.info("Отправляем запрос в Mistral API, попытка %s/3...", attempt)

            retry_suffix = ""
            if last_validation_error:
                retry_suffix = (
                    "\n\nПредыдущий ответ не прошел валидацию. "
                    "Исправь JSON строго по схеме. Ошибка валидатора:\n"
                    f"{last_validation_error}"
                )

            # 4. Вызываем API в режиме JSON
            response_text = await mistral.chat_completion(prompt + retry_suffix, json_mode=True)

            # 5. Строгая валидация (Pydantic проверяет структуру)
            analysis_result = TenderAnalysisResult.model_validate_json(response_text)

            # 6. Сохраняем проверенный результат в актуальную схему PostgreSQL.
            await save_analysis(pool, tender_db_id, analysis_result)
            await redis_client.lpush("ai_completed", reestr_number)

            logger.info(f"✅ Успех! Анализ тендера {reestr_number} сохранен в БД.")
            logger.info(f"Сводка ИИ: {analysis_result.summary}")
            return

        except ValidationError as e:
            last_validation_error = e
            logger.warning(f"Ошибка валидации Pydantic, попытка {attempt}/3: {e}")
        except Exception as e:
            logger.error(f"Сбой в процессе работы с ИИ: {e}")
            await mark_analysis_failed(pool, tender_db_id, f"Сбой анализа: {e}")
            return

    await mark_analysis_failed(pool, tender_db_id, f"Ошибка валидации ответа модели: {last_validation_error}")


async def main():
    logger.info("Запуск ИИ-агента в режиме ожидания задач...")

    # Инициализация клиентов
    pool = await init_db()

    # Не забудьте убедиться, что у вас MISTRAL_API_URL берется из .env,
    # чтобы запросы шли через ваш Cloudflare
    mistral = MistralClient(
        api_key=os.getenv("MISTRAL_API_KEY"),
        max_concurrent_requests=3
    )
    extractor = TenderDocumentExtractor()

    # Подключение к Redis (используем 127.0.0.1 для локальной работы на Windows)
    redis_url = os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0")

    # Пул соединений для надежности
    connection_pool = aioredis.ConnectionPool.from_url(
        redis_url, socket_timeout=60.0, socket_connect_timeout=10.0, health_check_interval=10
    )
    redis_client = aioredis.Redis(connection_pool=connection_pool)

    try:
        while True:
            # Неблокирующее чтение из очереди новых тендеров
            tender_id_bytes = await redis_client.rpop("analysis_tenders")

            if tender_id_bytes:
                tender_id = tender_id_bytes.decode("utf-8")
                logger.info(f"Получена задача на анализ тендера: {tender_id}")

                # Запускаем наш конвейер анализа
                await process_tender(pool, redis_client, tender_id, mistral, extractor)

            # Короткая пауза, чтобы не грузить процессор
            await asyncio.sleep(2.0)

    except asyncio.CancelledError:
        logger.info("ИИ-агент останавливается...")
    except Exception as e:
        logger.error(f"Критическая ошибка в главном цикле ИИ-агента: {e}")
    finally:
        await pool.close()
        await redis_client.close()


if __name__ == "__main__":
    # На Windows для asyncio лучше использовать стандартный цикл событий
    if os.name == 'nt':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    asyncio.run(main())
