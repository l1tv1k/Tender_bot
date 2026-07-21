import asyncio
import logging
import os

from playwright.async_api import async_playwright

logger = logging.getLogger(__name__)


class BrowserManager:
    """Единый Playwright Chromium-контекст для работы с ЕИС.

    Chromium повторяет рабочую конфигурацию scout.py и не использует внешний
    GeoIP-сервис при старте.
    """

    def __init__(self, proxy: str | None = None, headless: bool | None = None):
        self.proxy = proxy
        self.headless = (
            headless
            if headless is not None
            else os.getenv("EIS_HEADLESS", "true").strip().lower() not in {"0", "false", "no"}
        )
        self.playwright = None
        self.browser = None
        self.context = None

    async def start(self):
        if self.context:
            return

        logger.info("Запуск Playwright Chromium для ЕИС (headless=%s)", self.headless)
        self.playwright = await async_playwright().start()
        launch_args = {
            "headless": self.headless,
            "args": ["--disable-blink-features=AutomationControlled"],
        }
        executable_path = os.getenv("CHROMIUM_EXECUTABLE_PATH")
        if executable_path:
            launch_args["executable_path"] = executable_path
        if self.proxy:
            launch_args["proxy"] = {"server": self.proxy}

        self.browser = await self.playwright.chromium.launch(**launch_args)
        self.context = await self.browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            locale="ru-RU",
            viewport={"width": 1920, "height": 1080},
            ignore_https_errors=True,
        )

    async def stop(self):
        if self.context:
            await self.context.close()
        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()
        self.context = None
        self.browser = None
        self.playwright = None

    async def random_delay(self, min_sec: float = 1.0, max_sec: float = 2.0):
        await asyncio.sleep((min_sec + max_sec) / 2)

    async def get_page(self):
        if not self.context:
            await self.start()
        return await self.context.new_page()
