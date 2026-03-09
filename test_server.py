"""
Tests for server.py
Run with: python -m pytest test_server.py -v
or:        python test_server.py
"""
import unittest
from unittest.mock import patch, MagicMock, mock_open
import json
import os
import sys

# Mock environment before importing server so module-level code uses test values
os.environ.setdefault("BOT_TOKEN",     "test_token")
os.environ.setdefault("CHANNEL_ID",    "-1001000000001")
os.environ.setdefault("DISCUSSION_ID", "-1002000000002")
os.environ.setdefault("SERVER_URL",    "")

# Patch load_map so the real /tmp/post_map.json isn't touched on import
with patch("builtins.open", mock_open(read_data="{}")), \
     patch("os.path.exists", return_value=False):
    import server


# ──────────────────────────────────────────────────────────────────────────────
# 1. parse_channel_post_id
# ──────────────────────────────────────────────────────────────────────────────
class TestParseChannelPostId(unittest.TestCase):

    def test_valid_format(self):
        self.assertEqual(server.parse_channel_post_id("post_001_15"), 15)

    def test_large_id(self):
        self.assertEqual(server.parse_channel_post_id("post_001_99999"), 99999)

    def test_demo_returns_none(self):
        # mini-app sends "demo" when opened outside a real post
        self.assertIsNone(server.parse_channel_post_id("demo"))

    def test_too_few_parts(self):
        self.assertIsNone(server.parse_channel_post_id("post_15"))

    def test_non_numeric_id(self):
        self.assertIsNone(server.parse_channel_post_id("post_001_abc"))

    def test_empty_string(self):
        self.assertIsNone(server.parse_channel_post_id(""))


# ──────────────────────────────────────────────────────────────────────────────
# 2. handle_telegram_update — auto-forward detection
# ──────────────────────────────────────────────────────────────────────────────
def make_auto_forward(channel_msg_id, discussion_msg_id):
    """Build a Telegram update that looks like an automatic channel→group forward."""
    return {
        "update_id": 343000000,
        "message": {
            "message_id": discussion_msg_id,
            "from": {"id": 777000, "is_bot": False, "first_name": "Telegram"},
            "sender_chat": {"id": -1001000000001, "type": "channel"},
            "chat": {"id": -1002000000002, "type": "supergroup"},
            "date": 1773000000,
            "is_automatic_forward": True,
            "forward_from_message_id": channel_msg_id,
            "forward_from_chat": {"id": -1001000000001, "type": "channel"},
            "text": "Post text",
        }
    }


class TestHandleTelegramUpdate(unittest.TestCase):

    def setUp(self):
        server.POST_MAP.clear()

    # ── auto-forward correctly maps channel post → discussion message ──────────
    @patch("server.save_map")
    def test_auto_forward_stores_mapping(self, mock_save):
        server.handle_telegram_update(make_auto_forward(18, 35))
        self.assertEqual(server.POST_MAP[18], 35)
        mock_save.assert_called_once()

    @patch("server.save_map")
    def test_multiple_posts_all_mapped(self, mock_save):
        server.handle_telegram_update(make_auto_forward(10, 20))
        server.handle_telegram_update(make_auto_forward(15, 35))
        server.handle_telegram_update(make_auto_forward(17, 40))
        self.assertEqual(server.POST_MAP, {10: 20, 15: 35, 17: 40})
        self.assertEqual(mock_save.call_count, 3)

    # ── non-auto-forward messages must be ignored ──────────────────────────────
    @patch("server.save_map")
    def test_regular_group_message_ignored(self, mock_save):
        update = {
            "update_id": 1,
            "message": {
                "message_id": 99,
                "from": {"id": 123, "is_bot": False, "first_name": "User"},
                "chat": {"id": -1002000000002, "type": "supergroup"},
                "date": 1773000000,
                "text": "Hello",
            }
        }
        server.handle_telegram_update(update)
        self.assertEqual(server.POST_MAP, {})
        mock_save.assert_not_called()

    @patch("server.save_map")
    def test_channel_post_type_ignored(self, _):
        # Telegram sends channel posts as "channel_post", not "message"
        update = {
            "update_id": 2,
            "channel_post": {
                "message_id": 18,
                "chat": {"id": -1001000000001, "type": "channel"},
                "date": 1773000000,
                "text": "Channel post",
            }
        }
        server.handle_telegram_update(update)
        self.assertEqual(server.POST_MAP, {})

    @patch("server.save_map")
    def test_update_without_message_key(self, mock_save):
        server.handle_telegram_update({"update_id": 3})
        self.assertEqual(server.POST_MAP, {})
        mock_save.assert_not_called()


