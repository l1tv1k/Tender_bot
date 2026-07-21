from abc import ABC, abstractmethod
from typing import List, Optional
from datetime import datetime
from pydantic import BaseModel, Field

# --- Структуры данных (Пункт 3.5 ТЗ) ---
class DocumentMeta(BaseModel):
    filename: str
    original_url: str
    download_date: datetime
    file_hash: str
    version: int = 1


class TenderCard(BaseModel):
    tender_id: str = Field(description="Номер извещения/реестровый номер")
    title: str = Field(description="Наименование закупки")
    customer_name: str
    customer_inn: Optional[str] = None
    platform: str = Field(description="Площадка, например, ЕИС")
    law_type: str = Field(description="44-ФЗ или 223-ФЗ")
    method: str = Field(description="Способ определения поставщика")
    price: float = Field(description="НМЦК")
    deadline: Optional[datetime] = Field(default=None, description="Дата и время окончания подачи заявок")

    # Исправленные поля: явно указываем default=None
    results_date: Optional[datetime] = Field(default=None, description="Дата подведения итогов")
    execution_period: Optional[str] = Field(default=None, description="Срок исполнения контракта")
    region: Optional[str] = Field(default=None, description="Регион поставки/оказания услуг")
    security_deposit: Optional[float] = Field(default=None, description="Размер обеспечения")

    url: str = Field(description="Ссылка на карточку тендера")
    status: str = Field(description="Статус закупки")
    documents: List[DocumentMeta] = Field(default_factory=list)


# --- Базовый класс парсера (Пункт 3.3 ТЗ) ---
class BaseTenderParser(ABC):
    def __init__(self, browser_manager):
        self.browser_manager = browser_manager
        self.platform_name = "Base"

    @abstractmethod
    async def search_tenders(self, keywords: List[str], okpd2_codes: List[str]) -> List[str]:
        """Возвращает список ID тендеров или ссылок на них."""
        pass

    @abstractmethod
    async def get_card(self, tender_url: str) -> TenderCard:
        """Собирает данные карточки тендера."""
        pass

    @abstractmethod
    async def download_docs(self, tender: TenderCard, save_base_dir: str = "/data/tenders") -> None:
        """Скачивает документы, проверяет хэши и обновляет manifest.json."""
        pass
