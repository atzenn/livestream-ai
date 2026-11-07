"""TTS engine tests (offline; mock only, no model download).

The VieNeu-PC backend is tested against a local mock MCP server
(stdlib http.server): the real PC server is never dialled in tests.
"""

import base64
import io
import json
import math
import socket
import struct
import sys
import tempfile
import threading
import unittest
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from livestream_ai.tts import (
    FallbackTTS,
    MockTTS,
    TTSError,
    VieNeuPC_TTS,
    build_tts,
)


def _sine_wav(sample_rate: int = 48000, seconds: float = 0.5) -> bytes:
    """Non-silent WAV (440 Hz sine) so tests can tell real audio
    apart from silence."""
    frames = int(sample_rate * seconds)
    samples = [
        int(12000 * math.sin(2 * math.pi * 440 * i / sample_rate))
        for i in range(frames)
    ]
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(struct.pack(f"<{frames}h", *samples))
    return buf.getvalue()


class _MockMCPHandler(BaseHTTPRequestHandler):
    """Minimal MCP streamable-HTTP server for tests.

    initialize -> capabilities; tools/call -> one audio content block
    (base64 sine WAV). `error_on_call` makes tools/call answer a
    JSON-RPC error instead. All requests are recorded on the server
    object for assertions.
    """

    protocol_version = "HTTP/1.1"
    audio_b64 = base64.b64encode(_sine_wav()).decode("ascii")
    error_on_call = False
    calls: list = []
    headers_seen: list = []

    def log_message(self, *args):
        pass

    def _send_json(self, payload, status=200, session=None):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        if session:
            self.send_header("mcp-session-id", session)
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)
        try:
            msg = json.loads(raw.decode("utf-8"))
        except Exception:
            self._send_json({"error": "bad json"}, status=400)
            return
        method = msg.get("method", "")
        self.calls.append(msg)
        self.headers_seen.append(dict(self.headers))
        if method == "initialize":
            self._send_json(
                {
                    "jsonrpc": "2.0",
                    "id": msg.get("id"),
                    "result": {
                        "protocolVersion": "2025-06-18",
                        "capabilities": {"tools": {}},
                        "serverInfo": {
                            "name": "mock-vieneu-mcp",
                            "version": "0.0.1",
                        },
                    },
                },
                session="mock-session-1",
            )
        elif method == "notifications/initialized":
            self.send_response(202)
            self.send_header("Content-Length", "0")
            self.end_headers()
        elif method == "tools/call":
            if self.error_on_call:
                self._send_json(
                    {
                        "jsonrpc": "2.0",
                        "id": msg.get("id"),
                        "error": {"code": -32602, "message": "bad voice"},
                    }
                )
            else:
                self._send_json(
                    {
                        "jsonrpc": "2.0",
                        "id": msg.get("id"),
                        "result": {
                            "content": [
                                {
                                    "type": "audio",
                                    "data": self.audio_b64,
                                    "mimeType": "audio/wav",
                                }
                            ],
                            "isError": False,
                        },
                    }
                )
        else:
            self._send_json(
                {
                    "jsonrpc": "2.0",
                    "id": msg.get("id"),
                    "error": {"code": -32601, "message": "no such method"},
                }
            )


class _MockMCPServer:
    """Runs the mock MCP server on an ephemeral localhost port."""

    def __init__(self, error_on_call=False):
        handler = type(
            "H", (_MockMCPHandler,), {"error_on_call": error_on_call}
        )
        handler.calls = []
        handler.headers_seen = []
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.port = self.httpd.server_address[1]
        self.handler = handler
        self.thread = threading.Thread(
            target=self.httpd.serve_forever, daemon=True
        )

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self.httpd.server_close()
        return False


