"""Replay with private temporary learning state. No replay can submit production grades."""

import asyncio
import json
import tempfile
import time
from pathlib import Path

from .agents import Agents
from .llm import new_run
from .observability import build_info, finish_snapshot, start_snapshot
from .schemas import ChatInput, PracticeInput, QuestionSet, Submission
from .store import Store, now


class FrozenCorpus:
    def __init__(self, snapshot):
        self.parents = snapshot.get("source_pool", {})
        self.version = snapshot.get("dataset_version", "historical")

    def source(self, source_id):
        if source_id not in self.parents:
            raise ValueError("历史资料池中没有该来源，固定证据模式不能读取新资料。")
        return dict(self.parents[source_id])


class FrozenKnowledge:
    def __init__(self, snapshot):
        self.corpus = FrozenCorpus(snapshot)

    def search(self, query, course_id=None, **kwargs):
        sources = [s for s in self.corpus.parents.values() if not course_id or s.get("doc_id") == course_id]
        return {"sources": sources[:6], "warning": "固定历史资料池复测，不运行向量召回或重排。"}


def validate_snapshot(snapshot):
    if not isinstance(snapshot, dict) or snapshot.get("schema_version") != 1:
        raise ValueError("不支持的现场格式。")
    if len(json.dumps(snapshot, ensure_ascii=False).encode()) > 1_000_000:
        raise ValueError("单个现场超过1MB，请检查导出文件。")
    if snapshot.get("mode") not in ("demo", "live") or snapshot.get("dataset") not in ("sample", "private"):
        raise ValueError("现场模式或数据集无效。")
    request = snapshot.get("request")
    if not isinstance(request, dict):
        raise ValueError("现场缺少有效请求。")
    if snapshot.get("kind") == "chat":
        ChatInput.model_validate(request)
    elif snapshot.get("kind") == "practice":
        PracticeInput.model_validate(request)
    elif snapshot.get("kind") == "assessment":
        QuestionSet.model_validate({"questions": request.get("practice", {}).get("questions")})
        Submission.model_validate({"answers": request.get("answers")})
    else:
        raise ValueError("不支持的现场任务类型。")
    pool = snapshot.get("source_pool", {})
    if not isinstance(pool, dict) or len(pool) > 80:
        raise ValueError("历史证据池无效或超过上限。")
    for source_id, source in pool.items():
        if (
            not isinstance(source, dict)
            or source.get("id") != source_id
            or not all(isinstance(source.get(k), str) for k in ("id", "type", "doc_name", "chunk_text"))
        ):
            raise ValueError("历史证据缺少必需字段。")


