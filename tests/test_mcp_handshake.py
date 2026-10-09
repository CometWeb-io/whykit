"""Protocol negotiation over stdio, driven by a raw JSON-RPC client.

No SDK client is involved: each test writes the exact frames a host sends, so
the server's behaviour is pinned independently of the client library.
Hosts in the field open a connection in one of three ways:

* the classic ``initialize`` handshake (protocol versions before 2026-07-28);
* a 2026-07-28 ``server/discover`` probe followed by enveloped requests;
* a ``server/discover`` probe that the host gave up on (it timed out while
  the server was still starting), followed by a classic ``initialize`` on the
  same pipe.

All three must connect. CI runs this file against the lowest and highest
``mcp`` versions that ``pyproject.toml`` allows.
"""
from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
VAULT = ROOT / "examples" / "tiny"
TOOLS = {"query", "context", "impact", "status", "pack", "trace", "backlinks"}
MODERN = "2026-07-28"
UNSUPPORTED_PROTOCOL_VERSION = -32022

try:
    import mcp  # noqa: F401
except ModuleNotFoundError as exc:  # The core matrix intentionally omits the extra.
    if exc.name != "mcp":
        raise
    HAVE_MCP = False
else:
    HAVE_MCP = True


def envelope() -> dict[str, Any]:
    return {
        "io.modelcontextprotocol/protocolVersion": MODERN,
        "io.modelcontextprotocol/clientCapabilities": {},
        "io.modelcontextprotocol/clientInfo": {"name": "raw-test-client", "version": "0"},
    }


def initialize(request_id: int, version: str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0", "id": request_id, "method": "initialize",
        "params": {
            "protocolVersion": version,
            "capabilities": {"roots": {"listChanged": True}},
            "clientInfo": {"name": "raw-test-client", "version": "0"},
        },
    }


def discover(request_id: int) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "method": "server/discover", "params": {"_meta": envelope()}}


class RawStdioServer:
    """``whykit-mcp`` in a child process, spoken to one JSON line at a time."""

    def __init__(self, root: Path = VAULT) -> None:
        env = dict(os.environ, PYTHONIOENCODING="utf-8")
        self.process = subprocess.Popen(
            [sys.executable, "-m", "whykit.mcp_server", "--root", str(root), "--watch-interval", "0"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
        )
        self.lines: queue.Queue[bytes] = queue.Queue()
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self) -> None:
        assert self.process.stdout is not None
        for line in iter(self.process.stdout.readline, b""):
            self.lines.put(line)
        self.lines.put(b"")

    def send(self, message: dict[str, Any]) -> None:
        assert self.process.stdin is not None
        self.process.stdin.write(json.dumps(message).encode("utf-8") + b"\n")
        self.process.stdin.flush()

    def receive(self, timeout: float = 30) -> dict[str, Any]:
        line = self.lines.get(timeout=timeout)
        if not line:
            raise AssertionError(f"server closed stdout: {self.stderr()}")
        return json.loads(line)

    def response(self, request_id: int, timeout: float = 30, *, stale: frozenset[int] = frozenset()) -> dict[str, Any]:
        """The reply to ``request_id``, skipping notifications and replies to ``stale`` requests."""
        while True:
            message = self.receive(timeout)
            if message.get("id") == request_id:
                return message
            if "method" not in message and message.get("id") not in stale:
                raise AssertionError(f"unexpected reply while waiting for {request_id}: {message}")

    def stderr(self) -> str:
        self.close()
        assert self.process.stderr is not None
        return self.process.stderr.read().decode("utf-8", "replace")[-2000:]

    def shutdown(self) -> None:
        self.close()
        for stream in (self.process.stdout, self.process.stderr):
            if stream is not None:
                stream.close()

    def close(self) -> None:
        if self.process.stdin and not self.process.stdin.closed:
            self.process.stdin.close()
        try:
            self.process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait()


