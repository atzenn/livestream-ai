"""Compliance gate tests (offline)."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from livestream_ai.compliance import SpokenLog, check_text


class TestCompliance(unittest.TestCase):
    def test_clean_sales_talk_passes(self):
        r = check_text(
            "Sản phẩm này có chất liệu cotton thoáng mát, "
            "giá 299 nghìn đồng, đang giảm còn 249 nghìn đồng."
        )
        self.assertTrue(r.passed)
        self.assertEqual(r.violations, [])

    def test_medical_claim_blocked(self):
        r = check_text("Sản phẩm này chữa khỏi bệnh đau lưng.")
        self.assertFalse(r.passed)
        self.assertEqual(r.violations[0].rule, "medical")

    def test_before_after_blocked(self):
        r = check_text("Da trắng sau 7 ngày sử dụng.")
        self.assertFalse(r.passed)
        self.assertEqual(r.violations[0].rule, "before_after")

    def test_impersonation_blocked(self):
        r = check_text("Tôi là bác sĩ, khuyên các bạn dùng sản phẩm này.")
        self.assertFalse(r.passed)
        self.assertEqual(r.violations[0].rule, "impersonation")

    def test_rule_can_be_disabled(self):
        r = check_text(
            "Sản phẩm này chữa khỏi bệnh đau lưng.",
            block_medical=False,
        )
        self.assertTrue(r.passed)

    def test_spoken_log_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "spoken.jsonl"
            log = SpokenLog(path)
            log.record(0, "Chào mọi người.", "Minh Quan", "script")
            log.record(1, "Giá 299 nghìn.", "Minh Quan", "script")
            rows = log.read_all()
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]["text"], "Chào mọi người.")
            self.assertEqual(rows[1]["segment"], 1)
            # Append-only: file has one JSON object per line.
            lines = path.read_text(encoding="utf-8").strip().split("\n")
            self.assertEqual(len(lines), 2)
            json.loads(lines[0])


if __name__ == "__main__":
    unittest.main()
