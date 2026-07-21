import asyncio
import sys
import unittest
from pathlib import Path

from playwright.async_api import Error as PlaywrightError

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "parser"))

from core.base_parser import TenderCard
from platforms.eis_parser import EisParser, EisUnavailableError, parse_date, parse_money
from search_config import SearchProfile, load_search_profile, normalize_search_params


class FailingPage:
    def __init__(self):
        self.closed = False

    async def goto(self, *_args, **_kwargs):
        raise PlaywrightError("NS_ERROR_UNKNOWN_HOST")

    async def close(self):
        self.closed = True


class FailingBrowserManager:
    def __init__(self):
        self.page = FailingPage()

    async def get_page(self):
        return self.page


class BodyLocator:
    def __init__(self, body: str):
        self.body = body

    async def inner_text(self, **_kwargs):
        return self.body


class BodyPage:
    def __init__(self, body: str):
        self.body = body

    def locator(self, selector: str):
        assert selector == "body"
        return BodyLocator(self.body)


class EisParserTests(unittest.IsolatedAsyncioTestCase):
    def test_build_search_url_uses_lipetsk_active_application_filters(self):
        url = EisParser(None)._build_search_url("физическая охрана", 2)
        self.assertIn("searchString=%D1%84%D0%B8%D0%B7%D0%B8%D1%87%D0%B5%D1%81%D0%BA%D0%B0%D1%8F+%D0%BE%D1%85%D1%80%D0%B0%D0%BD%D0%B0", url)
        self.assertIn("pageNumber=2", url)
        self.assertIn("fz44=on", url)
        self.assertIn("fz223=on", url)
        self.assertIn("ppRf615=on", url)
        self.assertIn("af=off", url)
        self.assertIn("customerPlace=48000000000", url)
        self.assertIn("recordsPerPage=_10", url)
        self.assertNotIn("okpd2Ids", url)

    def test_security_profile_excludes_unrelated_maintenance(self):
        profile = load_search_profile()
        parser = EisParser(None, profile)
        self.assertEqual(profile.name, "security_services")
        self.assertTrue(parser._matches_profile_title("Монтаж систем видеонаблюдения"))
        self.assertFalse(parser._matches_profile_title("Техническое обслуживание кондиционеров"))

    def test_search_keywords_are_unique_without_okpd2(self):
        keywords = EisParser._search_requests(["охрана", "охрана", "пультовая охрана"])
        self.assertEqual(keywords, ["охрана", "пультовая охрана"])

    def test_document_validator_rejects_html(self):
        self.assertFalse(EisParser._is_document_content("documents.html", b"<html>not a file</html>", {"content-type": "text/html"}))
        self.assertTrue(EisParser._is_document_content("notice.pdf", b"%PDF-1.7", {"content-type": "application/pdf"}))

    def test_parse_money_handles_russian_format(self):
        self.assertEqual(parse_money("1 234 567,89 руб."), 1234567.89)

    def test_parse_date_extracts_only_the_date_and_never_uses_current_time(self):
        parsed = parse_date("Окончание подачи заявок 22.05.2027 12:00 (МСК). Дополнительная информация")
        self.assertEqual(parsed.isoformat(), "2027-05-22T12:00:00+00:00")
        self.assertIsNone(parse_date("дата на странице не указана"))

    def test_explicit_keywords_and_page_limit_are_preserved(self):
        keywords, max_pages = normalize_search_params(["охрана"], 1)
        self.assertEqual(keywords, ["охрана"])
        self.assertEqual(max_pages, 1)

    def test_target_filter_requires_open_application_and_minimum_price(self):
        tender = TenderCard(
            tender_id="1",
            title="Охрана",
            customer_name="Заказчик",
            platform="EIS",
            law_type="44-ФЗ",
            method="Аукцион",
            price=5000,
            deadline="2026-12-31T10:00:00Z",
            url="https://example.test",
            status="Подача заявок",
        )
        parser = EisParser(None, SearchProfile(
            name="test",
            customer_place="48000000000",
            min_price=5000,
            max_pages=1,
            keywords=("охрана",),
            required_title_terms=("охран",),
            excluded_title_terms=(),
        ))
        self.assertTrue(parser.is_target_tender(tender))
        self.assertFalse(parser.is_target_tender(tender.model_copy(update={"price": 4999.99})))
        self.assertFalse(parser.is_target_tender(tender.model_copy(update={"status": "Завершено"})))
        self.assertFalse(parser.is_target_tender(tender.model_copy(update={"deadline": None})))

    def test_document_url_may_end_with_html_when_it_has_file_id(self):
        self.assertTrue(EisParser._is_document_link("https://zakupki.gov.ru/documents.html?fileId=123"))
        self.assertTrue(EisParser._is_document_link("https://zakupki.gov.ru/filestore/public/file"))
        self.assertFalse(EisParser._is_document_link("https://zakupki.gov.ru/epz/order/notice/ea20/view/documents.html"))

    async def test_labeled_value_reads_only_the_neighboring_line(self):
        page = BodyPage(
            "Регион\nЛипецкая область\n"
            "Следующий раздел\n" + "Очень длинный текст " * 100
        )
        value = await EisParser(None)._extract_labeled_value(page, ["Регион"], max_length=255)
        self.assertEqual(value, "Липецкая область")

    async def test_dns_error_fails_the_task_instead_of_returning_empty_success(self):
        manager = FailingBrowserManager()
        parser = EisParser(manager)

        with self.assertRaises(EisUnavailableError):
            await parser.search_tenders(["охрана"], max_pages=1)

        self.assertTrue(manager.page.closed)


if __name__ == "__main__":
    unittest.main()
