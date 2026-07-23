"""MTProto transport for uploads that cannot pass through the Bot API proxy."""

import asyncio
from pathlib import Path
from typing import BinaryIO

try:
    from telethon import TelegramClient
    from telethon.network.connection import ConnectionTcpObfuscated
    from telethon.tl.types import DocumentAttributeFilename
except ImportError:  # Allows the bot to start with an old image before rebuild.
    TelegramClient = None
    ConnectionTcpObfuscated = None
    DocumentAttributeFilename = None

from config import (
    BOT_TOKEN,
    MTPROTO_SESSION_PATH,
    MTPROTO_USE_OBFUSCATED,
    TELEGRAM_API_HASH,
    TELEGRAM_API_ID,
)


class MtprotoUploadError(RuntimeError):
    """An upload could not be completed through Telegram MTProto."""


class MtprotoUploader:
    def __init__(self, api_id: int, api_hash: str, bot_token: str, session_path: str):
        self.api_id = api_id
        self.api_hash = api_hash
        self.bot_token = bot_token
        self.session_path = session_path
        self._client = None
        self._lock = asyncio.Lock()

    async def _client_or_connect(self):
        if TelegramClient is None:
            raise MtprotoUploadError("Telethon не установлен. Пересоберите контейнер tg_bot.")
        if not self.api_id or not self.api_hash or not self.bot_token:
            raise MtprotoUploadError("MTProto не настроен: проверьте TELEGRAM_API_ID и TELEGRAM_API_HASH.")

        async with self._lock:
            if self._client is None:
                Path(self.session_path).parent.mkdir(parents=True, exist_ok=True)
                client_options = {
                    "session": self.session_path,
                    "api_id": self.api_id,
                    "api_hash": self.api_hash,
                    "connection_retries": 2,
                    "retry_delay": 2,
                    "request_retries": 2,
                    "timeout": 10,
                }
                if MTPROTO_USE_OBFUSCATED:
                    client_options["connection"] = ConnectionTcpObfuscated
                self._client = TelegramClient(**client_options)
                await self._client.start(bot_token=self.bot_token)
        return self._client

    async def connect(self) -> None:
        """Authorizes the bot early so configuration errors appear at startup."""
        client = await self._client_or_connect()
        await client.get_me()

    async def _resolve_peer(self, chat_id: int):
        client = await self._client_or_connect()
        try:
            return await client.get_input_entity(chat_id)
        except ValueError:
            # Bot API updates do not carry an MTProto access_hash. Dialog discovery
            # fills Telethon's local entity cache for users who have started the bot.
            await client.get_dialogs()
            try:
                return await client.get_input_entity(chat_id)
            except ValueError as error:
                raise MtprotoUploadError(
                    "Не удалось определить получателя MTProto. Откройте чат с ботом и отправьте /start."
                ) from error

    async def send_file(self, chat_id: int, source: str | BinaryIO, filename: str) -> None:
        client = await self._client_or_connect()
        peer = await self._resolve_peer(chat_id)
        try:
            await client.send_file(
                peer,
                source,
                force_document=True,
                attributes=[DocumentAttributeFilename(filename)],
            )
        except Exception as error:
            raise MtprotoUploadError(f"Ошибка MTProto при отправке файла: {error}") from error

    async def close(self) -> None:
        if self._client is not None:
            await self._client.disconnect()
            self._client = None


_uploader: MtprotoUploader | None = None


def get_mtproto_uploader() -> MtprotoUploader:
    global _uploader
    if _uploader is None:
        _uploader = MtprotoUploader(
            api_id=TELEGRAM_API_ID,
            api_hash=TELEGRAM_API_HASH,
            bot_token=BOT_TOKEN,
            session_path=MTPROTO_SESSION_PATH,
        )
    return _uploader


async def send_mtproto_file(chat_id: int, source: str | BinaryIO, filename: str) -> None:
    await get_mtproto_uploader().send_file(chat_id, source, filename)


async def connect_mtproto_uploader() -> None:
    await get_mtproto_uploader().connect()


async def close_mtproto_uploader() -> None:
    global _uploader
    if _uploader is not None:
        await _uploader.close()
        _uploader = None
