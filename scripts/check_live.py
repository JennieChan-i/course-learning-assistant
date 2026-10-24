import asyncio
import time
from backend.config import Settings, ROOT
from backend.agents import Agents
from backend.knowledge import Knowledge
from backend.store import Store
from backend.llm import new_run
from backend.schemas import ChatInput
from backend.evaluation_jobs import save


async def ask(agent, store, query, web=False):
    sid = store.session(None, None, query)
    run = new_run(agent.settings, "final_check")
    begin = time.perf_counter()
    first = None
    answer = ""
    sources = []
    warnings = []
    async for item in agent.chat(ChatInput(message=query, session_id=sid, web_enabled=web), run):
        if item["event"] == "delta":
            if first is None:
                first = round((time.perf_counter() - begin) * 1000)
            answer += item["text"]
        elif item["event"] == "sources":
            sources = item["sources"]
        elif item["event"] == "warning":
            warnings.append(item["text"])
    return {
        "query": query,
        "answer": answer,
        "source_ids": [s["id"] for s in sources],
        "external_urls": [s["url"] for s in sources if s["type"] == "web"],
        "first_content_ms": first,
        "elapsed_ms": round((time.perf_counter() - begin) * 1000),
        "events": run["events"],
        "usage": run["usage"],
        "parameters": run["generation_parameters"],
        "warnings": warnings,
    }


async def main():
    # The comparison changes global effort; neutralize any coach-only override in the local .env.
    s = Settings(dataset="sample", app_mode="live", llm_coach_reasoning_effort=None)
    kb = Knowledge(s)
    kb.search("RAG")
    store = Store(s.runtime / "final-checks.sqlite3")
    agent = Agents(s, kb, store)
    try:
        web = await ask(
            agent,
            store,
            "请联网搜索 Tavily 当前免费额度和 basic 搜索消耗，只使用 docs.tavily.com 官方文档，include_domains 必须限定 docs.tavily.com。课程没提供当前数值。",
            True,
        )
        web.update(status="completed", transport="real MCP Streamable HTTP", model=s.llm_model)
        save(ROOT / "docs/reports/mcp-official-integration.json", web)
        print(
            {"check": "official_mcp", "urls": web["external_urls"], "warnings": web["warnings"]}, flush=True
        )
        report = {
            "model": s.llm_model,
            "conditions": "self-authored sample; models warmed; sequential requests; three prompts per mode; exploratory, not an acceptance P95; account billing not configured",
            "quality_status": "not_independently_human_scored",
            "records": [],
        }
        for effort in (None, "low"):
            s.llm_reasoning_effort = effort
            for query in ["RAG是什么？", "重排能补回没有召回的资料吗？", "学习偏好和作答记录应该怎样区分？"]:
                try:
                    result = await ask(agent, store, query)
                    result["status"] = "completed"
                except Exception as exc:
                    result = {"query": query, "status": "failed", "error_type": type(exc).__name__}
                result["reasoning_effort"] = effort
                report["records"].append(result)
                save(ROOT / "docs/reports/reasoning-effort-exploratory.json", report)
                print(
                    {
                        "effort": effort,
                        "status": result["status"],
                        "first_content_ms": result.get("first_content_ms"),
                        "elapsed_ms": result.get("elapsed_ms"),
                    },
                    flush=True,
                )
    finally:
        await agent.llm.close()
        kb.close()


if __name__ == "__main__":
    asyncio.run(main())
