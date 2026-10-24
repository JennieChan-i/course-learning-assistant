import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from backend.main import cancellable


async def test_disconnection_cancels_work_before_result_commit():
    cancelled = asyncio.Event()

    async def work():
        try:
            await asyncio.sleep(10)
        finally:
            cancelled.set()

    request = SimpleNamespace(is_disconnected=AsyncMock(return_value=True))
    with pytest.raises(asyncio.CancelledError):
        await cancellable(request, work())
    # A task cancelled before starting never reaches its body or any commit.
    assert not cancelled.is_set()


async def test_connected_work_returns_its_result():
    async def work():
        return "finished"

    request = SimpleNamespace(is_disconnected=AsyncMock(return_value=False))
    assert await cancellable(request, work()) == "finished"
