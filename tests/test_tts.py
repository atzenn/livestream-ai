"""TTS engine tests (offline; mock only, no model download)."""

import sys
import tempfile
import unittest
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from livestream_ai.tts import MockTTS, TTSError, build_tts


class TestTTS(unittest.TestCase):
    def test_mock_writes_real_wav(self):
        with tempfile.TemporaryDirectory() as d:
            out = str(Path(d) / "seg-000.wav")
            tts = MockTTS(sample_rate=48000)
            audio = tts.synthesize("Chào mọi người, mình là avatar AI.", out)
            self.assertEqual(audio.engine, "mock")
            self.assertEqual(audio.sample_rate, 48000)
            self.assertGreater(audio.duration_s, 0)
            with wave.open(out, "rb") as w:
                self.assertEqual(w.getframerate(), 48000)
                self.assertEqual(w.getnchannels(), 1)

    def test_mock_records_calls(self):
        tts = MockTTS()
        tts.synthesize("Câu một.", "/tmp/x.wav")
        tts.synthesize("Câu hai.", "/tmp/y.wav")
        self.assertEqual(tts.calls, ["Câu một.", "Câu hai."])

    def test_factory_unknown_engine(self):
        with self.assertRaises(TTSError):
            build_tts("bogus")

    def test_factory_mock(self):
        tts = build_tts("mock", sample_rate=24000)
        self.assertEqual(tts.sample_rate, 24000)


if __name__ == "__main__":
    unittest.main()
