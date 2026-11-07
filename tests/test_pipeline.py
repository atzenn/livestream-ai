"""Pipeline end-to-end tests (offline; mock TTS + null avatar)."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from livestream_ai.avatar import NullAvatar
from livestream_ai.config import load_config
from livestream_ai.obs_client import FakeOBSClient
from livestream_ai.pipeline import PipelineError, run_script
from livestream_ai.tts import MockTTS

HERE = Path(__file__).resolve().parent
FIX = HERE / "fixtures"
GOOD_CONFIG = FIX / "config-good.yaml"
SCRIPT = FIX / "script-sample.md"
BLOCKED_SCRIPT = FIX / "script-blocked.md"


class TestPipeline(unittest.TestCase):
    def test_dry_run_end_to_end(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = load_config(GOOD_CONFIG)
            cfg.compliance.log_path = str(Path(d) / "spoken.jsonl")
            tts = MockTTS(sample_rate=48000)
            avatar = NullAvatar()
            obs = FakeOBSClient()
            report = run_script(
                cfg,
                str(SCRIPT),
                tts,
                avatar,
                obs,
                out_dir=str(Path(d) / "out"),
                dry_run=True,
            )
            self.assertEqual(len(report.segments), 3)
            self.assertEqual(len(tts.calls), 3)
            self.assertEqual(len(avatar.calls), 3)
            # Segment scene fields come from the script markers.
            self.assertEqual(report.segments[0].scene, "Live")
            self.assertEqual(report.segments[2].scene, "Human")
            # Spoken log has one line per segment.
            rows = [
                json.loads(line)
                for line in Path(cfg.compliance.log_path)
                .read_text(encoding="utf-8")
                .strip()
                .split("\n")
            ]
            self.assertEqual(len(rows), 3)
            self.assertEqual(rows[0]["segment"], 0)

    def test_blocked_line_stops_pipeline(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = load_config(GOOD_CONFIG)
            cfg.compliance.log_path = str(Path(d) / "spoken.jsonl")
            with self.assertRaises(PipelineError) as ctx:
                run_script(
                    cfg,
                    str(BLOCKED_SCRIPT),
                    MockTTS(),
                    NullAvatar(),
                    FakeOBSClient(),
                    out_dir=str(Path(d) / "out"),
                    dry_run=True,
                )
            self.assertIn("medical", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
