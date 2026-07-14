import asyncio
import random
import logging
from playwright.async_api import async_playwright

logger = logging.getLogger(__name__)


class BrowserManager:
    def __init__(self, use_camoufox=True, proxy=None):
        self.use_camoufox = use_camoufox
        self.proxy = proxy
        self.playwright = None
        self.browser = None
        self.context = None

    async def start(self):
        """Запуск браузера (Пункт 3.2 ТЗ)."""

        # Пытаемся запустить Camoufox (Для VPS)
        if self.use_camoufox:
            try:
                from camoufox.async_api import AsyncCamoufox
                logger.info("Запуск через Camoufox...")
                launch_args = {"headless": True, "geoip": True, "ignore_https_errors": True}
                if self.proxy: launch_args["proxy"] = {"server": self.proxy}
                self.browser = await AsyncCamoufox(**launch_args)
                self.context = self.browser  # В Camoufox инстанс равен контексту
                return
            except Exception as e:
                logger.warning(f"Camoufox недоступен ({e}), откат на стандартный Firefox.")

        # Резервный запуск через стандартный Playwright (Для локальной разработки)
        self.playwright = await async_playwright().start()
        launch_args = {"headless": True, "args": ["--disable-blink-features=AutomationControlled"]}
        if self.proxy: launch_args["proxy"] = {"server": self.proxy}

        self.browser = await self.playwright.firefox.launch(**launch_args)
        self.context = await self.browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:120.0) Gecko/20100101 Firefox/120.0",
            viewport={"width": 1920, "height": 1080},
            ignore_https_errors=True  # Игнорируем сертификаты Минцифры
        )

    async def stop(self):
        if self.context and not self.use_camoufox:
            await self.context.close()
        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()

    async def random_delay(self, min_sec=1.5, max_sec=4.0):
        await asyncio.sleep(random.uniform(min_sec, max_sec))

    async def get_page(self):
        if not self.browser:
            await self.start()
        return await getattr(self.context, 'new_page', self.browser.new_page)()