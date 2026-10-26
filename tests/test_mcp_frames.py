"""Byte limits apply to actual JSON-RPC frames and escaped request IDs."""
from __future__ import annotations

import json
import subprocess
import sys
import textwrap
import unittest
from types import SimpleNamespace

from test_mcp_handshake import HAVE_MCP, VAULT, RawStdioServer, initialize
from whykit.mcp_server import ToolFailure, build_server

MAX_STDIO_REQUEST_BYTES = 1_048_576
MAX_RPC_FRAME_BYTES = 2_097_152


class RpcIdTests(unittest.TestCase):
    def test_exact_utf8_json_boundaries_and_scalar_types(self) -> None:
        from whykit.mcp_server import _validate_rpc_id
        for value in ("a" * 1022, "🙂" * 255, "\x00" * 170, 0, 10**1023, None):
            _validate_rpc_id({"id": value})
        for value in ("a" * 1023, "🙂" * 256, "\x00" * 171, "\ud800", 10**1024, False, [], {}):
            with self.subTest(kind=type(value).__name__), self.assertRaises(ToolFailure) as caught:
                _validate_rpc_id({"id": value})
            self.assertEqual(caught.exception.code, "invalid_request_id")


@unittest.skipUnless(HAVE_MCP, "install the optional whykit[mcp] extra")
class ErrorFrameTests(unittest.IsolatedAsyncioTestCase):
    async def test_tool_error_replacement_cannot_retain_oversized_metadata(self) -> None:
        from mcp.shared.exceptions import MCPError

        server = build_server(VAULT, watch_interval=0)

        async def oversized(ctx):
            return {"_meta": {"private-sentinel": "x" * MAX_RPC_FRAME_BYTES}, "resultType": "complete"}

        with self.assertRaises(MCPError) as caught:
            await server.middleware[-1](SimpleNamespace(request_id=1, method="tools/call"), oversized)
        self.assertEqual(caught.exception.error.data["error"]["code"], "response_too_large")

    async def test_oversized_sdk_error_data_is_replaced(self) -> None:
        from mcp.shared.exceptions import MCPError

        server = build_server(VAULT, watch_interval=0)

        async def fail(ctx):
            raise MCPError(code=-32603, message="private-sentinel", data={"text": "x" * MAX_RPC_FRAME_BYTES})

        with self.assertRaises(MCPError) as caught:
            await server.middleware[-1](SimpleNamespace(request_id=1, method="resources/read"), fail)
        error = caught.exception.error.model_dump(mode="json")
        self.assertEqual(error["data"]["error"]["code"], "response_too_large")
        self.assertNotIn("private-sentinel", json.dumps(error))


@unittest.skipUnless(HAVE_MCP, "install the optional whykit[mcp] extra")
class StdioFrameTests(unittest.TestCase):
    def setUp(self) -> None:
        self.server = RawStdioServer()
        self.addCleanup(self.server.shutdown)

    def raw(self, data: bytes) -> None:
        self.server.process.stdin.write(data)
        self.server.process.stdin.flush()

    def connect(self) -> None:
        self.server.send(initialize(1, "2025-11-25"))
        self.server.response(1)
        self.server.send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def test_oversized_unterminated_line_is_rejected_without_draining(self) -> None:
        self.raw(b"x" * (MAX_STDIO_REQUEST_BYTES + 1))
        reply = self.server.receive()
        self.assertIsNone(reply["id"])
        self.assertEqual(reply["error"]["data"]["error"]["code"], "request_too_large")
        self.assertEqual(self.server.process.wait(timeout=10), 0)

    def test_invalid_frames_and_large_ids_do_not_poison_following_requests(self) -> None:
        self.connect()
        for data, code in ((b'\xff\n', "invalid_argument"), (b'{"private-sentinel":\n', "invalid_argument"),
                           ((json.dumps({"jsonrpc": "2.0", "id": "private-sentinel" * 100, "method": "ping"}) + "\n").encode(), "invalid_request_id"),
                           ((json.dumps({"jsonrpc": "2.0", "id": 3, "method": ["private-sentinel"]}) + "\n").encode(), "invalid_argument")):
            self.raw(data)
            reply = self.server.receive()
            self.assertIsNone(reply["id"])
            self.assertEqual(reply["error"]["data"]["error"]["code"], code)
            self.assertNotIn("private-sentinel", json.dumps(reply))
        self.server.send({"jsonrpc": "2.0", "id": 2, "method": "ping"})
        self.assertIn("result", self.server.response(2))

    def test_unpaired_surrogate_id_is_rejected_without_ending_the_session(self) -> None:
        self.connect()
        self.raw(b'{"jsonrpc":"2.0","id":"\\ud800","method":"ping"}\n')
        reply = self.server.receive()
        self.assertIsNone(reply["id"])
        self.assertEqual(reply["error"]["data"]["error"]["code"], "invalid_request_id")
        self.server.send({"jsonrpc": "2.0", "id": 2, "method": "ping"})
        self.assertIn("result", self.server.response(2))

    def test_exact_input_limit_and_escaped_id_are_accepted(self) -> None:
        self.connect()
        message = {"jsonrpc": "2.0", "id": "🙂" * 255, "method": "ping", "params": {"padding": ""}}
        base = (json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n").encode()
        message["params"]["padding"] = " " * (MAX_STDIO_REQUEST_BYTES - len(base))
        encoded = (json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n").encode()
        self.assertEqual(len(encoded), MAX_STDIO_REQUEST_BYTES)
        self.raw(encoded)
        reply = self.server.receive()
        self.assertEqual(reply["id"], message["id"])
        self.assertIn("result", reply)
        self.assertLessEqual(len((json.dumps(reply, ensure_ascii=False, separators=(",", ":")) + "\n").encode()), MAX_RPC_FRAME_BYTES)


@unittest.skipUnless(HAVE_MCP, "install the optional whykit[mcp] extra")
class StdioOutputTests(unittest.TestCase):
    def test_serialized_boundary_and_buffered_stdout_isolation(self) -> None:
        code = textwrap.dedent('''
            import anyio
            from mcp.shared.message import SessionMessage
            from mcp_types import JSONRPCResponse
            from whykit.mcp_server import MAX_RPC_FRAME_BYTES, bounded_stdio_server

            async def main():
                async with bounded_stdio_server() as (_, output):
                    print("buffered-private-sentinel")
                    for identifier, excess in ((1, 0), (2, 1)):
                        message = JSONRPCResponse(jsonrpc="2.0", id=identifier, result={"text": "🙂\\x00"})
                        size = len(message.model_dump_json(by_alias=True, exclude_unset=True).encode("utf-8")) + 1
                        message.result["text"] += "x" * (MAX_RPC_FRAME_BYTES - size + excess)
                        await output.send(SessionMessage(message))
            anyio.run(main)
        ''')
        child = subprocess.run([sys.executable, "-c", code], input=b"", capture_output=True, timeout=15)
        self.assertEqual(child.returncode, 0, child.stderr.decode("utf-8", "replace"))
        lines = child.stdout.splitlines(keepends=True)
        self.assertEqual(len(lines), 2)
        self.assertEqual(len(lines[0]), MAX_RPC_FRAME_BYTES)
        self.assertEqual(json.loads(lines[0])["id"], 1)
        rejected = json.loads(lines[1])
        self.assertEqual(rejected["id"], 2)
        self.assertEqual(rejected["error"]["data"]["error"]["code"], "response_too_large")
        self.assertNotIn(b"buffered-private-sentinel", child.stdout)
        self.assertIn(b"buffered-private-sentinel", child.stderr)


if __name__ == "__main__":
    unittest.main()
