"""HTTP budgets reject work before JSON parsing and release every active slot."""
from __future__ import annotations

import asyncio
import json
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from whykit import mcp_server as mcp  # noqa: E402


class HTTPBudgetTests(unittest.IsolatedAsyncioTestCase):
    async def request(self, app, *, events=None, headers=(), receive=None):
        sent = []
        events = list(events or [{"type": "http.request", "body": b"", "more_body": False}])

        async def next_event():
            return events.pop(0) if events else {"type": "http.disconnect"}

        async def send(event):
            sent.append(event)

        await app({"type": "http", "method": "POST", "path": "/mcp", "headers": list(headers)},
                  receive or next_event, send)
        return sent

    async def ok(self, scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    def status(self, sent):
        return next(event["status"] for event in sent if event["type"] == "http.response.start")

    async def test_burst_is_bounded_and_refills_without_client_state(self):
        with patch.object(mcp, "HTTP_REQUEST_BURST", 2), patch.object(mcp.time, "monotonic", return_value=0) as clock:
            app = mcp.limit_http_requests(self.ok)
            self.assertEqual(self.status(await self.request(app)), 200)
            self.assertEqual(self.status(await self.request(app)), 200)
            denied = await self.request(app)
            self.assertEqual(self.status(denied), 429)
            self.assertIn((b"retry-after", b"1"), denied[0]["headers"])
            self.assertEqual(json.loads(denied[1]["body"])["error"]["code"], "rate_limited")
            clock.return_value = 0.5
            self.assertEqual(self.status(await self.request(app)), 200)

    async def test_inflight_limit_and_cancelled_request_release_slots(self):
        entered, release = asyncio.Event(), asyncio.Event()

        async def held(scope, receive, send):
            entered.set()
            await release.wait()
            await self.ok(scope, receive, send)

        with patch.object(mcp, "MAX_HTTP_INFLIGHT", 1):
            app = mcp.limit_http_requests(held)
            task = asyncio.create_task(self.request(app))
            await entered.wait()
            denied = await self.request(app)
            self.assertEqual(self.status(denied), 503)
            self.assertEqual(json.loads(denied[1]["body"])["error"]["code"], "server_busy")
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            release.set()
            self.assertEqual(self.status(await self.request(app)), 200)

    async def test_declared_and_chunked_body_budgets_before_sdk_entry(self):
        reached = []

        async def consume(scope, receive, send):
            reached.append(await receive())
            await self.ok(scope, receive, send)

        with patch.object(mcp, "MAX_HTTP_REQUEST_BYTES", 4):
            app = mcp.limit_http_requests(consume)
            self.assertEqual(self.status(await self.request(app, headers=[(b"content-length", b"5")])), 413)
            events = [{"type": "http.request", "body": b"{} ", "more_body": True},
                      {"type": "http.request", "body": b"  ", "more_body": False}]
            self.assertEqual(self.status(await self.request(app, events=events)), 413)
            self.assertEqual(reached, [])
            events[-1]["body"] = b" "
            self.assertEqual(self.status(await self.request(app, events=events)), 200)
            self.assertEqual(reached[0], {"type": "http.request", "body": b"{}  ", "more_body": False})

    async def test_invalid_lengths_disconnects_and_timeouts_do_not_hold_slots(self):
        async def stalled():
            await asyncio.Event().wait()

        with patch.object(mcp, "MAX_HTTP_INFLIGHT", 1), patch.object(mcp, "HTTP_BODY_TIMEOUT_SECONDS", 0.01):
            app = mcp.limit_http_requests(self.ok)
            for headers in ([(b"content-length", b"-1")], [(b"content-length", b"999999999999999999")],
                            [(b"content-length", b"1"), (b"Content-Length", b"1")]):
                self.assertEqual(self.status(await self.request(app, headers=headers)), 400)
            self.assertEqual(await self.request(app, events=[{"type": "http.disconnect"}]), [])
            self.assertEqual(self.status(await self.request(app, receive=stalled)), 408)
            self.assertEqual(self.status(await self.request(app)), 200)

    async def test_authentication_and_host_validation_precede_budgets(self):
        server = types.SimpleNamespace(streamable_http_app=lambda host: self.ok)
        with patch.object(mcp, "HTTP_REQUEST_BURST", 1):
            app = mcp.http_app(server, "0.0.0.0", 8000, "t" * 40)
            self.assertEqual(self.status(await self.request(app)), 401)
            self.assertEqual(self.status(await self.request(app, headers=[(b"authorization", b"Bearer " + b"t" * 40)])), 200)
            local = mcp.http_app(server, "127.0.0.1", 8000, None)
            self.assertEqual(self.status(await self.request(local, headers=[(b"host", b"evil.example")])), 421)
            self.assertEqual(self.status(await self.request(local, headers=[(b"host", b"localhost:8000")])), 200)


if __name__ == "__main__":
    unittest.main()
