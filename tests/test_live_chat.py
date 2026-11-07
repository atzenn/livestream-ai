"""Live chat classification / draft / queue tests (offline, no network)."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from livestream_ai.catalog import load_products
from livestream_ai.live_chat import (
    ApprovalQueue,
    ChatMessage,
    ChatPollState,
    classify,
    draft_reply,
    parse_chat_response,
)
from livestream_ai.youtube_live import YouTubeError, build_chat_insert_body

FIXTURES = Path(__file__).resolve().parent / "fixtures"


class TestParseAndClassify(unittest.TestCase):
    def setUp(self):
        self.products = load_products(FIXTURES / "products-test.yaml")
        with (FIXTURES / "chat-sample.json").open(encoding="utf-8") as f:
            self.resp = json.load(f)

    def test_parse_fixture(self):
        messages, token = parse_chat_response(self.resp)
        self.assertEqual(len(messages), 5)
        self.assertEqual(token, "TOKEN2")
        self.assertEqual(messages[0].author, "Lan Anh")
        self.assertEqual(messages[0].text, "Den ban hoc gia bao nhieu vay?")

    def test_parse_malformed_skipped(self):
        messages, _ = parse_chat_response({"items": [{"bad": 1}, None, 42]})
        self.assertEqual(messages, [])

    def test_classify_price(self):
        kind, product = classify("Den ban hoc gia bao nhieu?", self.products)
        self.assertEqual(kind, "price_question")
        self.assertEqual(product.id, "P01")

    def test_classify_buy(self):
        kind, product = classify(
            "Minh muon mua gia do dien thoai, link o dau?", self.products
        )
        self.assertEqual(kind, "buy_intent")
        self.assertEqual(product.id, "P02")

    def test_classify_greeting(self):
        kind, _ = classify("Chao ca nha", self.products)
        self.assertEqual(kind, "greeting")

    def test_classify_spam_link(self):
        kind, _ = classify("Vao https://spam.example kiem tien", self.products)
        self.assertEqual(kind, "spam")

    def test_classify_other(self):
        kind, _ = classify("Hom nay troi dep nhi", self.products)
        self.assertEqual(kind, "other")

    def test_draft_price_uses_catalog_only(self):
        msg = ChatMessage(id="m1", author="Lan Anh", text="Den ban hoc gia bao nhieu?")
        draft = draft_reply(msg, self.products)
        self.assertIsNotNone(draft)
        self.assertEqual(draft.kind, "price_question")
        self.assertIn("189.000", draft.text)
        self.assertIn("https://example.invalid/shop/den-ban-hoc-led", draft.text)
        self.assertTrue(draft.needs_approval)
        self.assertEqual(draft.product_id, "P01")

    def test_draft_buy_names_platform(self):
        msg = ChatMessage(
            id="m2", author="Minh Quan",
            text="Minh muon mua gia do dien thoai, link o dau?",
        )
        draft = draft_reply(msg, self.products)
        self.assertIsNotNone(draft)
        self.assertEqual(draft.kind, "buy_intent")
        self.assertIn("tren Lazada", draft.text)
        self.assertIn("99.000", draft.text)

    def test_draft_no_product_match_asks_for_name(self):
        msg = ChatMessage(id="m9", author="A", text="Cai nay gia bao nhieu?")
        draft = draft_reply(msg, self.products)
        self.assertIsNotNone(draft)
        self.assertTrue(draft.needs_approval)
        self.assertNotIn("189.000", draft.text)

    def test_draft_spam_and_other_none(self):
        self.assertIsNone(
            draft_reply(
                ChatMessage(id="m4", author="S", text="Vao https://spam.example"),
                self.products,
            )
        )
        self.assertIsNone(
            draft_reply(
                ChatMessage(id="m5", author="K", text="Hom nay troi dep nhi"),
                self.products,
            )
        )

    def test_draft_greeting_needs_no_approval(self):
        draft = draft_reply(
            ChatMessage(id="m3", author="Thu Ha", text="Chao ca nha"),
            self.products,
        )
        self.assertIsNotNone(draft)
        self.assertEqual(draft.kind, "greeting")
        self.assertFalse(draft.needs_approval)


class TestQueueAndDedup(unittest.TestCase):
    def test_queue_approve_reject(self):
        with tempfile.TemporaryDirectory() as d:
            q = ApprovalQueue(Path(d) / "q.jsonl")
            products = load_products(FIXTURES / "products-test.yaml")
            draft = draft_reply(
                ChatMessage(id="m1", author="Lan Anh", text="Den ban hoc gia bao nhieu?"),
                products,
            )
            entry = q.add(draft, "Den ban hoc gia bao nhieu?")
            self.assertEqual(entry["status"], "pending")
            self.assertEqual(len(q.pending()), 1)
            done = q.set_status("m1", "approved")
            self.assertEqual(done["status"], "approved")
            self.assertEqual(q.pending(), [])
            self.assertIsNone(q.set_status("missing", "approved"))

    def test_poll_dedup(self):
        state = ChatPollState()
        msgs, _ = parse_chat_response(
            {"items": [{"id": "a", "snippet": {"displayMessage": "hi"}}]}
        )
        self.assertEqual(len(state.fresh(msgs)), 1)
        self.assertEqual(len(state.fresh(msgs)), 0)


class TestChatInsertBody(unittest.TestCase):
    def test_insert_body(self):
        body = build_chat_insert_body("CHAT123", "Xin chao ca nha")
        self.assertEqual(body["snippet"]["liveChatId"], "CHAT123")
        self.assertEqual(
            body["snippet"]["textMessageDetails"]["messageText"], "Xin chao ca nha"
        )

    def test_insert_body_rejects_empty_and_long(self):
        with self.assertRaises(YouTubeError):
            build_chat_insert_body("CHAT123", "   ")
        with self.assertRaises(YouTubeError):
            build_chat_insert_body("CHAT123", "x" * 201)
        with self.assertRaises(YouTubeError):
            build_chat_insert_body("", "hi")


if __name__ == "__main__":
    unittest.main()
