"""A stalled protocol consumer cannot make the negotiator queue grow forever."""
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from test_mcp_handshake import HAVE_MCP
from whykit.mcp_server import NEGOTIATION_BUFFER_SIZE, serve_negotiated_stream


@unittest.skipUnless(HAVE_MCP, "install the optional whykit[mcp] extra")
class NegotiatorBackpressureTests(unittest.TestCase):
    def test_slow_consumer_stops_reading_then_resumes_without_loss(self) -> None:
        import anyio

        async def exercise():
            release = anyio.Event()
            full = anyio.Event()
            consumed = 0

            class Incoming:
                produced = 0

                async def __aenter__(self):
                    return self

                async def __aexit__(self, *args):
                    return None

                async def __aiter__(self):
                    for _ in range(NEGOTIATION_BUFFER_SIZE * 3):
                        self.produced += 1
                        if self.produced == NEGOTIATION_BUFFER_SIZE + 1:
                            full.set()
                        yield SimpleNamespace(message=None)

            class Outgoing:
                async def aclose(self):
                    return None

            async def slow_loop(_server, receive, _write, **kwargs):
                nonlocal consumed
                await release.wait()
                async with receive:
                    async for _ in receive:
                        consumed += 1

            incoming = Incoming()

            async def serve():
                await serve_negotiated_stream(None, incoming, Outgoing(), lifespan_state=None, init_options=None)

            with anyio.fail_after(5), patch("mcp.server.runner.serve_dual_era_loop", slow_loop):
                async with anyio.create_task_group() as group:
                    group.start_soon(serve)
                    await full.wait()
                    self.assertEqual(incoming.produced, NEGOTIATION_BUFFER_SIZE + 1)
                    self.assertEqual(consumed, 0)
                    release.set()
            self.assertEqual(consumed, NEGOTIATION_BUFFER_SIZE * 3)

        anyio.run(exercise)


if __name__ == "__main__":
    unittest.main()