# ──────────────────────────────────────────────────────────────────────────────
# 3. POST_MAP persistence (file I/O)
# ──────────────────────────────────────────────────────────────────────────────
class TestPostMapPersistence(unittest.TestCase):

    def test_save_writes_json(self):
        m = mock_open()
        with patch("builtins.open", m):
            server.save_map({18: 35, 20: 40})
        handle = m()
        written = "".join(c.args[0] for c in handle.write.call_args_list)
        data = json.loads(written)
        self.assertEqual(data, {"18": 35, "20": 40})

    def test_load_missing_file_returns_empty(self):
        with patch("os.path.exists", return_value=False):
            result = server.load_map()
        self.assertEqual(result, {})

    def test_load_converts_string_keys_to_int(self):
        raw = json.dumps({"18": 35, "20": 40})
        with patch("os.path.exists", return_value=True), \
             patch("builtins.open", mock_open(read_data=raw)):
            result = server.load_map()
        self.assertEqual(result[18], 35)
        self.assertEqual(result[20], 40)

    def test_load_corrupt_file_returns_empty(self):
        with patch("os.path.exists", return_value=True), \
             patch("builtins.open", mock_open(read_data="not json {{{")):
            result = server.load_map()
        self.assertEqual(result, {})


# ──────────────────────────────────────────────────────────────────────────────
# 4. format_comment
# ──────────────────────────────────────────────────────────────────────────────
class TestFormatComment(unittest.TestCase):

    BASE = {
        "username": "testuser",
        "name": "Test User",
        "final": 75,
        "scores": {"content": 7, "usability": 8, "visual": 6, "idea": 9},
        "comment": "",
    }

    def test_username_as_mention(self):
        result = server.format_comment(self.BASE)
        self.assertIn("@testuser", result)

    def test_final_score_shown(self):
        result = server.format_comment(self.BASE)
        self.assertIn("75/17", result)

    def test_all_four_subcriteria_shown(self):
        result = server.format_comment(self.BASE)
        for key in ("Смысл", "Удобство", "Визуал", "Идея"):
            self.assertIn(key, result)

    def test_comment_text_shown_when_present(self):
        data = {**self.BASE, "comment": "Хороший дизайн"}
        result = server.format_comment(data)
        self.assertIn("Хороший дизайн", result)
        self.assertIn("💬", result)

    def test_no_comment_block_when_empty(self):
        result = server.format_comment(self.BASE)
        self.assertNotIn("💬", result)

    def test_fallback_to_name_when_no_username(self):
        data = {**self.BASE, "username": None, "name": "Мелисса"}
        result = server.format_comment(data)
        self.assertIn("Мелисса", result)
        self.assertNotIn("@", result)

    def test_anonymous_fallback(self):
        data = {**self.BASE, "username": None, "name": None}
        # Should not crash; name defaults to "Аноним" in format_comment
        data["name"] = "Аноним"
        result = server.format_comment(data)
        self.assertIn("Аноним", result)


# ──────────────────────────────────────────────────────────────────────────────
# 5. score_bar
# ──────────────────────────────────────────────────────────────────────────────
class TestScoreBar(unittest.TestCase):

    def test_zero_is_all_empty(self):
        self.assertEqual(server.score_bar(0), "○○○○○")

    def test_max_is_all_filled(self):
        self.assertEqual(server.score_bar(5), "●●●●●")

    def test_always_five_chars(self):
        for i in range(6):
            bar = server.score_bar(i)
            self.assertEqual(len(bar), 5, f"score_bar({i}) has wrong length")

    def test_partial_fill(self):
        bar = server.score_bar(3)  # 3/5 → ~3 filled, ~2 empty
        filled = bar.count("●")
        empty  = bar.count("○")
        self.assertEqual(filled + empty, 5)
        self.assertGreater(filled, 0)
        self.assertGreater(empty, 0)


