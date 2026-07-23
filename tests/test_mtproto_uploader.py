import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "bot"))

import mtproto_uploader
from mtproto_uploader import MtprotoUploader


class MtprotoUploaderTests(unittest.IsolatedAsyncioTestCase):
    async def test_send_file_resolves_peer_and_uses_document_attribute(self):
        client = MagicMock()
        client.send_file = AsyncMock()
        uploader = MtprotoUploader(1, "hash", "token", "/tmp/tender-bot")

        with patch.object(uploader, "_client_or_connect", new=AsyncMock(return_value=client)):
            with patch.object(uploader, "_resolve_peer", new=AsyncMock(return_value="peer")):
                with patch.object(
                    mtproto_uploader,
                    "DocumentAttributeFilename",
                    side_effect=lambda name: ("filename", name),
                ):
                    await uploader.send_file(123, "/tmp/tender.docx", "tender.docx")

        client.send_file.assert_awaited_once()
        self.assertEqual(client.send_file.await_args.args[:2], ("peer", "/tmp/tender.docx"))
        self.assertTrue(client.send_file.await_args.kwargs["force_document"])
