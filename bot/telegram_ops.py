"""Narrow, observable wrappers around Telegram API calls."""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import TypeVar

from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramServerError,
)

from config import TELEGRAM_EDIT_RETRY_ATTEMPTS, TELEGRAM_RETRY_BASE_DELAY


logger = logging.getLogger(__name__)
Result = TypeVar("Result")


def _error_text(error: Exception) -> str:
    return str(error).casefold()


def is_message_not_modified(error: TelegramBadRequest) -> bool:
    return "message is not modified" in _error_text(error)


def is_expired_callback(error: TelegramBadRequest) -> bool:
    text = _error_text(error)
    return "query is too old" in text or "query is invalid" in text


async def retry_idempotent(
    operation: Callable[[], Awaitable[Result]],
    operation_name: str,
    attempts: int = TELEGRAM_EDIT_RETRY_ATTEMPTS,
) -> Result:
    """Retries only operations whose duplicate execution is safe, such as edits."""
    for attempt in range(1, attempts + 1):
        try:
            return await operation()
        except TelegramRetryAfter as error:
            if attempt == attempts:
                raise
            delay = max(float(error.retry_after), TELEGRAM_RETRY_BASE_DELAY * (2 ** (attempt - 1)))
        except (TelegramNetworkError, TelegramServerError) as error:
            if attempt == attempts:
                raise
            delay = TELEGRAM_RETRY_BASE_DELAY * (2 ** (attempt - 1))

        logger.warning(
            "Telegram %s failed, retry %s/%s in %.1f sec",
            operation_name,
            attempt,
            attempts - 1,
            delay,
        )
        await asyncio.sleep(delay)

    raise RuntimeError(f"Telegram {operation_name} exhausted retry attempts")


async def answer_callback(callback, text: str | None = None, *, show_alert: bool = False) -> bool:
    """Answers a callback quickly and ignores only callbacks that Telegram already expired."""
    try:
        await retry_idempotent(
            lambda: callback.answer(text=text, show_alert=show_alert),
            "callback.answer",
            attempts=2,
        )
        return True
    except TelegramBadRequest as error:
        if is_expired_callback(error):
            logger.info("Callback is already expired: %s", error)
            return False
        logger.exception("Telegram rejected callback answer")
        raise
    except (TelegramNetworkError, TelegramServerError, TelegramRetryAfter) as error:
        # The action itself may still safely complete and update the visible card.
        logger.warning("Could not acknowledge callback after retries: %s", error)
        return False


async def edit_message(message, text: str, **kwargs) -> bool:
    """Edits a message with retry; a duplicate edit is an expected no-op."""
    try:
        await retry_idempotent(
            lambda: message.edit_text(text, **kwargs),
            "message.edit_text",
        )
        return True
    except TelegramBadRequest as error:
        if is_message_not_modified(error):
            logger.debug("Skipped unchanged Telegram message")
            return False
        logger.exception("Telegram rejected message edit")
        raise


async def edit_bot_message(bot, **kwargs) -> bool:
    """Same retry policy for edits addressed by chat and message IDs."""
    try:
        await retry_idempotent(
            lambda: bot.edit_message_text(**kwargs),
            "bot.edit_message_text",
        )
        return True
    except TelegramBadRequest as error:
        if is_message_not_modified(error):
            logger.debug("Skipped unchanged Telegram message")
            return False
        logger.exception("Telegram rejected message edit")
        raise
