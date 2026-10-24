import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from backend.agents import Agents, CitationGuard
from backend.knowledge import Knowledge
from backend.llm import LLM, new_run
from backend.schemas import AuditResult
from backend.store import Store


def tool_message(name, arguments):
    return SimpleNamespace(
        tool_calls=[
            SimpleNamespace(id=name, function=SimpleNamespace(name=name, arguments=json.dumps(arguments)))
        ],
        model_dump=lambda **_: {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": name,
                    "type": "function",
                    "function": {"name": name, "arguments": json.dumps(arguments)},
                }
            ],
        },
    )


def test_stream_filters_fake_references_across_chunk_boundaries():
    guard = CitationGuard({"S01"})
    output = "".join(guard.feed(part) for part in ["依据[S", "01]，伪造[FA", "KE_123]。"])
    output += guard.feed("", final=True)
    assert "[S01]" in output
    assert "[FAKE_123]" not in output
    assert guard.rejected == {"FAKE_123"}


async def test_schema_repair_is_bounded_and_keeps_usage_events(config):
    llm = LLM(config)
    llm.complete = AsyncMock(
        side_effect=[
            SimpleNamespace(content='{"approved":"wrong"}'),
            SimpleNamespace(content='{"approved":true,"issues":[]}'),
        ]
    )
    run = new_run(config, "test")
    result = await llm.structured([], AuditResult, run, "assessor")
    assert result.approved
    assert llm.complete.await_count == 2
    assert run["events"][0]["name"] == "schema_rejected"
    llm.complete = AsyncMock(return_value=SimpleNamespace(content='{"approved":"wrong"}'))
    with pytest.raises(ValueError, match="连续两次"):
        await llm.structured([], AuditResult, new_run(config, "test"), "assessor")
    assert llm.complete.await_count == 2


async def test_intent_clarification_does_not_retrieve_and_receives_recent_dialogue(config, store):
    kb = Knowledge(config)
    kb.search = lambda *_: pytest.fail("Clarification must not retrieve")
    agent = Agents(config, kb, store)
    agent.llm.complete = AsyncMock(
        return_value=tool_message(
            "search_course",
            {"query": "?", "intent": "clarify", "clarification": "不能引用 [FAKE_123]，请提供真实材料。"},
        )
    )
    run = new_run(config, "test")
    recent = [{"role": "user", "content": "上次的问题"}]
    assert await agent.collect("那个呢", None, run, recent=recent) == []
    assert "[FAKE_123]" not in run["clarification"]
    assert json.loads(agent.llm.complete.call_args.args[0][1]["content"])["recent_dialogue"] == recent
    assert agent.llm.complete.await_count == 1


async def test_web_results_survive_six_course_candidates(config, store):
    kb = Knowledge(config)
    kb.search = lambda *_: {"sources": [kb.corpus.source(f"S{i:02}") for i in range(1, 7)]}
    agent = Agents(config, kb, store)
    agent.llm.complete = AsyncMock(
        side_effect=[
            tool_message("search_course", {"query": "最新额度", "intent": "current"}),
            tool_message("search_web", {"query": "official credits"}),
        ]
    )
    agent.web.search = AsyncMock(
        return_value={
            "sources": [
                {
                    "id": "W1",
                    "type": "web",
                    "doc_name": "Official",
                    "chunk_text": "Current credits",
                    "url": "https://example.org",
                }
            ],
            "warning": "",
        }
    )
    sources = await agent.collect("最新额度", None, new_run(config, "test"), web_enabled=True, allow_web=True)
    assert len(sources) == 6 and sources[-1]["id"] == "W1"
    assert agent.web.search.await_count == 1


def test_search_budget_is_shared_across_learning_databases(config, tmp_path, monkeypatch):
    monkeypatch.setattr("backend.agents.ROOT", tmp_path)
    first_store, second_store = Store(tmp_path / "one.sqlite3"), Store(tmp_path / "two.sqlite3")
    first = Agents(config, Knowledge(config), first_store)
    second = Agents(config, Knowledge(config), second_store)
    first.web.store.reserve_search(1)
    first_store.clear_learning()
    assert second.web.store.search_usage() == 1
    with pytest.raises(ValueError):
        second.web.store.reserve_search(1)


async def test_coach_effort_does_not_reduce_assessor_effort(config):
    config.llm_coach_reasoning_effort = "low"
    llm = LLM(config)
    create = AsyncMock(
        return_value=SimpleNamespace(
            usage=None, choices=[SimpleNamespace(finish_reason="stop", message=SimpleNamespace(content="ok"))]
        )
    )
    llm.get_client = lambda: SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    run = new_run(config, "test")
    await llm.complete([], run, "coach")
    await llm.complete([], run, "assessor")
    assert create.call_args_list[0].kwargs["reasoning_effort"] == "low"
    assert "reasoning_effort" not in create.call_args_list[1].kwargs
