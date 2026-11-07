"""Config loading tests (offline)."""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from livestream_ai.config import ConfigError, load_config

HERE = Path(__file__).resolve().parent
GOOD = HERE / "fixtures" / "config-good.yaml"
PC = HERE / "fixtures" / "config-pc.yaml"
BAD_ENGINE = HERE / "fixtures" / "config-bad-engine.yaml"


class TestConfig(unittest.TestCase):
    def test_load_good(self):
        cfg = load_config(GOOD)
        self.assertEqual(cfg.tts.engine, "vieneu")
        self.assertEqual(cfg.tts.voice, "Hải Đăng")
        self.assertEqual(cfg.tts.precision, "fp32")
        self.assertEqual(cfg.youtube.privacy_status, "unlisted")
        self.assertEqual(cfg.youtube.latency, "low")
        self.assertEqual(cfg.obs.port, 4455)
        self.assertEqual(
            cfg.compliance.disclosure_overlay,
            "Nội dung AI - Avatar AI đang nói",
        )

    def test_missing_file(self):
        with self.assertRaises(ConfigError):
            load_config(HERE / "fixtures" / "nope.yaml")

    def test_bad_engine(self):
        with self.assertRaises(ConfigError):
            load_config(BAD_ENGINE)

    def test_obs_password_from_env(self):
        import os

        os.environ["OBS_WS_PASSWORD"] = "secret-test"
        try:
            cfg = load_config(GOOD)
            self.assertEqual(cfg.obs_password, "secret-test")
        finally:
            del os.environ["OBS_WS_PASSWORD"]

    def test_load_pc_engine(self):
        cfg = load_config(PC)
        self.assertEqual(cfg.tts.engine, "vieneu-pc")
        self.assertTrue(cfg.tts.pc_mcp_enabled)
        self.assertEqual(cfg.tts.pc_mcp_host, "127.0.0.1")
        self.assertEqual(cfg.tts.pc_mcp_port, 8790)
        self.assertEqual(cfg.tts.pc_mcp_path, "/mcp")
        self.assertEqual(cfg.tts.pc_mcp_tool, "synthesize")
        self.assertEqual(cfg.tts.pc_mcp_model, "v3-turbo")
        self.assertEqual(cfg.tts.pc_mcp_timeout_s, 60)

    def test_pc_engine_needs_enabled(self):
        import yaml

        raw = yaml.safe_load(PC.read_text(encoding="utf-8"))
        raw["tts"]["pc_mcp_enabled"] = False
        with tempfile.NamedTemporaryFile(
            "w", suffix=".yaml", delete=False, encoding="utf-8"
        ) as f:
            yaml.safe_dump(raw, f, allow_unicode=True)
            path = f.name
        try:
            with self.assertRaises(ConfigError):
                load_config(path)
        finally:
            Path(path).unlink()

    def test_pc_enabled_needs_host(self):
        import yaml

        raw = yaml.safe_load(PC.read_text(encoding="utf-8"))
        raw["tts"]["pc_mcp_host"] = ""
        with tempfile.NamedTemporaryFile(
            "w", suffix=".yaml", delete=False, encoding="utf-8"
        ) as f:
            yaml.safe_dump(raw, f, allow_unicode=True)
            path = f.name
        try:
            with self.assertRaises(ConfigError):
                load_config(path)
        finally:
            Path(path).unlink()

    def test_pc_host_from_env(self):
        import os

        os.environ["LIVESTREAM_PC_HOST"] = "192.0.2.10"
        try:
            cfg = load_config(GOOD)
            self.assertEqual(cfg.tts.pc_mcp_host, "192.0.2.10")
        finally:
            del os.environ["LIVESTREAM_PC_HOST"]


if __name__ == "__main__":
    unittest.main()
