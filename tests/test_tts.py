"""TTS engine tests (offline; mock only, no model download).

The VieNeu-PC backend is tested against a local mock MCP server
(stdlib http.server): the real PC server is never dialled in tests.
The mock implements the REAL interface (Leader tin 7485):
text_to_speech returns a JSON text block with the file path in the
VieNeu library on the PC, a browser listen link, the engine name
and a job id - never audio bytes. Long text may come back while
the server is still reading (status "processing"); the client
then polls get_speech_status until the job is done.
"""

import json
import socket
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

REMOTE_PATH = "C:\\VieNeu\\library\\seg-000.wav"
LISTEN_URL = "http://127.0.0.1:8790/listen/job-1"


def _done_payload() -> dict:
    return {
        "file_path": REMOTE_PATH,
        "listen_url": LISTEN_URL,
        "engine": "vieneu",
        "job_id": "job-1",
        "status": "completed",
        "duration_s": 2.6,
        "sample_rate": 48000,
    }


class _MockMCPHandler(BaseHTTPRequestHandler):
    """Minimal MCP streamable-HTTP server for tests.

    initialize -> capabilities; tools/call dispatches on the real
    tool names: text_to_speech, get_speech_status, list_voices.
    `error_on_call` makes tools/call answer a JSON-RPC error.
    `pending_first` makes the first text_to_speech answer
    "processing" with a job id and no file path, so the client must
    poll get_speech_status. `no_path` makes text_to_speech answer
    "completed" with no file path. All requests are recorded on
    the server object for assertions.
    """

    protocol_version = "HTTP/1.1"
    error_on_call = False
    pending_first = False
    no_path = False
    calls: list = []
    headers_seen: list = []
    tts_calls: int = 0

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

    def _send_result(self, msg, payload):
        self._send_json(
            {
                "jsonrpc": "2.0",
                "id": msg.get("id"),
                "result": {
                    "content": [
                        {"type": "text", "text": json.dumps(payload)}
                    ],
                    "isError": False,
                },
            }
        )

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
                return
            name = (msg.get("params") or {}).get("name")
            if name == "list_voices":
                self._send_result(
                    msg,
                    [
                        {"id": "local:Hải Đăng", "name": "Hải Đăng"},
                        {"id": "local:Minh Quân", "name": "Minh Quân"},
                    ],
                )
            elif name == "get_speech_status":
                self._send_result(msg, _done_payload())
            elif name == "text_to_speech":
                type(self).tts_calls += 1
                if self.no_path:
                    self._send_result(
                        msg,
                        {
                            "job_id": "job-9",
                            "status": "completed",
                            "engine": "vieneu",
                        },
                    )
                elif self.pending_first and type(self).tts_calls == 1:
                    self._send_result(
                        msg,
                        {
                            "job_id": "job-1",
                            "status": "processing",
                            "engine": "vieneu",
                        },
                    )
                else:
                    self._send_result(msg, _done_payload())
            else:
                self._send_json(
                    {
                        "jsonrpc": "2.0",
                        "id": msg.get("id"),
                        "error": {"code": -32601, "message": "no such tool"},
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

    def __init__(self, error_on_call=False, pending_first=False,
                 no_path=False):
        handler = type(
            "H",
            (_MockMCPHandler,),
            {
                "error_on_call": error_on_call,
                "pending_first": pending_first,
                "no_path": no_path,
            },
        )
        handler.calls = []
        handler.headers_seen = []
        handler.tts_calls = 0
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


def _tools_call(handler, name):
    return next(
        c
        for c in handler.calls
        if c.get("method") == "tools/call"
        and c.get("params", {}).get("name") == name
    )


class TestTTS(unittest.TestCase):
    def test_mock_writes_real_wav(self):
        with tempfile.TemporaryDirectory() as d:
            out = str(Path(d) / "seg-000.wav")
            tts = MockTTS(sample_rate=48000)
            audio = tts.synthesize(
                "Chào mọi người, mình là avatar AI.", out
            )
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

    def test_pc_returns_library_path_not_audio_bytes(self):
        with _MockMCPServer() as server, tempfile.TemporaryDirectory() as d:
            out = str(Path(d) / "seg-000.wav")
            tts = VieNeuPC_TTS(
                host="127.0.0.1", port=server.port, timeout_s=10
            )
            audio = tts.synthesize(
                "Chào mọi người, mình là avatar AI.", out
            )
            self.assertEqual(audio.engine, "vieneu-pc")
            self.assertEqual(audio.voice, "local:Hải Đăng")
            self.assertEqual(audio.sample_rate, 48000)
            self.assertEqual(audio.duration_s, 2.6)
            # The audio lives on the PC: path is the library path
            # and no local file is written at out_path.
            self.assertEqual(audio.path, REMOTE_PATH)
            self.assertEqual(audio.remote_path, REMOTE_PATH)
            self.assertEqual(audio.listen_url, LISTEN_URL)
            self.assertEqual(audio.job_id, "job-1")
            self.assertFalse(Path(out).exists())
            # The client ran the MCP handshake, then called the REAL
            # tool with the REAL argument names.
            methods = [c.get("method") for c in server.handler.calls]
            self.assertIn("initialize", methods)
            self.assertIn("tools/call", methods)
            call = _tools_call(server.handler, "text_to_speech")
            self.assertEqual(
                call["params"]["arguments"]["text"],
                "Chào mọi người, mình là avatar AI.",
            )
            self.assertEqual(
                call["params"]["arguments"]["voice"], "local:Hải Đăng"
            )
            self.assertEqual(
                call["params"]["arguments"]["file_name"], "seg-000.wav"
            )
            # No model argument: the real tool has none.
            self.assertNotIn("model", call["params"]["arguments"])
            # The session id from initialize is carried on later calls.
            call_headers = server.handler.headers_seen[-1]
            self.assertEqual(
                call_headers.get("Mcp-Session-Id"), "mock-session-1"
            )

    def test_pc_polls_status_until_done(self):
        with _MockMCPServer(pending_first=True) as server:
            tts = VieNeuPC_TTS(
                host="127.0.0.1",
                port=server.port,
                timeout_s=10,
                poll_interval_s=0.01,
            )
            with tempfile.TemporaryDirectory() as d:
                audio = tts.synthesize(
                    "Câu dài cần đọc lâu.",
                    str(Path(d) / "seg-000.wav"),
                )
            self.assertEqual(audio.path, REMOTE_PATH)
            self.assertEqual(audio.job_id, "job-1")
            self.assertEqual(audio.duration_s, 2.6)
            status_call = _tools_call(server.handler, "get_speech_status")
            self.assertEqual(
                status_call["params"]["arguments"]["job_id"], "job-1"
            )
            # text_to_speech ran once, then get_speech_status.
            tts_calls = [
                c
                for c in server.handler.calls
                if c.get("method") == "tools/call"
            ]
            self.assertEqual(
                [c["params"]["name"] for c in tts_calls],
                ["text_to_speech", "get_speech_status"],
            )

    def test_pc_omits_voice_when_empty(self):
        with _MockMCPServer() as server:
            tts = VieNeuPC_TTS(
                host="127.0.0.1",
                port=server.port,
                timeout_s=10,
                voice="",
            )
            with tempfile.TemporaryDirectory() as d:
                tts.synthesize("Câu thử.", str(Path(d) / "seg-000.wav"))
            call = _tools_call(server.handler, "text_to_speech")
            self.assertNotIn("voice", call["params"]["arguments"])

    def test_pc_voice_bare_name_gets_local_prefix(self):
        tts = VieNeuPC_TTS(host="127.0.0.1", voice="Hải Đăng")
        self.assertEqual(tts.voice, "local:Hải Đăng")
        tts2 = VieNeuPC_TTS(host="127.0.0.1", voice="local:Hải Đăng")
        self.assertEqual(tts2.voice, "local:Hải Đăng")
        tts3 = VieNeuPC_TTS(host="127.0.0.1", voice="")
        self.assertEqual(tts3.voice, "")

    def test_pc_list_voices(self):
        with _MockMCPServer() as server:
            tts = VieNeuPC_TTS(
                host="127.0.0.1", port=server.port, timeout_s=10
            )
            voices = tts.list_voices()
            self.assertEqual(
                voices, ["local:Hải Đăng", "local:Minh Quân"]
            )
            _tools_call(server.handler, "list_voices")

    def test_pc_missing_file_path_raises(self):
        with _MockMCPServer(no_path=True) as server:
            tts = VieNeuPC_TTS(
                host="127.0.0.1", port=server.port, timeout_s=10
            )
            with tempfile.TemporaryDirectory() as d:
                with self.assertRaises(TTSError):
                    tts.synthesize("Câu thử.", str(Path(d) / "x.wav"))

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
        self.assertEqual(tts.voice, "local:Hải Đăng")
        self.assertEqual(tts.tool, "text_to_speech")
        self.assertFalse(hasattr(tts, "model"))
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