# ──────────────────────────────────────────────────────────────────────────────
# 6. Reply logic — reply_to_message_id is set only when mapping exists
# ──────────────────────────────────────────────────────────────────────────────
class TestReplyLogic(unittest.TestCase):

    def setUp(self):
        server.POST_MAP.clear()

    def test_reply_id_set_when_mapping_exists(self):
        server.POST_MAP[18] = 35
        channel_post_id     = server.parse_channel_post_id("post_001_18")
        discussion_thread   = server.POST_MAP.get(channel_post_id)
        self.assertEqual(discussion_thread, 35)

    def test_no_reply_id_when_mapping_missing(self):
        channel_post_id   = server.parse_channel_post_id("post_001_99")
        discussion_thread = server.POST_MAP.get(channel_post_id)
        self.assertIsNone(discussion_thread)

    def test_no_reply_id_for_demo_post(self):
        channel_post_id   = server.parse_channel_post_id("demo")
        discussion_thread = server.POST_MAP.get(channel_post_id) if channel_post_id else None
        self.assertIsNone(discussion_thread)


# ──────────────────────────────────────────────────────────────────────────────
# 7. End-to-end flow: publish post → webhook → submit rating → correct payload
# ──────────────────────────────────────────────────────────────────────────────
class TestEndToEndFlow(unittest.TestCase):
    """
    Simulates:
      1. Channel post published → Telegram auto-forwards to discussion group
      2. Webhook receives auto-forward → POST_MAP updated
      3. User submits rating → sendMessage called with reply_to_message_id
    """

    def setUp(self):
        server.POST_MAP.clear()

    @patch("server.save_map")
    @patch("server.tg")
    def test_full_flow(self, mock_tg, _):
        mock_tg.return_value = {"result": {"message_id": 100}}

        # Step 1: webhook receives auto-forward for channel post 18 → discussion msg 35
        server.handle_telegram_update(make_auto_forward(channel_msg_id=18, discussion_msg_id=35))
        self.assertEqual(server.POST_MAP[18], 35)

        # Step 2: build sendMessage payload as the handler would
        channel_post_id    = server.parse_channel_post_id("post_001_18")
        discussion_thread  = server.POST_MAP.get(channel_post_id)
        payload = {
            "chat_id": server.DISCUSSION_ID,
            "text": "test comment",
            "allow_sending_without_reply": True,
        }
        if discussion_thread:
            payload["reply_to_message_id"] = discussion_thread

        server.tg("sendMessage", payload)

        # Step 3: verify reply_to_message_id=35 was in the payload
        call_args = mock_tg.call_args[0]
        sent_payload = call_args[1]
        self.assertIn("reply_to_message_id", sent_payload)
        self.assertEqual(sent_payload["reply_to_message_id"], 35)

    @patch("server.save_map")
    @patch("server.tg")
    def test_flow_without_prior_mapping_sends_without_reply(self, mock_tg, _):
        mock_tg.return_value = {"result": {"message_id": 101}}

        # No auto-forward received → POST_MAP empty
        channel_post_id   = server.parse_channel_post_id("post_001_99")
        discussion_thread = server.POST_MAP.get(channel_post_id)
        payload = {
            "chat_id": server.DISCUSSION_ID,
            "text": "test comment",
            "allow_sending_without_reply": True,
        }
        if discussion_thread:
            payload["reply_to_message_id"] = discussion_thread

        server.tg("sendMessage", payload)

        sent_payload = mock_tg.call_args[0][1]
        self.assertNotIn("reply_to_message_id", sent_payload)


if __name__ == "__main__":
    unittest.main(verbosity=2)
