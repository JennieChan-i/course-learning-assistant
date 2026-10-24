"""Exercise the real OpenAI-compatible transport and agent graph against an explicit mock.

This is a protocol regression test, not evidence of real model quality.
"""

import json

import httpx
import pytest
from openai import AsyncOpenAI

from backend.agents import Agents, demo_answers, demo_grades, demo_questions
from backend.config import Settings
from backend.knowledge import Knowledge
from backend.llm import new_run
from backend.schemas import ChatInput, PracticeInput


@pytest.mark.parametrize("effort", [None, "low"])
async def test_live_agent_flow_over_native_tool_and_stream_protocol(store, effort):
    demo_config = Settings(_env_file=None, app_mode="demo", dataset="sample")
    config = Settings(
        _env_file=None,
        app_mode="live",
        dataset="sample",
        llm_api_key="test-only",
        llm_model="mock-model",
        llm_reasoning_effort=effort,
        llm_base_url="https://mock.invalid/v1",
    )
    knowledge = Knowledge(demo_config)
    agent = Agents(config, knowledge, store)
    calls = []

    def handle(request):
        body = json.loads(request.content)
        calls.append(body)
        if body.get("stream"):
            content = "RAG 先检索相关资料，再依据证据生成回答。[S01]"
            chunks = [
                {
                    "id": "stream",
                    "object": "chat.completion.chunk",
                    "created": 0,
                    "model": "mock-model",
                    "choices": [{"index": 0, "delta": {"content": content}, "finish_reason": None}],
                },
                {
                    "id": "stream",
                    "object": "chat.completion.chunk",
                    "created": 0,
                    "model": "mock-model",
                    "choices": [],
                    "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
                },
            ]
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content="".join("data: " + json.dumps(c) + "\n\n" for c in chunks) + "data: [DONE]\n\n",
            )
        tail = body["messages"][-1]["content"]
        message = {"role": "assistant", "content": "已获得课程证据。"}
        if isinstance(body.get("tool_choice"), dict):
            message = {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "c1",
                        "type": "function",
                        "function": {
                            "name": "search_course",
                            "arguments": json.dumps({"query": "RAG 检索 重排 证据不足", "course_id": "01"}),
                        },
                    },
                    {
                        "id": "c2",
                        "type": "function",
                        "function": {"name": "get_source", "arguments": '{"source_id":"S02"}'},
                    },
                    {
                        "id": "c3",
                        "type": "function",
                        "function": {"name": "get_source", "arguments": '{"source_id":"S03"}'},
                    },
                ],
            }
        elif "QuestionSet" in tail:
            message["content"] = json.dumps({"questions": demo_questions()}, ensure_ascii=False)
        elif "AuditResult" in tail:
            message["content"] = json.dumps({"approved": True, "issues": []})
        elif "GradeSet" in tail:
            message["content"] = json.dumps({"grades": demo_grades()}, ensure_ascii=False)
        elif '"title": "Route"' in tail:
            message["content"] = json.dumps({"intent": "explain", "query": "RAG 检索", "clarification": ""})
        response = {
            "id": "mock",
            "object": "chat.completion",
            "created": 0,
            "model": "mock-model",
            "choices": [
                {
                    "index": 0,
                    "message": message,
                    "finish_reason": "tool_calls" if message.get("tool_calls") else "stop",
                }
            ],
            "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
        }
        return httpx.Response(200, json=response)

    agent.llm.client = AsyncOpenAI(
        api_key="test-only",
        base_url=config.llm_base_url,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handle)),
    )
    try:
        session_id = store.session(None, "01", "RAG")
        run = new_run(config, "chat")
        events = [
            e
            async for e in agent.chat(
                ChatInput(message="RAG 是什么", session_id=session_id, course_id="01"), run
            )
        ]
        assert "[S01]" in "".join(e.get("text", "") for e in events)
        assert any(e["name"] == "tool_call" for e in run["events"])
        practice_run = new_run(config, "practice")
        practice = await agent.create_practice(PracticeInput(course_id="01", topic="RAG"), practice_run)
        grade_run = new_run(config, "grade")
        result = await agent.grade(practice, demo_answers(), grade_run)
        assert len(result["grades"]) == 3
        assert any(e.get("agent") == "assessor" for e in grade_run["events"])
        assert any(e["name"] == "handoff" for e in practice_run["events"])
        assert run["usage"]["prompt_tokens"] > 0
        assert any(b.get("stream") for b in calls)
        assert all("temperature" not in b for b in calls)
        assert all(b.get("reasoning_effort") == effort for b in calls)
    finally:
        await agent.llm.close()
