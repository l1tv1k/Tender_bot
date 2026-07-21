import os
import json
import hashlib
import aiofiles
import logging
import re
import shutil
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlencode, unquote, urljoin, urlparse
from playwright.async_api import Error as PlaywrightError
from core.base_parser import BaseTenderParser, TenderCard, DocumentMeta
from core.browser_manager import BrowserManager
from search_config import SearchProfile, load_search_profile

logger = logging.getLogger(__name__)

SEARCH_RECORDS_PER_PAGE = 10
DEFAULT_MAX_PAGES = 3
ALLOWED_DOCUMENT_SUFFIXES = {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".zip", ".rar", ".7z"}
DOCUMENT_QUERY_KEYS = {"attachment", "docid", "documentid", "download", "fileid", "file_id", "filename", "filepath"}
DOCUMENT_PATH_MARKERS = ("/download/", "/filestore/", "/attachment/", "/get-file", "/getfile")
EIS_PAGE_NAMES = ("common-info.html", "documents.html", "notice-documents.html", "printform.html")
MAX_REGION_LENGTH = 255
MAX_PLATFORM_STATUS_LENGTH = 100
MAX_CUSTOMER_INN_LENGTH = 20


class EisUnavailableError(RuntimeError):
    """ЕИС не открылась, поэтому задачу нельзя считать успешно выполненной."""


def clean_text(value: str | None) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def parse_money(value: str | None) -> float:
    cleaned = clean_text(value)
    cleaned = re.sub(r"[^\d,.\-]", "", cleaned).replace(" ", "")
    if not cleaned:
        return 0.0
    if "," in cleaned and "." in cleaned:
        cleaned = cleaned.replace(".", "").replace(",", ".")
    else:
        cleaned = cleaned.replace(",", ".")
    try:
        return float(cleaned)
    except ValueError:
        logger.warning("Не удалось разобрать НМЦК: %s", value)
        return 0.0


def parse_date(value: str | None) -> datetime | None:
    text = clean_text(value)
    match = re.search(r"\b(\d{2}\.\d{2}\.\d{4})(?:\s+(\d{2}:\d{2}(?::\d{2})?))?\b", text)
    if not match:
        if text:
            logger.warning("Не удалось разобрать дату подачи заявок: %s", text[:300])
        return None

    date_value = " ".join(part for part in match.groups() if part)
    for pattern in ("%d.%m.%Y %H:%M:%S", "%d.%m.%Y %H:%M", "%d.%m.%Y"):
        try:
            parsed = datetime.strptime(date_value, pattern)
            return parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    logger.warning("Не удалось разобрать дату подачи заявок: %s", text[:300])
    return None


def safe_filename(name: str) -> str:
    name = clean_text(name) or "document"
    return re.sub(r'[<>:"/\\|?*\x00-\x1F]', "_", name)[:180]


