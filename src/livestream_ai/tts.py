"""Text-to-speech engines.

Primary: VieNeu-TTS v3 Turbo (Apache-2.0, Vietnamese, 48 kHz, 23 preset
voices). CPU path is torch-free (ONNX Runtime). GPU path needs CUDA
and is auto-detected by the SDK. First audio ~140 ms int8 / ~300 ms
fp32 on CPU per the upstream README; the study says to measure it on
the real machine instead of trusting README numbers.

Fallback: Piper (offline, CPU, ONNX). Piper core is MIT (archived) /
piper1-gpl GPL-3.0; voice model licenses are per-voice. Vietnamese
voice vi_VN (VIVOS) is used when configured.

PC source: VieNeu-TTS v3 Turbo running on Zen's PC, reached through
an MCP server on port 8790 (model v3-turbo, voice "Hải Đăng"). The
Leader confirmed a 5.9 s sample voice was made on the PC on
03/11/2026. The access path from the livestream machine to Zen's PC
is arranged by the Leader; this client never dials the PC directly
in tests - it is tested against a local mock MCP server only.

ASSUMED MCP INTERFACE (read before the first real call): no document
in the workspace describes the PC server's tool names or argument
names, so the tool name ("synthesize") and the argument names
(text / voice / model) are ASSUMPTIONS. Only the MCP JSON-RPC
transport itself is standard: initialize, notifications/initialized,
tools/call over streamable HTTP (POST to the configured path, with
the mcp-session-id header carried after initialize). The Leader must
confirm the tool name and argument names before the first real call
with the PC. The client accepts the audio back in three shapes:
MCP audio content block, embedded resource blob, or base64 text.

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

import base64
import json
import math
import struct
import sys
import urllib.error
import urllib.request
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


class VieNeuPC_TTS:
    """VieNeu-TTS v3 Turbo on Zen's PC, via its MCP server (port 8790).

    Transport: MCP streamable HTTP (JSON-RPC 2.0 POST). Stdlib only.
    The endpoint comes from config/env, never hardcoded: `host` is the
    PC address as the Leader provides it, `port` defaults to 8790,
    `path` defaults to "/mcp". Pass a full `url` instead to override
    host/port/path (e.g. when the PC is reached through a tunnel).

    ASSUMED INTERFACE: the tool name ("synthesize") and the argument
    names (text / voice / model) are assumptions - no document in the
    workspace describes the PC server's tools. The Leader must confirm
    them before the first real call. The client only assumes the MCP
    transport itself (initialize / tools/call), which is the published
    MCP spec. Audio is accepted back as an MCP audio content block,
    an embedded resource blob, or base64 text.
    """

    # Tool name and argument names are ASSUMED (see class docstring).
    DEFAULT_TOOL = "synthesize"
    DEFAULT_MODEL = "v3-turbo"

    def __init__(
        self,
        host: str = "",
        port: int = 8790,
        path: str = "/mcp",
        tool: str = DEFAULT_TOOL,
        voice: str = "Hải Đăng",
        model: str = DEFAULT_MODEL,
        timeout_s: int = 60,
        url: str = "",
    ):
        if url:
            if not url.startswith(("http://", "https://")):
                raise TTSError(
                    f"VieNeu PC url must start with http:// or "
                    f"https://, got: {url}"
                )
            self._url = url
            self.host = host
            self.port = port
            self.path = path
        else:
            if not host:
                raise TTSError(
                    "VieNeu PC host is not set. Set tts.pc_server.host "
                    "in config.yaml (or LIVESTREAM_PC_HOST in env), "
                    "or give a full tts.pc_server.url."
                )
            self.host = host
            self.port = port
            self.path = path if path.startswith("/") else "/" + path
            self._url = f"http://{host}:{port}{self.path}"
        self.tool = tool
        self.voice = voice
        self.model = model
        self.timeout_s = timeout_s
        self._session_id = ""
        self._next_id = 1
        self._initialized = False

    def _post(self, payload: dict) -> dict:
        body = json.dumps(payload).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        req = urllib.request.Request(
            self._url, data=body, headers=headers, method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                raw = resp.read()
                session = resp.headers.get("mcp-session-id")
                if session:
                    self._session_id = session
                content_type = resp.headers.get("Content-Type", "")
        except urllib.error.HTTPError as exc:
            raise TTSError(
                f"PC MCP server HTTP {exc.code}: {exc.reason}"
            ) from exc
        except urllib.error.URLError as exc:
            raise TTSError(
                f"PC MCP server unreachable at {self._url}: {exc.reason}"
            ) from exc
        except TimeoutError as exc:
            raise TTSError(
                f"PC MCP server timed out after {self.timeout_s}s: "
                f"{self._url}"
            ) from exc
        text = raw.decode("utf-8", errors="replace")
        return self._parse_response(text, content_type)

    @staticmethod
    def _parse_response(text: str, content_type: str) -> dict:
        # Streamable HTTP servers may answer with SSE (data: {...}
        # lines); plain JSON is the other shape. Take the last
        # JSON-RPC message in either case.
        if "text/event-stream" in content_type:
            last = ""
            for line in text.splitlines():
                if line.startswith("data:"):
                    last = line[len("data:"):].strip()
            if not last:
                raise TTSError("PC MCP server sent an empty SSE stream")
            text = last
        try:
            msg = json.loads(text)
        except json.JSONDecodeError as exc:
            raise TTSError(
                f"PC MCP server sent non-JSON: {text[:120]}"
            ) from exc
        if not isinstance(msg, dict) or "jsonrpc" not in msg:
            raise TTSError(f"PC MCP server sent a bad message: {text[:120]}")
        if "error" in msg:
            err = msg["error"]
            raise TTSError(
                f"PC MCP server error {err.get('code')}: "
                f"{err.get('message')}"
            )
        if "result" not in msg:
            raise TTSError(f"PC MCP server sent no result: {text[:120]}")
        return msg["result"]

    def _rpc(self, method: str, params: dict | None = None) -> dict:
        payload: dict = {
            "jsonrpc": "2.0",
            "id": self._next_id,
            "method": method,
        }
        self._next_id += 1
        if params is not None:
            payload["params"] = params
        return self._post(payload)

    def _notify(self, method: str) -> None:
        payload = {"jsonrpc": "2.0", "method": method}
        body = json.dumps(payload).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        req = urllib.request.Request(
            self._url, data=body, headers=headers, method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s):
                pass
        except Exception:
            # A failed initialized notification is not fatal; the
            # tools/call below will fail loudly if the server is down.
            pass

    def _initialize(self) -> None:
        if self._initialized:
            return
        self._rpc(
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "livestream-ai", "version": "0.1.0"},
            },
        )
        self._notify("notifications/initialized")
        self._initialized = True

    @staticmethod
    def _extract_audio(result: dict) -> bytes:
        """Pull WAV bytes out of a tools/call result, in any of the
        three shapes the client accepts (audio block, resource blob,
        base64 text). Raises TTSError when no audio is found."""
        content = result.get("content")
        if not isinstance(content, list):
            raise TTSError("PC MCP tools/call returned no content blocks")
        for block in content:
            if not isinstance(block, dict):
                continue
            kind = block.get("type")
            if kind == "audio" and block.get("data"):
                return base64.b64decode(block["data"])
            if kind == "resource":
                resource = block.get("resource") or {}
                if resource.get("blob"):
                    return base64.b64decode(resource["blob"])
            if kind == "text" and block.get("text"):
                text = block["text"].strip()
                try:
                    raw = base64.b64decode(text, validate=True)
                except Exception:
                    continue
                if raw[:4] == b"RIFF":
                    return raw
        raise TTSError(
            "PC MCP tools/call returned no audio block "
            "(expected audio / resource blob / base64 WAV text)"
        )

    def synthesize(self, text: str, out_path: str) -> SynthesizedAudio:
        self._initialize()
        result = self._rpc(
            "tools/call",
            {
                "name": self.tool,
                "arguments": {
                    "text": text,
                    "voice": self.voice,
                    "model": self.model,
                },
            },
        )
        raw = self._extract_audio(result)
        if raw[:4] != b"RIFF":
            raise TTSError(
                "PC MCP server returned bytes that are not a WAV file"
            )
        with open(out_path, "wb") as f:
            f.write(raw)
        with wave.open(out_path, "rb") as w:
            sample_rate = w.getframerate()
            duration = w.getnframes() / float(sample_rate)
        return SynthesizedAudio(
            path=out_path,
            sample_rate=sample_rate,
            duration_s=duration,
            engine="vieneu-pc",
            voice=self.voice,
        )


# Canonical name for this engine (Leader tin 7451 task). The
# underscore spelling stays as an alias for existing callers.
VieNeuPCServerTTS = VieNeuPC_TTS


class FallbackTTS:
    """Try engines in order; first success wins.

    Chain is PC server -> local VieNeu -> Piper (build_tts
    "fallback"), but any list works: FallbackTTS(a, b, c) or
    FallbackTTS([a, b, c]). Each failure is reported through
    on_fallback (default: stderr) and the next engine is tried.
    The returned SynthesizedAudio carries the engine that actually
    produced the audio. Only when every engine fails does
    synthesize raise TTSError, chaining the last failure.
    """

    def __init__(self, *engines, on_fallback=None, voice=""):
        if len(engines) == 1 and isinstance(engines[0], (list, tuple)):
            engines = tuple(engines[0])
        if not engines:
            raise TTSError(
                "fallback chain is empty: no TTS engine configured"
            )
        self.engines = list(engines)
        # First engine is the primary; second is the first fallback,
        # kept as attributes for callers that used the old 2-engine
        # form FallbackTTS(primary, fallback).
        self.primary = self.engines[0]
        self.fallback = (
            self.engines[1] if len(self.engines) > 1 else None
        )
        self.on_fallback = on_fallback or self._default_log
        self._voice = voice

    @staticmethod
    def _default_log(message: str) -> None:
        print(f"[tts] {message}", file=sys.stderr)

    @property
    def voice(self) -> str:
        if self._voice:
            return self._voice
        for engine in self.engines:
            v = getattr(engine, "voice", "")
            if v:
                return v
        return ""

    def synthesize(self, text: str, out_path: str) -> SynthesizedAudio:
        failures: list[str] = []
        for engine in self.engines:
            try:
                return engine.synthesize(text, out_path)
            except Exception as exc:
                failures.append(f"{type(engine).__name__}: {exc}")
                self.on_fallback(
                    f"TTS engine {type(engine).__name__} failed: "
                    f"{exc}; falling back to next engine"
                )
        raise TTSError(
            "all TTS engines failed: " + "; ".join(failures)
        )


def _pc_kwargs(kwargs: dict) -> dict:
    """Merge PC server settings from a `pc_server` dict (preferred)
    and the flat pc_mcp_* kwargs. Returns kwargs for VieNeuPC_TTS."""
    pc = kwargs.get("pc_server") or {}
    if not isinstance(pc, dict):
        raise TTSError("pc_server must be a mapping")
    return {
        "host": str(pc.get("host", kwargs.get("pc_mcp_host", ""))),
        "url": str(
            pc.get(
                "url",
                kwargs.get(
                    "pc_mcp_url", kwargs.get("pc_server_url", "")
                ),
            )
        ),
        "port": int(pc.get("port", kwargs.get("pc_mcp_port", 8790))),
        "path": str(pc.get("path", kwargs.get("pc_mcp_path", "/mcp"))),
        "tool": str(
            pc.get(
                "tool", kwargs.get("pc_mcp_tool", VieNeuPC_TTS.DEFAULT_TOOL)
            )
        ),
        "model": str(
            pc.get(
                "model",
                kwargs.get("pc_mcp_model", VieNeuPC_TTS.DEFAULT_MODEL),
            )
        ),
        "timeout_s": int(
            pc.get(
                "timeout_s",
                pc.get("timeout", kwargs.get("pc_mcp_timeout_s", 60)),
            )
        ),
        "voice": str(kwargs.get("voice", "Hải Đăng")),
    }


def _pc_enabled(kwargs: dict) -> bool:
    """True when the PC source is switched on, from a `pc_server`
    dict (preferred) or the flat pc_mcp_enabled kwarg."""
    pc = kwargs.get("pc_server") or {}
    if isinstance(pc, dict) and "enabled" in pc:
        return bool(pc["enabled"])
    return bool(kwargs.get("pc_mcp_enabled", False))


def build_tts(engine: str, **kwargs):
    """Factory. `engine` is one of: vieneu | vieneu-pc | piper |
    mock | fallback.

    vieneu-pc is the VieNeu-TTS server on Zen's PC (MCP, port 8790).
    When piper_voice_path is set, the PC client is wrapped with a
    Piper fallback; without it the PC client stands alone.
    fallback is the full chain PC server -> local VieNeu -> Piper
    (each step only when configured: PC only when enabled, Piper
    only when piper_voice_path is set). With a single engine the
    chain collapses to that engine.
    """
    if engine == "vieneu":
        return VieNeuTTS(
            voice=kwargs.get("voice", "Hải Đăng"),
            precision=kwargs.get("precision", "fp32"),
            sample_rate=int(kwargs.get("sample_rate", 48000)),
            onnx_dir=kwargs.get("onnx_dir", ""),
            codec_dir=kwargs.get("codec_dir", ""),
        )
    if engine == "vieneu-pc":
        pc = VieNeuPC_TTS(**_pc_kwargs(kwargs))
        piper_path = kwargs.get("piper_voice_path", "")
        if piper_path:
            return FallbackTTS(
                pc,
                PiperTTS(
                    voice_path=piper_path,
                    sample_rate=int(kwargs.get("sample_rate", 22050)),
                ),
                on_fallback=kwargs.get("on_fallback"),
                voice=kwargs.get("voice", "Hải Đăng"),
            )
        return pc
    if engine == "fallback":
        chain = []
        if _pc_enabled(kwargs):
            chain.append(VieNeuPC_TTS(**_pc_kwargs(kwargs)))
        chain.append(
            VieNeuTTS(
                voice=kwargs.get("voice", "Hải Đăng"),
                precision=kwargs.get("precision", "fp32"),
                sample_rate=int(kwargs.get("sample_rate", 48000)),
                onnx_dir=kwargs.get("onnx_dir", ""),
                codec_dir=kwargs.get("codec_dir", ""),
            )
        )
        piper_path = kwargs.get("piper_voice_path", "")
        if piper_path:
            chain.append(
                PiperTTS(
                    voice_path=piper_path,
                    sample_rate=int(kwargs.get("sample_rate", 22050)),
                )
            )
        if len(chain) == 1:
            return chain[0]
        return FallbackTTS(
            chain,
            on_fallback=kwargs.get("on_fallback"),
            voice=kwargs.get("voice", "Hải Đăng"),
        )
    if engine == "piper":
        return PiperTTS(
            voice_path=kwargs.get("piper_voice_path", ""),
            sample_rate=int(kwargs.get("sample_rate", 22050)),
        )
    if engine == "mock":
        return MockTTS(sample_rate=int(kwargs.get("sample_rate", 48000)))
    raise TTSError(f"unknown TTS engine: {engine}")
