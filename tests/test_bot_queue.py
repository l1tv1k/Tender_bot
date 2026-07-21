import base64
import json
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "bot"))

from main import build_celery_message


class BotQueueTests(unittest.TestCase):
    def test_fallback_message_uses_celery_protocol_v2(self):
        task_id, raw_message = build_celery_message("tasks.run_eis_parser", {"debug": True, "max_pages": 1})
        message = json.loads(raw_message)
        body = json.loads(base64.b64decode(message["body"]))

        self.assertEqual(message["headers"]["task"], "tasks.run_eis_parser")
        self.assertEqual(message["headers"]["id"], task_id)
        self.assertEqual(message["properties"]["delivery_info"]["routing_key"], "celery")
        self.assertEqual(body[1], {"debug": True, "max_pages": 1})
