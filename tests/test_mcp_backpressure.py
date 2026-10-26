"""Negotiation cannot read ahead or retain reply IDs without a bound."""
from __future__ import annotations

import gc
import unittest
import weakref
from unittest.mock import patch

from test_mcp_handshake import HAVE_MCP, MODERN
from whykit.mcp_server import PROTOCOL_VERSION_META_KEY, serve_negotiated_stream


class TrackedID(str):
    """A valid string ID whose lifetime can be observed without retaining it."""


class Sink:
    def __init__(self):
        self.sent = 0
        self.closed = False

    async def send(self, item):
        self.sent += 1

    async def aclose(self):
        self.closed = True


@unittest.skipUnless(HAVE_MCP, "install the optional whykit[mcp] extra")
class NegotiatorBackpressureTests(unittest.TestCase):
    def test_stalled_consumer_bounds_read_ahead_then_preserves_fifo_at_eof(self):
        import anyio
        from mcp.shared.message import SessionMessage
        from mcp_types import JSONRPCRequest

        async def exercise():
            release = anyio.Event()
            consumed = []

            class Incoming:
                produced = 0

                async def __aenter__(self):
                    return self

                async def __aexit__(self, *args):
                    return None

                async def __aiter__(self):
                    for request_id in range(96):
                        self.produced += 1
                        yield SessionMessage(JSONRPCRequest(jsonrpc="2.0", id=request_id, method="ping"))

            async def slow_loop(_server, receive, _write, **kwargs):
                await release.wait()
                async with receive:
                    async for item in receive:
                        consumed.append(item.message.id)

            incoming, outgoing = Incoming(), Sink()
            with anyio.fail_after(5), patch("mcp.server.runner.serve_dual_era_loop", slow_loop):
                async with anyio.create_task_group() as group:
                    group.start_soon(self.serve, incoming, outgoing)
                    await anyio.wait_all_tasks_blocked()
                    produced = incoming.produced
                    before = list(consumed)
                    release.set()
            return produced, before, consumed, outgoing.closed

        produced, before, consumed, closed = anyio.run(exercise)
        self.assertEqual(produced, 33)  # 32 queued and one blocked send.
        self.assertEqual(before, [])
        self.assertEqual(consumed, list(range(96)))
        self.assertTrue(closed)

    def test_discovery_ids_are_bounded_and_released_when_negotiation_finishes(self):
        import anyio
        from mcp.shared.message import SessionMessage
        from mcp_types import JSONRPCRequest, JSONRPCResponse

        async def exercise():
            sender, incoming = anyio.create_memory_object_stream(0)
            outgoing = Sink()
            refs = []

            async def echo_loop(_server, receive, write, **kwargs):
                async with receive:
                    async for item in receive:
                        await write.send(SessionMessage(JSONRPCResponse.model_construct(id=item.message.id, result={})))

            async def batch(start, count, method):
                for number in range(start, start + count):
                    request_id = TrackedID(str(number))
                    refs.append(weakref.ref(request_id))
                    params = {"_meta": {PROTOCOL_VERSION_META_KEY: MODERN}} if method == "server/discover" else None
                    # Preserve the valid str subclass to observe retention across the SDK boundary.
                    message = JSONRPCRequest.model_construct(jsonrpc="2.0", id=request_id, method=method, params=params)
                    await sender.send(SessionMessage(message))
                del message, request_id
                await anyio.wait_all_tasks_blocked()
                gc.collect()
                return sum(ref() is not None for ref in refs)

            with anyio.fail_after(5), patch("mcp.server.runner.serve_dual_era_loop", echo_loop):
                async with anyio.create_task_group() as group:
                    group.start_soon(self.serve, incoming, outgoing)
                    discovery = await batch(0, 96, "server/discover")
                    settled = await batch(96, 1, "initialize")
                    ordinary = await batch(97, 128, "ping")
                    await sender.aclose()
            return discovery, settled, ordinary, outgoing.sent, outgoing.closed

        discovery, settled, ordinary, sent, closed = anyio.run(exercise)
        with self.subTest(phase="discovery"):
            self.assertLessEqual(discovery, 32)
        with self.subTest(phase="initialized"):
            self.assertLessEqual(settled, 1)  # Only the current request may remain in loop locals.
        with self.subTest(phase="ordinary traffic"):
            self.assertLessEqual(ordinary, 1)
        self.assertEqual(sent, 225)
        self.assertTrue(closed)

    def test_cancellation_closes_the_writer_with_a_stalled_consumer(self):
        import anyio

        async def exercise():
            sender, incoming = anyio.create_memory_object_stream(0)
            outgoing = Sink()

            async def stalled_loop(_server, receive, _write, **kwargs):
                async with receive:
                    await anyio.sleep_forever()

            with anyio.fail_after(5), patch("mcp.server.runner.serve_dual_era_loop", stalled_loop):
                async with sender, anyio.create_task_group() as group:
                    group.start_soon(self.serve, incoming, outgoing)
                    await anyio.wait_all_tasks_blocked()
                    group.cancel_scope.cancel()
            return outgoing.closed

        self.assertTrue(anyio.run(exercise))

    @staticmethod
    async def serve(incoming, outgoing):
        await serve_negotiated_stream(None, incoming, outgoing, lifespan_state=None, init_options=None)


if __name__ == "__main__":
    unittest.main()
