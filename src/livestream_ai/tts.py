"""Text-to-speech engines.

Primary: VieNeu-TTS v3 Turbo (Apache-2.0, Vietnamese, 48 kHz, 23 preset
voices). CPU path is torch-free (ONNX Runtime). GPU path needs CUDA
and is auto-detected by the SDK. First audio ~140 ms int8 / ~300 ms
fp32 on CPU per the upstream README; the study says to measure it on
the real machine instead of trusting README numbers.

Fallback: Piper (offline, CPU, ONNX). Piper core is MIT (archived) /
piper1-gpl GPL-3.0; voice model licenses are per-voice. Vietnamese
voice vi_VN (VIVOS) is used when configured.

Mock: writes a silent WAV of plausible length (0.1 s per word,
minimum 0.5 s) so the pipeline can be tested offline without any
model. The mock never pretends to be real speech: output files are
named *.mock.wav.

All engines share one interface: synthesize(text) -> SynthesizedAudio.
No engine contacts a paid service. Edge TTS (Microsoft Read Aloud,
no key) is intentionally NOT wired in: it is a consumer service with
no contract and can rate-limit, so it is not a reliable live path.
"""

from __future__ import annotations

import math
import struct
import wave
from dataclasses import dataclass
from pathlib import Path


class TTSError(RuntimeError):
    """Raised when synthesis fails."""


@dataclass
class SynthesizedAudio:
    path: str
    sample_rate: int
    duration_s: float
    engine: str
    voice: str


def _write_silent_wav(path: str, sample_rate: int, duration_s: float) -> None:
    frames = max(1, int(sample_rate * duration_s))
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(struct.pack(f"<{frames}h", *([0] * frames)))


class MockTTS:
    """Offline stand-in. Writes silent WAVs, records every call."""

    def __init__(self, sample_rate: int = 48000):
        self.sample_rate = sample_rate
        self.calls: list[str] = []

    def synthesize(self, text: str, out_path: str) -> SynthesizedAudio:
        self.calls.append(text)
        words = max(1, len(text.split()))
        duration = max(0.5, words * 0.1)
        _write_silent_wav(out_path, self.sample_rate, duration)
        return SynthesizedAudio(
            path=out_path,
            sample_rate=self.sample_rate,
            duration_s=duration,
            engine="mock",
            voice="mock",
        )


class VieNeuTTS:
    """VieNeu-TTS v3 Turbo via the `vieneu` SDK (lazy import).

    `onnx_dir` / `codec_dir` point at local model files (downloaded
    once with curl, because huggingface_hub stalls on this machine's
    egress proxy). When empty, the SDK downloads from Hugging Face.
    """

    def __init__(
        self,
        voice: str = "Hải Đăng",
        precision: str = "fp32",
        sample_rate: int = 48000,
        onnx_dir: str = "",
        codec_dir: str = "",
    ):
        self.voice = voice
        self.precision = precision
        self.sample_rate = sample_rate
        self.onnx_dir = onnx_dir
        self.codec_dir = codec_dir
        self._engine = None

    def _load(self):
        if self._engine is None:
            try:
                from vieneu import Vieneu
            except ImportError as exc:
                raise TTSError(
                    "vieneu SDK not installed. Run: pip install vieneu"
                ) from exc
            # First call downloads ~317 MB of model files from Hugging
            # Face (pnnbao-ump/VieNeu-TTS-v3-Turbo) into the HF cache,
            # unless onnx_dir/codec_dir point at local files.
            kwargs = {"precision": self.precision}
            if self.onnx_dir:
                kwargs["onnx_dir"] = self.onnx_dir
            if self.codec_dir:
                kwargs["codec_dir"] = self.codec_dir
            self._engine = Vieneu(**kwargs)
        return self._engine

    def list_voices(self) -> list[tuple[str, str]]:
        engine = self._load()
        return list(engine.list_preset_voices())

    def synthesize(self, text: str, out_path: str) -> SynthesizedAudio:
        engine = self._load()
        audio = engine.infer(text, voice=self.voice)
        engine.save(audio, out_path)
        duration = self._wav_duration(out_path)
        return SynthesizedAudio(
            path=out_path,
            sample_rate=self.sample_rate,
            duration_s=duration,
            engine="vieneu",
            voice=self.voice,
        )

    @staticmethod
    def _wav_duration(path: str) -> float:
        with wave.open(path, "rb") as w:
            return w.getnframes() / float(w.getframerate())


class PiperTTS:
    """Piper offline TTS (fallback). Voice model path comes from config."""

    def __init__(self, voice_path: str, sample_rate: int = 22050):
        self.voice_path = voice_path
        self.sample_rate = sample_rate
        self._voice = None

    def _load(self):
        if self._voice is None:
            try:
                from piper import PiperVoice
            except ImportError as exc:
                raise TTSError(
                    "piper-tts not installed. Run: pip install piper-tts"
                ) from exc
            if not Path(self.voice_path).is_file():
                raise TTSError(
                    f"piper voice model not found: {self.voice_path}"
                )
            self._voice = PiperVoice.load(self.voice_path)
        return self._voice

    def synthesize(self, text: str, out_path: str) -> SynthesizedAudio:
        import numpy as np

        voice = self._load()
        chunks = list(voice.synthesize(text))
        if not chunks:
            raise TTSError(f"piper produced no audio for: {text[:60]}")
        sample_rate = int(chunks[0].sample_rate)
        with wave.open(out_path, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(sample_rate)
            for chunk in chunks:
                audio = np.clip(chunk.audio_float_array, -1.0, 1.0)
                ints = (audio * 32767).astype(np.int16)
                w.writeframes(ints.tobytes())
        duration = VieNeuTTS._wav_duration(out_path)
        return SynthesizedAudio(
            path=out_path,
            sample_rate=sample_rate,
            duration_s=duration,
            engine="piper",
            voice=self.voice_path,
        )


def build_tts(engine: str, **kwargs):
    """Factory. `engine` is one of: vieneu | piper | mock."""
    if engine == "vieneu":
        return VieNeuTTS(
            voice=kwargs.get("voice", "Hải Đăng"),
            precision=kwargs.get("precision", "fp32"),
            sample_rate=int(kwargs.get("sample_rate", 48000)),
            onnx_dir=kwargs.get("onnx_dir", ""),
            codec_dir=kwargs.get("codec_dir", ""),
        )
    if engine == "piper":
        return PiperTTS(
            voice_path=kwargs.get("piper_voice_path", ""),
            sample_rate=int(kwargs.get("sample_rate", 22050)),
        )
    if engine == "mock":
        return MockTTS(sample_rate=int(kwargs.get("sample_rate", 48000)))
    raise TTSError(f"unknown TTS engine: {engine}")
