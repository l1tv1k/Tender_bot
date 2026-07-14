import asyncio
import asyncpg
import logging
import os
from pydantic import ValidationError
from dotenv import load_dotenv
import json
import redis.asyncio as aioredis
from smart_extractor import SmartDocxExtractor
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
    """Подключение к БД и создание таблицы для аналитики, если ее нет."""
    pool = await asyncpg.create_pool(
        user=DB_USER, password=DB_PASS, host=DB_HOST, database=DB_NAME
    )
    async with pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS tender_analysis (
                reestr_number VARCHAR(50) PRIMARY KEY,
                analysis_data JSONB NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
    return pool


async def process_tender(pool, tender_id: str, mistral: MistralClient, extractor: SmartDocxExtractor):
    logger.info(f"Начинаем анализ тендера: {tender_id}")

    # 1. Формируем прямой путь к файлам на диске.
    # Так как main.py лежит внутри папки ai_agent/, мы поднимаемся на уровень выше
    # (в корень проекта) и идем в папку data/tenders/...
    base_dir = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "data", "tenders", "eis", tender_id, "docs")
    )

    if not os.path.exists(base_dir):
        logger.error(f"Папка с документами не найдена: {base_dir}")
        return

    # Получаем список всех .docx файлов в этой папке
    docx_files = [os.path.join(base_dir, f) for f in os.listdir(base_dir) if f.endswith('.docx')]

    if not docx_files:
        logger.warning(f"В папке {base_dir} нет файлов .docx для анализа.")
        return

    # 2. Вытаскиваем "мясо" из документов с помощью нашего умного парсера
    full_text = ""
    for file_path in docx_files:
        logger.info(f"Извлекаем текст из: {file_path}")
        extracted = extractor.extract(file_path)
        full_text += extracted + "\n\n"

    if not full_text.strip():
        logger.error("Не удалось извлечь текст. Прерываем анализ.")
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

    try:
        logger.info("Отправляем запрос в Mistral API...")

        # 4. Вызываем API в режиме JSON
        response_text = await mistral.chat_completion(prompt, json_mode=True)

        # 5. Строгая валидация (Pydantic проверяет структуру)
        analysis_result = TenderAnalysisResult.model_validate_json(response_text)

        # 6. Сохраняем проверенный JSON в PostgreSQL
        # (оставляем этот шаг, так как результат нам в базе нужен)
        async with pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO tender_analysis (reestr_number, analysis_data, created_at)
                VALUES ($1, $2::jsonb, NOW())
                ON CONFLICT (reestr_number) DO UPDATE 
                SET analysis_data = EXCLUDED.analysis_data, created_at = NOW();
            """, tender_id, analysis_result.model_dump_json())

        logger.info(f"✅ Успех! Анализ тендера {tender_id} сохранен в БД.")
        logger.info(f"Сводка ИИ: {analysis_result.summary}")

    except ValidationError as e:
        logger.error(f"Ошибка валидации Pydantic (Mistral вернул кривой JSON): {e}")
    except Exception as e:
        logger.error(f"Сбой в процессе работы с ИИ: {e}")


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
    extractor = SmartDocxExtractor()

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
            tender_id_bytes = await redis_client.rpop("new_tenders")

            if tender_id_bytes:
                tender_id = tender_id_bytes.decode("utf-8")
                logger.info(f"Получена задача на анализ тендера: {tender_id}")

                # Запускаем наш конвейер анализа
                await process_tender(pool, tender_id, mistral, extractor)

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