def _closed_port() -> int:
    """A localhost port with nothing listening on it."""
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


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

    def test_pc_synthesizes_real_wav(self):
        with _MockMCPServer() as server, tempfile.TemporaryDirectory() as d:
            out = str(Path(d) / "seg-000.wav")
            tts = VieNeuPC_TTS(
                host="127.0.0.1", port=server.port, timeout_s=10
            )
            audio = tts.synthesize(
                "Chào mọi người, mình là avatar AI.", out
            )
            self.assertEqual(audio.engine, "vieneu-pc")
            self.assertEqual(audio.voice, "Hải Đăng")
            self.assertEqual(audio.sample_rate, 48000)
            self.assertGreater(audio.duration_s, 0)
            with wave.open(out, "rb") as w:
                self.assertEqual(w.getframerate(), 48000)
                frames = w.readframes(w.getnframes())
            # Real audio, not silence: RMS of the samples is above 0.
            ints = struct.unpack(f"<{len(frames) // 2}h", frames)
            rms = (sum(v * v for v in ints) / max(1, len(ints))) ** 0.5
            self.assertGreater(rms, 0)
            # The client ran the MCP handshake, then called the
            # ASSUMED tool with the ASSUMED argument names.
            methods = [c.get("method") for c in server.handler.calls]
            self.assertIn("initialize", methods)
            self.assertIn("tools/call", methods)
            call = next(
                c for c in server.handler.calls if c.get("method") == "tools/call"
            )
            self.assertEqual(call["params"]["name"], "synthesize")
            self.assertEqual(
                call["params"]["arguments"]["text"],
                "Chào mọi người, mình là avatar AI.",
            )
            self.assertEqual(call["params"]["arguments"]["voice"], "Hải Đăng")
            self.assertEqual(call["params"]["arguments"]["model"], "v3-turbo")
            # The session id from initialize is carried on later calls.
            call_headers = server.handler.headers_seen[-1]
            self.assertEqual(
                call_headers.get("Mcp-Session-Id"), "mock-session-1"
            )

    def test_pc_unreachable_raises(self):
        tts = VieNeuPC_TTS(
            host="127.0.0.1", port=_closed_port(), timeout_s=5
        )
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(TTSError):
                tts.synthesize("Câu thử.", str(Path(d) / "x.wav"))

    def test_pc_tool_error_raises(self):
        with _MockMCPServer(error_on_call=True) as server:
            tts = VieNeuPC_TTS(
                host="127.0.0.1", port=server.port, timeout_s=10
            )
            with tempfile.TemporaryDirectory() as d:
                with self.assertRaises(TTSError):
                    tts.synthesize("Câu thử.", str(Path(d) / "x.wav"))

    def test_pc_requires_host(self):
        with self.assertRaises(TTSError):
            VieNeuPC_TTS(host="")

    def test_fallback_uses_mock_when_pc_down(self):
        logs: list[str] = []
        pc = VieNeuPC_TTS(
            host="127.0.0.1", port=_closed_port(), timeout_s=5
        )
        tts = FallbackTTS(pc, MockTTS(), on_fallback=logs.append)
        with tempfile.TemporaryDirectory() as d:
            out = str(Path(d) / "seg-000.wav")
            audio = tts.synthesize("Câu thử.", out)
        self.assertEqual(audio.engine, "mock")
        self.assertEqual(len(logs), 1)
        self.assertIn("falling back", logs[0])

    def test_factory_vieneu_pc(self):
        tts = build_tts(
            "vieneu-pc", pc_mcp_host="127.0.0.1", piper_voice_path=""
        )
        self.assertIsInstance(tts, VieNeuPC_TTS)
        self.assertEqual(tts.port, 8790)
        self.assertEqual(tts.voice, "Hải Đăng")
        tts2 = build_tts(
            "vieneu-pc",
            pc_mcp_host="127.0.0.1",
            piper_voice_path="models/piper/x.onnx",
        )
        self.assertIsInstance(tts2, FallbackTTS)
        with self.assertRaises(TTSError):
            build_tts("vieneu-pc", pc_mcp_host="")


if __name__ == "__main__":
    unittest.main()
