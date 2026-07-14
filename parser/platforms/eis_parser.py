import os
import json
import hashlib
import aiofiles
import shutil
import logging
from datetime import datetime
from core.base_parser import BaseTenderParser, TenderCard, DocumentMeta
from core.browser_manager import BrowserManager

logger = logging.getLogger(__name__)


class EisParser(BaseTenderParser):
    def __init__(self, browser_manager: BrowserManager):
        super().__init__(browser_manager)
        self.platform_name = "EIS"
        self.base_url = "https://zakupki.gov.ru"

    async def search_tenders(self, keywords: list, okpd2_codes: list) -> list[str]:
        """Поиск по 44-ФЗ и 223-ФЗ."""
        page = await self.browser_manager.get_page()
        found_urls = []
        try:
            await page.goto(f"{self.base_url}/epz/order/extendedsearch/search.html")
            await self.browser_manager.random_delay(2, 5)

            # Внимание: селекторы ниже требуют проверки на актуальность через F12
            await page.fill('input[name="searchString"]', keywords[0])
            await page.click('.search-button')
            await self.browser_manager.random_delay(2, 5)

            # Эмуляция сбора ссылок
            tender_links = await page.locator(".registry-entry__header-mid__number a").all_get_attribute("href")
            found_urls = tender_links if tender_links else []

            # Для теста, если список пуст — подставляем заглушку
            if not found_urls:
                found_urls = ["/epz/order/notice/ea20/view/common-info.html?regNumber=00000000000"]

        except Exception as e:
            logger.error(f"Ошибка при поиске на ЕИС: {e}")
        finally:
            await page.close()

        return found_urls

    async def get_card(self, tender_url: str) -> TenderCard:
        """Извлечение данных карточки."""
        page = await self.browser_manager.get_page()
        try:
            full_url = f"{self.base_url}{tender_url}" if not tender_url.startswith("http") else tender_url
            await page.goto(full_url)
            await self.browser_manager.random_delay()

            # Извлекаем текст (замените селекторы на реальные после проверки в F12)
            title = await page.locator("h2.card-title").inner_text(timeout=5000)
            price_text = await page.locator(".price-amount").inner_text(timeout=5000)

            # Очистка цены (убираем пробелы и символы валют)
            clean_price = float(price_text.replace(" ", "").replace(",", ".").replace("₽", ""))

            return TenderCard(
                tender_id="00000000000",
                title=title,
                customer_name="ГБУ Пример",
                platform=self.platform_name,
                law_type="44-ФЗ",
                method="Электронный аукцион",
                price=clean_price,
                deadline=datetime.now(),
                url=full_url,
                status="Подача заявок"
            )
        except Exception as e:
            logger.error(f"Ошибка при парсинге карточки {tender_url}: {e}")
            raise e
        finally:
            await page.close()

    async def _calculate_file_hash(self, filepath: str) -> str:
        sha256 = hashlib.sha256()
        async with aiofiles.open(filepath, 'rb') as f:
            while chunk := await f.read(8192):
                sha256.update(chunk)
        return sha256.hexdigest()

    async def download_docs(self, tender: TenderCard, save_base_dir: str = "/data/tenders") -> None:
        tender_dir = os.path.join(save_base_dir, self.platform_name.lower(), tender.tender_id)
        docs_dir = os.path.join(tender_dir, "docs")
        os.makedirs(docs_dir, exist_ok=True)

        manifest_path = os.path.join(tender_dir, "manifest.json")
        manifest = {}
        if os.path.exists(manifest_path):
            with open(manifest_path, 'r', encoding='utf-8') as f:
                manifest = json.load(f)

        # Создаем фейковый документ
        dummy_path = "/tmp/TZ_ohrana.docx"
        async with aiofiles.open(dummy_path, 'w', encoding='utf-8') as dummy_file:
            await dummy_file.write("Тестовое техническое задание.")

        dummy_downloaded_files = [{"name": "TZ_ohrana.docx", "path": dummy_path, "url": "https://zakupki.gov.ru/..."}]

        for file_info in dummy_downloaded_files:
            file_hash = await self._calculate_file_hash(file_info["path"])
            original_name = file_info["name"]

            version_dir = docs_dir
            version_num = 1

            if original_name in manifest:
                if manifest[original_name]["file_hash"] != file_hash:
                    version_num = manifest[original_name]["version"] + 1
                    version_dir = os.path.join(docs_dir, f"v{version_num}")
                    os.makedirs(version_dir, exist_ok=True)
                else:
                    continue

            final_path = os.path.join(version_dir, original_name)
            shutil.move(file_info["path"], final_path)

            doc_meta = DocumentMeta(
                filename=original_name,
                original_url=file_info["url"],
                download_date=datetime.now(),
                file_hash=file_hash,
                version=version_num
            )
            tender.documents.append(doc_meta)
            manifest[original_name] = doc_meta.model_dump(mode='json')

        async with aiofiles.open(manifest_path, 'w', encoding='utf-8') as f:
            await f.write(json.dumps(manifest, ensure_ascii=False, indent=4))