@unittest.skipUnless(HAVE_MCP, "install the optional whykit[mcp] extra")
class ClassicHandshakeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.server = RawStdioServer()
        self.addCleanup(lambda: self.server.shutdown())

    def assert_handshake_session(self, init: dict[str, Any], version: str, *, first_id: int = 1) -> None:
        self.assertNotIn("error", init, init)
        result = init["result"]
        self.assertEqual(result["protocolVersion"], version)
        self.assertEqual(result["serverInfo"]["name"], "whykit")
        capabilities = result["capabilities"]
        # Handshake clients get the static capability set: no change events.
        self.assertFalse(capabilities["resources"].get("subscribe", False))
        self.assertFalse(capabilities["resources"].get("listChanged", False))
        self.assertFalse(capabilities["tools"].get("listChanged", False))
        self.server.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        self.server.send({"jsonrpc": "2.0", "id": first_id, "method": "tools/list"})
        listed = self.server.response(first_id)
        self.assertNotIn("error", listed, listed)
        self.assertEqual({tool["name"] for tool in listed["result"]["tools"]}, TOOLS)
        self.server.send({
            "jsonrpc": "2.0", "id": first_id + 1, "method": "tools/call",
            "params": {"name": "status", "arguments": {"today": "2026-09-17"}},
        })
        called = self.server.response(first_id + 1)
        self.assertNotIn("error", called, called)
        self.assertFalse(called["result"].get("isError", False), called)
        self.assertEqual(called["result"]["structuredContent"]["contract_version"], 1)

    def test_classic_initialize_connects_at_each_handshake_version(self) -> None:
        for version in ("2025-06-18", "2025-03-26"):
            with self.subTest(version=version):
                if version != "2025-06-18":
                    self.server.shutdown()
                    self.server = RawStdioServer()
                self.server.send(initialize(0, version))
                self.assert_handshake_session(self.server.response(0), version)

    def test_an_unknown_handshake_version_gets_the_newest_one_served(self) -> None:
        self.server.send(initialize(0, "2024-01-01"))
        self.assert_handshake_session(self.server.response(0), "2025-11-25")

    def test_initialize_after_an_answered_probe_connects(self) -> None:
        # The host probed, decided the answer was not usable, and fell back to
        # the classic handshake on the same pipe.
        self.server.send(discover(0))
        probe = self.server.response(0)
        self.assertIn(MODERN, probe["result"]["supportedVersions"])
        self.server.send(initialize(1, "2025-06-18"))
        self.assert_handshake_session(self.server.response(1), "2025-06-18", first_id=2)

    def test_probe_burst_over_the_queue_limit_still_falls_back(self) -> None:
        from whykit.mcp_server import NEGOTIATION_BUFFER_SIZE

        count = NEGOTIATION_BUFFER_SIZE * 3
        for request_id in range(count):
            self.server.send(discover(request_id))
        self.server.send(initialize(count, "2025-06-18"))
        reply = self.server.response(count, stale=frozenset(range(count)))
        self.assert_handshake_session(reply, "2025-06-18", first_id=count + 1)

    def test_initialize_pipelined_behind_a_timed_out_probe_connects(self) -> None:
        # The host's probe timed out while the server was still starting, so
        # both frames are already queued when the server reads its first line.
        self.server.send(discover(0))
        self.server.send({"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": 0}})
        self.server.send(initialize(1, "2025-11-25"))
        # The abandoned probe may still be answered; the host drops that reply.
        init = self.server.response(1, stale=frozenset({0}))
        self.assertNotEqual(init.get("error", {}).get("code"), UNSUPPORTED_PROTOCOL_VERSION, init)
        self.assert_handshake_session(init, "2025-11-25", first_id=2)

    def test_modern_clients_keep_the_2026_protocol_after_the_probe(self) -> None:
        self.server.send(discover(0))
        probe = self.server.response(0)
        self.assertTrue(probe["result"]["capabilities"]["resources"]["subscribe"])
        self.server.send({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {"_meta": envelope()}})
        listed = self.server.response(1)
        self.assertNotIn("error", listed, listed)
        self.assertEqual({tool["name"] for tool in listed["result"]["tools"]}, TOOLS)
        # Once a 2026-07-28 request has been served the connection's era is
        # settled, and a late handshake is refused with the typed error.
        self.server.send(initialize(2, "2025-06-18"))
        late = self.server.response(2)
        self.assertEqual(late["error"]["code"], UNSUPPORTED_PROTOCOL_VERSION, late)


class SupportedSdkRangeTests(unittest.TestCase):
    """CI exercises this file on both ends of the declared ``mcp`` range."""

    def test_ci_runs_the_handshake_tests_on_the_lowest_and_highest_allowed_sdk(self) -> None:
        import re

        pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        match = re.search(r'"mcp>=(\d+)\.(\d+),<(\d+)\.(\d+)"', pyproject)
        self.assertIsNotNone(match, "the mcp extra must declare a >=X.Y,<X.Z range")
        assert match is not None
        low_major, low_minor, high_major, high_minor = map(int, match.groups())
        self.assertEqual(low_major, high_major)
        expected = [f"{low_major}.{minor}.0" for minor in (low_minor, high_minor - 1)]
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        loop = re.search(r"for version in ([0-9. ]+); do", workflow)
        self.assertIsNotNone(loop, "ci.yml must loop over the supported mcp versions")
        assert loop is not None
        self.assertEqual(loop.group(1).split(), expected)
        self.assertIn("-p 'test_mcp_handshake.py'", workflow)
        self.assertIn("-p 'test_mcp_frames.py'", workflow)


if __name__ == "__main__":
    unittest.main()
