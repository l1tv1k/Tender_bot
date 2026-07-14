import os
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import declarative_base
from sqlalchemy import Column, String, Numeric, DateTime, Date, ForeignKey, Integer
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy import select
from datetime import datetime, timezone
from core.base_parser import TenderCard

# Берем URL из переменных окружения, заменяем драйвер на асинхронный asyncpg
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://tender_user:tender_password@db:5432/tender_bot_db"
).replace("postgresql://", "postgresql+asyncpg://")

engine = create_async_engine(DATABASE_URL, echo=False)
AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

Base = declarative_base()


class TenderDB(Base):
    """Модель таблицы tenders"""
    __tablename__ = 'tenders'

    # UUID генерируется на стороне базы данных (init.sql)
    id = Column(UUID(as_uuid=True), primary_key=True, server_default="uuid_generate_v4()")
    reestr_number = Column(String(100), unique=True, nullable=False)
    platform = Column(String(100), nullable=False)
    law_type = Column(String(50), nullable=False)
    title = Column(String, nullable=False)
    customer_name = Column(String, nullable=False)
    customer_inn = Column(String(20))
    nmck = Column(Numeric(15, 2))
    submission_deadline = Column(DateTime(timezone=True))
    execution_start = Column(Date)
    execution_end = Column(Date)
    region = Column(String(255))
    status_on_platform = Column(String(100))
    source_url = Column(String)
    first_seen_at = Column(DateTime(timezone=True))
    last_updated_at = Column(DateTime(timezone=True))


class TenderDocumentDB(Base):
    """Модель таблицы tender_documents"""
    __tablename__ = 'tender_documents'

    id = Column(UUID(as_uuid=True), primary_key=True, server_default="uuid_generate_v4()")
    tender_id = Column(UUID(as_uuid=True), ForeignKey('tenders.id', ondelete='CASCADE'))
    file_name = Column(String, nullable=False)
    file_path = Column(String, nullable=False)
    file_hash = Column(String(64), nullable=False)
    version = Column(Integer, default=1)
    downloaded_at = Column(DateTime(timezone=True))





async def save_or_update_tender(session: AsyncSession, tender: TenderCard) -> tuple[str, bool]:
    """
    Сохраняет тендер и его документы.
    Возвращает (ID тендера в формате UUID, Флаг: был ли это уже существующий тендер).
    """
    # 1. Проверяем, существует ли тендер по реестровому номеру
    stmt = select(TenderDB).where(TenderDB.reestr_number == tender.tender_id)
    result = await session.execute(stmt)
    existing_tender = result.scalars().first()

    is_existing = existing_tender is not None

    if is_existing:
        # Обновляем только то, что могло измениться
        existing_tender.status_on_platform = tender.status
        existing_tender.submission_deadline = tender.deadline
        existing_tender.last_updated_at = datetime.now(timezone.utc)
        db_tender_id = existing_tender.id
    else:
        # Создаем новую запись
        new_tender = TenderDB(
            reestr_number=tender.tender_id,
            platform=tender.platform,
            law_type=tender.law_type,
            title=tender.title,
            customer_name=tender.customer_name,
            customer_inn=tender.customer_inn,
            nmck=tender.price,
            submission_deadline=tender.deadline,
            region=tender.region,
            status_on_platform=tender.status,
            source_url=tender.url
        )
        session.add(new_tender)
        await session.flush()  # Записываем, чтобы БД сгенерировала UUID
        db_tender_id = new_tender.id

    # 2. Сохраняем информацию о документах
    for doc in tender.documents:
        # Ищем документ по хэшу и привязке к тендеру, чтобы не дублировать
        doc_stmt = select(TenderDocumentDB).where(
            TenderDocumentDB.tender_id == db_tender_id,
            TenderDocumentDB.file_hash == doc.file_hash
        )
        doc_result = await session.execute(doc_stmt)

        if not doc_result.scalars().first():
            # Если такого файла еще нет — добавляем
            version_folder = f"v{doc.version}" if doc.version > 1 else ""
            file_path = f"/data/tenders/{tender.platform.lower()}/{tender.tender_id}/docs/{version_folder}/{doc.filename}"

            new_doc = TenderDocumentDB(
                tender_id=db_tender_id,
                file_name=doc.filename,
                file_path=file_path.replace("//", "/"),  # Защита от двойных слешей
                file_hash=doc.file_hash,
                version=doc.version,
                downloaded_at=doc.download_date
            )
            session.add(new_doc)

    await session.commit()

    # Возвращаем строковое представление UUID для Celery
    return str(db_tender_id), is_existing