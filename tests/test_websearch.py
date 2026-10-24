from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

from backend.websearch import WebSearch


async def test_mcp_discovers_tool_and_uses_only_basic_search(config, store, monkeypatch):
    config.tavily_api_key = "test-only"
    calls = []

    @asynccontextmanager
    async def transport(*args, **kwargs):
        assert args[0] == "https://mcp.tavily.com/mcp/"
        yield (None, None, lambda: None)

    @asynccontextmanager
    async def session(*args):
        async def call_tool(name, params):
            calls.append((name, params))
            return SimpleNamespace(
                isError=False,
                content=[
                    SimpleNamespace(
                        type="text",
                        text='{"results":[{"url":"https://example.org/docs","title":"Docs","content":"Evidence"},{"url":"javascript:alert(1)","content":"bad"}]}',
                    )
                ],
            )

        yield SimpleNamespace(
            initialize=AsyncMock(),
            list_tools=AsyncMock(
                return_value=SimpleNamespace(
                    tools=[
                        SimpleNamespace(
                            name="tavily_search",
                            inputSchema={
                                "properties": {
                                    k: {} for k in ["query", "search_depth", "max_results", "auto_parameters"]
                                }
                            },
                        )
                    ]
                )
            ),
            call_tool=call_tool,
        )

    monkeypatch.setattr("backend.websearch.streamable_http_client", transport)
    monkeypatch.setattr("backend.websearch.ClientSession", session)
    result = await WebSearch(config, store).search("MCP 官方文档")
    assert len(result["sources"]) == 1
    assert calls[0][0] == "tavily_search"
    assert calls[0][1]["search_depth"] == "basic"
    assert calls[0][1]["auto_parameters"] is False
    assert store.search_usage() == 1


async def test_keyless_quota_envelope_is_not_treated_as_success(config, store, monkeypatch):
    config.tavily_allow_keyless = True

    @asynccontextmanager
    async def transport(*args, **kwargs):
        assert kwargs["http_client"].headers["X-Tavily-Access-Mode"] == "keyless"
        assert "Authorization" not in kwargs["http_client"].headers
        yield None, None, lambda: None

    @asynccontextmanager
    async def session(*args):
        async def call(name, params):
            assert "auto_parameters" not in params
            return SimpleNamespace(
                isError=False,
                content=[
                    SimpleNamespace(
                        type="text",
                        text='{"code":"monthly_cap_reached_bonus_eligible","next_actions":[{"type":"agentic_payment"}]}',
                    )
                ],
            )

        yield SimpleNamespace(
            initialize=AsyncMock(),
            list_tools=AsyncMock(
                return_value=SimpleNamespace(
                    tools=[
                        SimpleNamespace(
                            name="tavily_search",
                            inputSchema={
                                "properties": {k: {} for k in ["query", "search_depth", "max_results"]}
                            },
                        )
                    ]
                )
            ),
            call_tool=call,
        )

    monkeypatch.setattr("backend.websearch.streamable_http_client", transport)
    monkeypatch.setattr("backend.websearch.ClientSession", session)
    result = await WebSearch(config, store).search("公开文档")
    assert not result["sources"]
    assert "额度" in result["warning"]
    assert store.search_usage() == 1


async def test_mcp_failure_spends_reserved_slot_and_redacts_error(config, store, monkeypatch):
    config.tavily_api_key = "sensitive-test-key"

    @asynccontextmanager
    async def transport(*args, **kwargs):
        raise RuntimeError("provider error includes sensitive-test-key")
        yield

    monkeypatch.setattr("backend.websearch.streamable_http_client", transport)
    result = await WebSearch(config, store).search("RAG")
    assert result["sources"] == []
    assert "sensitive-test-key" not in result["warning"]
    assert store.search_usage() == 1
