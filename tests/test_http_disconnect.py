"""A real TCP disconnect must cancel generation before any learning result is committed."""

import asyncio
import socket

import httpx
import pytest
import uvicorn

from backend.main import create_app


async def test_real_http_disconnect_cancels_inflight_practice(config, store):
    app = create_app(config, store=store)
    started, stopped = asyncio.Event(), asyncio.Event()

    async def slow_practice(body, run):
        started.set()
        try:
            await asyncio.sleep(30)
        finally:
            stopped.set()

    app.state.agents.create_practice = slow_practice
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(16)
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="critical", timeout_graceful_shutdown=1))
    serving = asyncio.create_task(server.serve(sockets=[sock]))
    try:
        async with asyncio.timeout(5):
            while not server.started:
                await asyncio.sleep(0.01)
        async with httpx.AsyncClient() as client:
            request = asyncio.create_task(
                client.post(
                    f"http://127.0.0.1:{port}/api/practices",
                    json={"topic": "cancel test"},
                    headers={"X-Learning-Client": "workbench"},
                )
            )
            await asyncio.wait_for(started.wait(), 3)
            request.cancel()
            with pytest.raises(asyncio.CancelledError):
                await request
        await asyncio.wait_for(stopped.wait(), 3)
        await asyncio.sleep(0.05)
        assert store.practices() == []
        assert store.traces()[0]["status"] == "cancelled"
        assert not app.state.lock.locked()
    finally:
        server.should_exit = True
        await serving
