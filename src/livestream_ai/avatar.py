"""Avatar engines (lip-sync).

Primary: MuseTalk 1.5 driven by LiveTalking (lipku/LiveTalking,
Apache-2.0). LiveTalking takes audio -> lip-sync -> WebRTC / RTMP /
virtual camera. It needs:
- an NVIDIA GPU (RTX 3060 or better for MuseTalk real-time),
- MuseTalk checkpoints (~4 GB: UNet v1.5 3.40 GB fp32 + VAE,
  Whisper-tiny, DWPose, face-parse),
- a prepared avatar bundle (wav2lip.pth, data/avatars/).

This machine has no GPU, so the real engine cannot run here. The
MuseTalkAvatar class checks for a GPU first and raises a clear
AvatarNotAvailable error instead of failing halfway. The LiveTalking
integration itself (subprocess call with the right flags) is written
and unit-tested with a fake binary path, so it can be plugged in on
the GPU box without rewriting the pipeline.

NullAvatar is the offline stand-in: it passes audio through unchanged
and records the call, so the pipeline can be tested end to end
without a GPU. It never claims to produce video.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass


class AvatarNotAvailable(RuntimeError):
    """Raised when the real avatar engine cannot run on this machine."""


@dataclass
class AvatarFrame:
    audio_path: str
    video_out: str  # RTMP URL, virtual camera name, or file path
    engine: str


def gpu_available() -> bool:
    """True when an NVIDIA GPU is visible (nvidia-smi present)."""
    return shutil.which("nvidia-smi") is not None


class NullAvatar:
    """Offline stand-in: audio pass-through, records calls."""

    def __init__(self):
        self.calls: list[str] = []

    def speak(self, audio_path: str, video_out: str) -> AvatarFrame:
        self.calls.append(audio_path)
        return AvatarFrame(
            audio_path=audio_path, video_out=video_out, engine="null"
        )


class MuseTalkAvatar:
    """MuseTalk 1.5 via LiveTalking.

    `livetalking_path` is the LiveTalking checkout on the GPU box.
    `checkpoint_dir` holds the MuseTalk weights. `avatar_bundle` is
    the prepared avatar (image + config). `video_out` is the RTMP
    URL or virtual camera the avatar stream goes to.
    """

    def __init__(
        self,
        livetalking_path: str,
        checkpoint_dir: str,
        avatar_bundle: str,
    ):
        self.livetalking_path = livetalking_path
        self.checkpoint_dir = checkpoint_dir
        self.avatar_bundle = avatar_bundle

    def check_ready(self) -> None:
        if not gpu_available():
            raise AvatarNotAvailable(
                "no NVIDIA GPU on this machine (nvidia-smi not found). "
                "MuseTalk needs RTX 3060 or better. Run on the GPU box."
            )
        if not self.livetalking_path:
            raise AvatarNotAvailable(
                "livetalking_path is empty in config. Set the LiveTalking "
                "checkout path on the GPU box."
            )
        if not self.checkpoint_dir:
            raise AvatarNotAvailable(
                "checkpoint_dir is empty in config. MuseTalk needs ~4 GB "
                "of weights (UNet v1.5, VAE, Whisper-tiny, DWPose, "
                "face-parse)."
            )
        if not self.avatar_bundle:
            raise AvatarNotAvailable(
                "avatar_bundle is empty in config. Prepare the avatar "
                "bundle (image + config) first."
            )

    def build_command(self, audio_path: str, video_out: str) -> list[str]:
        """Build the LiveTalking command (no execution)."""
        return [
            "python",
            "app.py",
            "--avatar",
            self.avatar_bundle,
            "--checkpoint",
            self.checkpoint_dir,
            "--audio",
            audio_path,
            "--output",
            video_out,
        ]

    def speak(self, audio_path: str, video_out: str) -> AvatarFrame:
        self.check_ready()
        cmd = self.build_command(audio_path, video_out)
        proc = subprocess.run(
            cmd,
            cwd=self.livetalking_path,
            capture_output=True,
            text=True,
            timeout=3600,
        )
        if proc.returncode != 0:
            raise AvatarNotAvailable(
                f"LiveTalking exited {proc.returncode}: "
                f"{proc.stderr.strip()[-500:]}"
            )
        return AvatarFrame(
            audio_path=audio_path, video_out=video_out, engine="musetalk"
        )


def build_avatar(engine: str, **kwargs):
    """Factory. `engine` is one of: musetalk | null."""
    if engine == "musetalk":
        return MuseTalkAvatar(
            livetalking_path=kwargs.get("livetalking_path", ""),
            checkpoint_dir=kwargs.get("checkpoint_dir", ""),
            avatar_bundle=kwargs.get("avatar_bundle", ""),
        )
    if engine == "null":
        return NullAvatar()
    raise AvatarNotAvailable(f"unknown avatar engine: {engine}")