class EisParser(BaseTenderParser):
    def __init__(self, browser_manager: BrowserManager, profile: SearchProfile | None = None):
        super().__init__(browser_manager)
        self.platform_name = "EIS"
        self.base_url = "https://zakupki.gov.ru"
        self.profile = profile or load_search_profile()

    def _build_search_url(self, keyword: str, page_number: int = 1) -> str:
        """Строит выдачу ЕИС только для Липецкой области и активной подачи заявок."""
        params = {
            "searchString": keyword,
            "morphology": "on",
            "pageNumber": str(page_number),
            "sortDirection": "FALSE",
            "sortBy": "UPDATE_DATE",
            "orderStages": "AF",
            "fz44": "on",
            "fz223": "on",
            "ppRf615": "on",
            "af": "off",
            "customerPlace": self.profile.customer_place,
            "recordsPerPage": "_10",
        }
        query = urlencode(params)
        return f"{self.base_url}/epz/order/extendedsearch/results.html?{query}"

    @staticmethod
    def _search_requests(keywords: list[str]) -> list[str]:
        return list(dict.fromkeys(clean_text(item) for item in keywords if clean_text(item)))

    @staticmethod
    def is_application_open(status: str) -> bool:
        normalized = clean_text(status).lower()
        return "подач" in normalized and "заяв" in normalized

    def _matches_profile_title(self, title: str) -> bool:
        normalized = clean_text(title).casefold()
        if any(term in normalized for term in self.profile.excluded_title_terms):
            return False
        return any(term in normalized for term in self.profile.required_title_terms)

    def is_target_tender(self, tender: TenderCard) -> bool:
        return (
            tender.price >= self.profile.min_price
            and tender.deadline is not None
            and tender.deadline > datetime.now(timezone.utc)
            and self.is_application_open(tender.status)
            and self._matches_profile_title(tender.title)
        )

    async def _texts(self, page, selectors: list[str]) -> list[str]:
        for selector in selectors:
            try:
                locator = page.locator(selector)
                count = await locator.count()
                if count:
                    values = [clean_text(await locator.nth(i).inner_text(timeout=2000)) for i in range(count)]
                    return [value for value in values if value]
            except Exception:
                continue
        return []

    async def _first_text(self, page, selectors: list[str], default: str = "") -> str:
        values = await self._texts(page, selectors)
        return values[0] if values else default

    @staticmethod
    def _bounded_text(value: str | None, max_length: int) -> str:
        return clean_text(value)[:max_length]

    async def _extract_labeled_value(
        self,
        page,
        labels: list[str],
        max_length: int = 500,
    ) -> str:
        """Reads a single value next to an EIS label without flattening the whole page."""
        body = await page.locator("body").inner_text(timeout=10000)
        lines = [clean_text(line) for line in body.splitlines() if clean_text(line)]

        for index, line in enumerate(lines):
            normalized_line = line.rstrip(":").casefold()
            for label in labels:
                normalized_label = clean_text(label).casefold()
                if normalized_line == normalized_label:
                    if index + 1 < len(lines):
                        return self._bounded_text(lines[index + 1], max_length)
                    continue

                prefix = f"{normalized_label}:"
                if line.casefold().startswith(prefix):
                    return self._bounded_text(line[len(label) + 1:], max_length)
        return ""

    @staticmethod
    def _extract_inn(value: str) -> str | None:
        match = re.search(r"\b\d{10}(?:\d{2})?\b", value)
        return match.group(0)[:MAX_CUSTOMER_INN_LENGTH] if match else None

    def _extract_reg_number(self, tender_url: str) -> str:
        parsed = urlparse(tender_url)
        query = parse_qs(parsed.query)
        for key in ("regNumber", "noticeId", "purchaseNoticeNumber"):
            if query.get(key):
                return query[key][0]

        match = re.search(r"(?:regNumber=|/)(\d{8,})", tender_url)
        if match:
            return match.group(1)
        raise ValueError(f"Не удалось извлечь реестровый номер из URL: {tender_url}")

    async def _extract_result_links(self, page) -> list[str]:
        """
        Достаёт по одной основной ссылке из каждой карточки .registry-entry__form —
        логика повторяет то, что реально работало в старом BeautifulSoup-скрипте
        (сначала ссылка на /notice/ или /purchase/, если не нашли — ссылка,
        в тексте которой есть длинное число реестрового номера).
        """
        return await page.locator(".registry-entry__form").evaluate_all(
            r"""
            (cards) => cards.map((card) => {
                const anchors = Array.from(card.querySelectorAll('a[href]'));
                const safe = anchors.filter((a) => {
                    const href = a.getAttribute('href') || '';
                    return !/printForm|listModal|extSign/i.test(href);
                });
                let primary = safe.find((a) => /\/notice\/|\/purchase\//.test(a.getAttribute('href') || ''));
                if (!primary) {
                    primary = safe.find((a) => /\d{10,}/.test((a.innerText || a.textContent || '')));
                }
                return primary ? primary.getAttribute('href') : null;
            }).filter(Boolean)
            """
        )

    async def search_tenders(
        self,
        keywords: list[str],
        max_pages: int = DEFAULT_MAX_PAGES,
        debug: bool = False,
    ) -> list[str]:
        """Поиск по 44-ФЗ и 223-ФЗ через публичную выдачу ЕИС.

        Селектор карточек (.registry-entry__form) и постраничный обход
        перенесены из ранее рабочего скрипта — предыдущая версия вообще
        не листала страницы дальше первой и использовала непроверенные
        селекторы ссылок.
        """
        page = await self.browser_manager.get_page()
        found_urls: list[str] = []
        seen = set()
        empty_markers = ("ничего не найдено", "не найдено результатов", "по запросу не найдено", "изменить параметры поиска")

        navigation_errors: list[Exception] = []
        search_keywords = self._search_requests(keywords)
        if not search_keywords:
            logger.warning("Поиск ЕИС пропущен: не заданы ключевые слова.")
            await page.close()
            return []

        try:
            for keyword in search_keywords:
                for page_number in range(1, max(1, max_pages) + 1):
                    url = self._build_search_url(keyword, page_number)
                    logger.info(
                        "ЕИС поиск Липецк: keyword=%r страница=%s url=%s",
                        keyword, page_number, url,
                    )
                    try:
                        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
                    except PlaywrightError as error:
                        navigation_errors.append(error)
                        logger.error("ЕИС недоступна для %s: %s", url, error)
                        break
                    await self.browser_manager.random_delay(2, 5)

                    try:
                        await page.wait_for_selector(".registry-entry__form", timeout=6000)
                    except Exception:
                        content = (await page.content()).lower()
                        if any(marker in content for marker in empty_markers):
                            logger.info("По '%s' (стр. %s) результатов больше нет.", keyword, page_number)
                        else:
                            logger.warning(
                                "Карточки не появились на странице %s по '%s' — пропускаем эту пару "
                                "(возможна капча/блокировка, стоит проверить скриншотом)",
                                page_number, keyword,
                            )
                        break

                    hrefs = await self._extract_result_links(page)
                    if not hrefs:
                        logger.warning("ЕИС вернул карточки без ссылок: keyword=%r", keyword)
                        break

                    if debug:
                        logger.info(
                            "[DEBUG EIS] keyword=%r page=%s: карточек=%s, ссылок=%s",
                            keyword, page_number,
                            await page.locator(".registry-entry__form").count(), len(hrefs),
                        )

                    for href in hrefs:
                        absolute = urljoin(self.base_url, href)
                        common_url = re.sub(r"/view/[^/?]+\.html", "/view/common-info.html", absolute)
                        if common_url not in seen:
                            seen.add(common_url)
                            found_urls.append(common_url)

                    if page_number == 1:
                        has_paginator = await page.locator(
                            ".paging, .paginator, .paginator-block, a.page-link"
                        ).count() > 0
                        if not has_paginator:
                            break

        finally:
            await page.close()

        if navigation_errors and not found_urls:
            raise EisUnavailableError(
                "ЕИС недоступна: " + "; ".join(str(error) for error in navigation_errors[:3])
            )

        return found_urls

    async def get_card(self, tender_url: str) -> TenderCard:
        """Извлечение данных карточки."""
        page = await self.browser_manager.get_page()
        try:
            full_url = f"{self.base_url}{tender_url}" if not tender_url.startswith("http") else tender_url
            reestr_number = self._extract_reg_number(full_url)
            await page.goto(full_url, wait_until="domcontentloaded", timeout=60000)
            await self.browser_manager.random_delay()

            title = await self._first_text(page, [
                ".cardMainInfo__content",
                ".cardMainInfo .sectionMainInfo__header",
                "h1",
                "h2",
            ], default=f"Закупка {reestr_number}")
            price_text = await self._first_text(page, [
                ".price",
                ".cardMainInfo__content.cost",
                "span:has-text('₽')",
                "div:has-text('Начальная') + div",
            ])
            customer_name = await self._extract_labeled_value(page, ["Заказчик", "Организация, осуществляющая размещение"])
            customer_inn = self._extract_inn(await self._extract_labeled_value(page, ["ИНН"]))
            deadline_text = await self._extract_labeled_value(page, [
                "Дата и время окончания срока подачи заявок",
                "Окончание подачи заявок",
                "Дата окончания подачи заявок",
            ])
            region = await self._extract_labeled_value(page, ["Регион"], MAX_REGION_LENGTH)
            status = await self._first_text(page, [
                ".cardMainInfo__state",
                ".registry-entry__header-mid__title",
                "span:has-text('Подача заявок')",
                "span:has-text('Работа комиссии')",
                "span:has-text('Завершено')",
            ], default="Не определен")

            body_text = await page.locator("body").inner_text(timeout=10000)
            law_type = "223-ФЗ" if "223-ФЗ" in body_text or "223-ФЗ" in full_url else "44-ФЗ"

            return TenderCard(
                tender_id=reestr_number,
                title=title,
                customer_name=customer_name or "Не указан",
                customer_inn=customer_inn,
                platform=self.platform_name,
                law_type=law_type,
                method=await self._extract_labeled_value(page, ["Способ определения поставщика", "Способ закупки"]) or "Не указан",
                price=parse_money(price_text),
                deadline=parse_date(deadline_text),
                region=region or "Липецкая область",
                url=full_url,
                status=self._bounded_text(status, MAX_PLATFORM_STATUS_LENGTH) or "Не определен",
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

    @staticmethod
    def _url_from_link_value(value: str | None) -> str:
        raw = clean_text(value)
        if not raw:
            return ""
        if raw.casefold().startswith("javascript:"):
            match = re.search(r"['\"]([^'\"]+)['\"]", raw)
            return match.group(1) if match else ""
        return raw

    @staticmethod
    def _is_document_link(url: str) -> bool:
        parsed = urlparse(url)
        if parsed.scheme and parsed.scheme not in {"http", "https"}:
            return False

        path = parsed.path.casefold()
        query_keys = {key.casefold() for key in parse_qs(parsed.query)}
        if query_keys & DOCUMENT_QUERY_KEYS:
            return True
        if path.endswith(tuple(ALLOWED_DOCUMENT_SUFFIXES)):
            return True
        if any(marker in path for marker in DOCUMENT_PATH_MARKERS):
            return True
        return not path.endswith(EIS_PAGE_NAMES) and "download" in path

    async def _collect_document_links(self, page, tender: TenderCard) -> list[dict]:
        urls_to_visit = [tender.url]
        for replacement in ("documents.html", "notice-documents.html"):
            urls_to_visit.append(re.sub(r"/view/[^/?]+\.html", f"/view/{replacement}", tender.url))

        links = []
        seen = set()
        for url in dict.fromkeys(urls_to_visit):
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=60000)
                await self.browser_manager.random_delay(1, 2)
                found = await page.locator("a").evaluate_all(
                    """
                    (nodes) => nodes.map((node) => ({
                        text: (node.innerText || node.textContent || '').trim(),
                        values: [
                            node.getAttribute('href'),
                            node.getAttribute('data-href'),
                            node.getAttribute('data-url'),
                            node.getAttribute('data-download-url'),
                            node.getAttribute('onclick')
                        ].filter(Boolean)
                    }))
                    """
                )
            except Exception as e:
                logger.warning("Не удалось открыть страницу документов %s: %s", url, e)
                continue

            for item in found:
                text = clean_text(item.get("text"))
                for value in item.get("values", []):
                    href = self._url_from_link_value(value)
                    absolute_url = urljoin(self.base_url, href)
                    if not self._is_document_link(absolute_url) or absolute_url in seen:
                        continue
                    filename = safe_filename(text or os.path.basename(urlparse(absolute_url).path) or f"{tender.tender_id}.bin")
                    seen.add(absolute_url)
                    links.append({"url": absolute_url, "name": filename})

        logger.info("Для тендера %s найдено ссылок на документы: %s", tender.tender_id, len(links))

        return links

    def _filename_from_response(self, url: str, headers: dict, fallback: str) -> str:
        disposition = headers.get("content-disposition") or headers.get("Content-Disposition") or ""
        match = re.search(r"filename\*=UTF-8''([^;]+)", disposition, re.IGNORECASE)
        if match:
            return safe_filename(unquote(match.group(1)))
        match = re.search(r'filename="?([^";]+)"?', disposition, re.IGNORECASE)
        if match:
            return safe_filename(unquote(match.group(1)))
        basename = os.path.basename(urlparse(url).path)
        return safe_filename(unquote(basename) or fallback)

    @staticmethod
    def _is_html_response(content: bytes, headers: dict) -> bool:
        content_type = (headers.get("content-type") or headers.get("Content-Type") or "").lower()
        sample = content[:512].lstrip().lower()
        return "text/html" in content_type or "application/xhtml" in content_type or sample.startswith((b"<!doctype html", b"<html", b"<head"))

    @staticmethod
    def _is_document_content(filename: str, content: bytes, headers: dict) -> bool:
        if not content or EisParser._is_html_response(content, headers):
            return False
        suffix = os.path.splitext(filename.lower())[1]
        magic = content[:8]
        known_binary = magic.startswith((b"%PDF-", b"PK\x03\x04", b"\xd0\xcf\x11\xe0", b"Rar!", b"7z\xbc\xaf"))
        content_type = (headers.get("content-type") or headers.get("Content-Type") or "").lower()
        return suffix in ALLOWED_DOCUMENT_SUFFIXES or known_binary or "application/pdf" in content_type

    async def _download_file(self, page, url: str, fallback_name: str, referer: str) -> tuple[str, bytes]:
        response = await page.context.request.get(
            url,
            timeout=120000,
            headers={"Referer": referer},
        )
        if not response.ok:
            raise RuntimeError(f"HTTP {response.status} при скачивании {url}")
        content = await response.body()
        filename = self._filename_from_response(url, response.headers, fallback_name)
        if not self._is_document_content(filename, content, response.headers):
            raise ValueError(f"Ответ не является документом: {filename}")
        return filename, content

    async def download_docs(self, tender: TenderCard, save_base_dir: str = "/data/tenders") -> None:
        page = await self.browser_manager.get_page()
        try:
            document_links = await self._collect_document_links(page, tender)
        finally:
            await page.close()

        if not document_links:
            logger.warning("Для тендера %s не найдены ссылки на документы.", tender.tender_id)
            return

        tender_dir = os.path.join(save_base_dir, self.platform_name.lower(), tender.tender_id)
        docs_dir = os.path.join(tender_dir, "docs")
        manifest_path = os.path.join(tender_dir, "manifest.json")
        manifest = {}
        manifest_loaded = False

        for file_info in document_links:
            original_name = safe_filename(file_info["name"])
            page = await self.browser_manager.get_page()
            try:
                original_name, content = await self._download_file(
                    page,
                    file_info["url"],
                    original_name,
                    tender.url,
                )
            except Exception as e:
                logger.warning("Не удалось скачать документ %s: %s", file_info["url"], e)
                continue
            finally:
                await page.close()

            if not manifest_loaded:
                os.makedirs(docs_dir, exist_ok=True)
                if os.path.exists(manifest_path):
                    with open(manifest_path, 'r', encoding='utf-8') as f:
                        manifest = json.load(f)
                manifest_loaded = True

            temp_path = os.path.join(tender_dir, f".download_{original_name}")
            async with aiofiles.open(temp_path, "wb") as f:
                await f.write(content)

            file_hash = await self._calculate_file_hash(temp_path)
            version_num = 1
            version_dir = docs_dir

            if original_name in manifest:
                if manifest[original_name]["file_hash"] != file_hash:
                    version_num = manifest[original_name]["version"] + 1
                    version_dir = os.path.join(docs_dir, f"v{version_num}")
                    os.makedirs(version_dir, exist_ok=True)
                else:
                    os.remove(temp_path)
                    continue

            final_path = os.path.join(version_dir, original_name)
            os.replace(temp_path, final_path)

            doc_meta = DocumentMeta(
                filename=original_name,
                original_url=file_info["url"],
                download_date=datetime.now(timezone.utc),
                file_hash=file_hash,
                version=version_num
            )
            tender.documents.append(doc_meta)
            manifest[original_name] = doc_meta.model_dump(mode='json')

        if manifest_loaded:
            async with aiofiles.open(manifest_path, 'w', encoding='utf-8') as f:
                await f.write(json.dumps(manifest, ensure_ascii=False, indent=4))

    def remove_local_documents(self, tender_id: str, save_base_dir: str = "/data/tenders") -> None:
        """Удаляет только каталог файлов тендера, не затрагивая запись закупки в БД."""
        if not re.fullmatch(r"[A-Za-z0-9_-]+", tender_id):
            raise ValueError(f"Некорректный реестровый номер для очистки: {tender_id}")
        tender_dir = os.path.join(save_base_dir, self.platform_name.lower(), tender_id)
        if os.path.isdir(tender_dir):
            shutil.rmtree(tender_dir)
