import base64
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "bot"))

from main import build_celery_message, send_document_to_user


class BotQueueTests(unittest.TestCase):
    def test_fallback_message_uses_celery_protocol_v2(self):
        task_id, raw_message = build_celery_message("tasks.run_eis_parser", {"debug": True, "max_pages": 1})
        message = json.loads(raw_message)
        body = json.loads(base64.b64decode(message["body"]))

        self.assertEqual(message["headers"]["task"], "tasks.run_eis_parser")
        self.assertEqual(message["headers"]["id"], task_id)
        self.assertEqual(message["properties"]["delivery_info"]["routing_key"], "celery")
        self.assertEqual(body[1], {"debug": True, "max_pages": 1})


class BotDocumentTests(unittest.IsolatedAsyncioTestCase):
    async def test_document_uses_mtproto_by_default(self):
        message = MagicMock()
        message.chat.id = 123
        message.answer_document = AsyncMock()
        mtproto_send = AsyncMock()

        with patch("main.mtproto_uploader_ready", True):
            with patch("main.send_mtproto_file", new=mtproto_send):
                await send_document_to_user(message, "/tmp/tender.docx", "tender.docx")

        mtproto_send.assert_awaited_once()
        self.assertEqual(mtproto_send.await_args.args[0], 123)
        self.assertEqual(mtproto_send.await_args.args[2], "tender.docx")
        message.answer_document.assert_not_awaited()

    async def test_document_falls_back_to_worker_when_mtproto_is_unavailable(self):
        message = MagicMock()
        message.chat.id = 123
        message.answer_document = AsyncMock()

        with patch("main.mtproto_uploader_ready", False):
            await send_document_to_user(message, "/tmp/tender.docx", "tender.docx")

        message.answer_document.assert_awaited_once()
