"""Avatar engine tests (offline; GPU-gated, no model download)."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from livestream_ai.avatar import (
    AvatarNotAvailable,
    MuseTalkAvatar,
    NullAvatar,
    build_avatar,
    gpu_available,
)


class TestAvatar(unittest.TestCase):
    def test_no_gpu_on_this_machine(self):
        # This VM has no NVIDIA GPU. The check must say so.
        self.assertFalse(gpu_available())

    def test_musetalk_refuses_without_gpu(self):
        av = MuseTalkAvatar("/tmp/livetalking", "/tmp/ckpt", "bundle")
        with self.assertRaises(AvatarNotAvailable) as ctx:
            av.speak("/tmp/a.wav", "rtmp://example.com/live")
        self.assertIn("no NVIDIA GPU", str(ctx.exception))

    def test_musetalk_command_shape(self):
        av = MuseTalkAvatar(
            "/data/livetalking", "/data/ckpt", "avatar-01"
        )
        cmd = av.build_command("/tmp/a.wav", "rtmp://yt/live/key")
        self.assertEqual(cmd[0], "python")
        self.assertIn("/tmp/a.wav", cmd)
        self.assertIn("rtmp://yt/live/key", cmd)
        self.assertIn("avatar-01", cmd)

    def test_null_avatar_records(self):
        av = NullAvatar()
        frame = av.speak("/tmp/a.wav", "rtmp://example.com/live")
        self.assertEqual(frame.engine, "null")
        self.assertEqual(av.calls, ["/tmp/a.wav"])

    def test_factory_unknown_engine(self):
        with self.assertRaises(AvatarNotAvailable):
            build_avatar("bogus")


if __name__ == "__main__":
    unittest.main()
