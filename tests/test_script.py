"""Script parsing tests (offline)."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from livestream_ai.script import parse_script

HERE = Path(__file__).resolve().parent
FIX = HERE / "fixtures" / "script-sample.md"


class TestScript(unittest.TestCase):
    def test_parse_sample(self):
        script = parse_script(str(FIX))
        self.assertEqual(script.title, "Kịch bản mẫu")
        self.assertEqual(len(script), 3)
        self.assertEqual(script.segments[0].scene, "Live")
        self.assertIn("avatar AI", script.segments[0].text)
        # Markdown markers are stripped, text is kept.
        self.assertNotIn("**", script.segments[1].text)
        self.assertIn("299 nghìn", script.segments[1].text)
        # Scene switch on the third segment.
        self.assertEqual(script.segments[2].scene, "Human")
        # Pause directive is parsed, not spoken.
        self.assertEqual(script.segments[2].pause_before_s, 1.0)
        self.assertNotIn("pause", script.segments[2].text)

    def test_empty_file(self):
        import tempfile

        with tempfile.NamedTemporaryFile(
            "w", suffix=".md", encoding="utf-8", delete=False
        ) as f:
            f.write("")
            name = f.name
        script = parse_script(name)
        self.assertEqual(len(script), 0)


if __name__ == "__main__":
    unittest.main()