async def replay(snapshot, config, production, strategy, knowledge=None):
    if snapshot.get("redacted"):
        raise ValueError("现场已删除，不能复测。")
    validate_snapshot(snapshot)
    if strategy not in ("frozen_evidence", "current_knowledge"):
        raise ValueError("不支持的复测范围。")
    if snapshot.get("dataset") != config.dataset:
        raise ValueError("案例与当前课程数据集不同，请切换到对应数据集后再测。")
    if config.app_mode == "live" and not config.llm_ready:
        raise ValueError("请先配置LLM；未接入API不能执行真实复测。")
    if strategy == "current_knowledge" and knowledge is None:
        raise ValueError("当前知识库复测需要已打开的本地检索服务。")
    before = production.learning_digest()
    cfg = config.model_copy(deep=True)
    cfg.tavily_api_key = ""
    cfg.tavily_allow_keyless = False
    selected = FrozenKnowledge(snapshot) if strategy == "frozen_evidence" else knowledge
    directory = production.path.parent / "badcase-replays"
    directory.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    result = {
        "status": "failed",
        "strategy": strategy,
        "mode_label": cfg.app_mode,
        "build": build_info(),
        "checks": {},
        "output": None,
        "scope": "historical evidence pool; current prompts and model configuration; retrieval quality not tested"
        if strategy == "frozen_evidence"
        else "current local knowledge; current prompts and configuration; no fresh web search",
        "original_configuration": snapshot.get("configuration"),
        "original_dataset_version": snapshot.get("dataset_version"),
    }
    with tempfile.TemporaryDirectory(prefix="isolated-", dir=directory) as temporary:
        isolated = Store(Path(temporary) / "learning.sqlite3")
        agent = Agents(cfg, selected, isolated)
        run = new_run(cfg, snapshot["kind"])
        run["is_badcase_replay"] = True
        run["dataset_version"] = selected.corpus.version
        request = dict(snapshot["request"])
        start_snapshot(run, cfg, request)
        result["configuration"] = run["snapshot"]["configuration"]
        result["dataset_version"] = selected.corpus.version
        try:
            if snapshot["kind"] == "chat":
                memory = snapshot.get("memory", {})
                for key, value in memory.get("preferences", {}).items():
                    isolated.preference(key, value)
                session_id = isolated.session(None, request.get("course_id"), "隔离复测")
                for message in memory.get("recent_dialogue", []):
                    isolated.add_message(session_id, message["role"], message["content"])
                for i, record in enumerate(memory.get("learning_evidence", [])):
                    with isolated.connect() as c:
                        c.execute(
                            "INSERT INTO reviews VALUES (?,?,?,?,?,?,?,?)",
                            (
                                f"replay-{i}",
                                record["topic"],
                                request.get("course_id"),
                                "[]",
                                record["misconception"],
                                0,
                                now(),
                                now(),
                            ),
                        )
                request.update(session_id=session_id, web_enabled=False)
                text, sources, warnings = "", [], []
                async for item in agent.chat(ChatInput.model_validate(request), run):
                    if item["event"] == "delta":
                        text += item["text"]
                    elif item["event"] == "sources":
                        sources = item["sources"]
                    elif item["event"] == "warning":
                        warnings.append(item["text"])
                result["output"] = {"text": text, "sources": sources, "warnings": warnings}
            elif snapshot["kind"] == "practice":
                enriched = snapshot.get("practice_request", request)
                if request.get("review_id"):
                    with isolated.connect() as c:
                        c.execute(
                            "INSERT INTO reviews VALUES (?,?,?,?,?,?,?,?)",
                            (
                                request["review_id"],
                                enriched["topic"],
                                enriched.get("course_id"),
                                "[]",
                                enriched.get("previous_misconception", ""),
                                0,
                                now(),
                                now(),
                            ),
                        )
                result["output"] = await agent.create_practice(PracticeInput.model_validate(request), run)
            elif snapshot["kind"] == "assessment":
                result["output"] = await agent.grade(request["practice"], request["answers"], run)
            else:
                raise ValueError("此类历史运行不支持复测。")
            result["status"] = "completed"
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            result["error_type"] = type(exc).__name__
            result["error"] = (
                str(exc) if isinstance(exc, ValueError) else "复测调用失败，请检查服务连接、额度或超时。"
            )
        finally:
            await agent.llm.close()
            run = finish_snapshot(run, cfg)
            result.update(
                usage=run["usage"],
                events=run["events"],
                elapsed_ms=round((time.perf_counter() - started) * 1000),
            )
    result["checks"] = {
        "execution_succeeded": result["status"] == "completed",
        "production_learning_unchanged": before == production.learning_digest(),
        "no_invalid_citations": not any(e["name"] == "citation_rejected" for e in run["events"]),
        "no_unlisted_tools": not any(e["name"] == "tool_rejected" for e in run["events"]),
        "no_schema_rejections": not any(e["name"] == "schema_rejected" for e in run["events"]),
        "citations_present_when_required": not any(e["name"] == "citation_missing" for e in run["events"]),
    }
    if not all(result["checks"].values()):
        result["status"] = "failed"
    result["human_semantic_review"] = "pending; automatic checks do not establish answer or grading quality"
    return result


async def evaluate_export(config, path, max_cases=1):
    if not config.llm_ready:
        return {"status": "not_run", "reason": "LLM API is not configured"}
    if path.stat().st_size > 5_000_000:
        raise ValueError("回归文件过大，请拆成小批次。")
    try:
        rows = [
            json.loads(line) for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()
        ]
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise ValueError("请提供UTF-8编码的有效JSONL回归文件。") from exc
    if not 1 <= max_cases <= 20:
        raise ValueError("每次复测限制为1至20条，默认1条。")
    if not rows:
        raise ValueError("回归文件为空。")
    # Validate the complete selected batch before spending any API tokens.
    for row in rows[:max_cases]:
        if (
            not isinstance(row, dict)
            or row.get("schema") != "course-assistant.badcase.v1"
            or row.get("split") != "regression"
            or not isinstance(row.get("expected_behavior"), str)
            or not row["expected_behavior"].strip()
            or not row.get("id")
        ):
            raise ValueError("不是有效的badcase回归样例；不会自动写入锁定测试集。")
        validate_snapshot(row.get("snapshot"))
    records = []
    with tempfile.TemporaryDirectory(dir=config.runtime) as temporary:
        isolated = Store(Path(temporary) / "evaluation.sqlite3")
        for row in rows[:max_cases]:
            output = await replay(row["snapshot"], config, isolated, "frozen_evidence")
            records.append({"id": row["id"], "expected_behavior": row["expected_behavior"], "result": output})
    return {
        "status": "completed_awaiting_human_review",
        "records": records,
        "selected": len(records),
        "available": len(rows),
        "note": "isolated historical evidence evaluation; no automatic human correctness score",
    }
