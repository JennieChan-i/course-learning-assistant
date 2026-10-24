"""Resumable, isolated live evaluations. Draft scores never become human gold."""

import hashlib
import json
import time
import re
import shutil

from .agents import Agents
from .config import ROOT
from .knowledge import Knowledge
from .llm import new_run
from .store import Store


def save(path, report):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def summarize(report):
    report["modes"] = {}
    for mode in ("single_coach", "two_agents"):
        attempts = [b for b in report["batches"] if b["mode"] == mode]
        batches = [b for b in attempts if b["status"] == "completed"]
        records = [r for b in batches for r in b["records"]]
        gold = [r for r in records if r["human_expected_total"] is not None]
        report["modes"][mode] = {
            "case_count": len(records),
            "human_scored_count": len(gold),
            "within_one_point": sum(abs(r["score"] - r["human_expected_total"]) <= 1 for r in gold)
            / len(gold)
            if gold
            else None,
            "elapsed_seconds": round(sum(b["elapsed_seconds"] for b in attempts), 2),
            "failed_batches": len(attempts) - len(batches),
            "usage": {
                k: sum(b["usage"][k] for b in attempts) for k in ("prompt_tokens", "completion_tokens")
            },
        }
    by_mode = {
        m: {
            r["id"]: r["score"]
            for b in report["batches"]
            if b["mode"] == m and b["status"] == "completed"
            for r in b["records"]
        }
        for m in ("single_coach", "two_agents")
    }
    overlap = set(by_mode["single_coach"]) & set(by_mode["two_agents"])
    report["score_disagreement"] = {
        "paired_cases": len(overlap),
        "different_scores": sum(by_mode["single_coach"][i] != by_mode["two_agents"][i] for i in overlap),
        "note": "Agreement is not accuracy; independent human gold is still required.",
    }


async def grading_job(config, max_batches=None, retry_report=None):
    if not config.llm_ready:
        return {"status": "not_run", "reason": "LLM API is not configured"}
    if config.dataset != "sample":
        raise ValueError(
            "公开40条评分评测使用自编课程，请指定 --dataset sample。不会把公开来源ID套用到私有课程。"
        )
    from .evaluation import read_jsonl

    from .evaluation_review import reviewed_rows

    rows, _ = reviewed_rows(ROOT / "evals/grading.jsonl", read_jsonl(ROOT / "evals/grading.jsonl"))
    retry_keys = None
    retry_origin = None
    if retry_report:
        retry_report = retry_report.resolve()
        previous = json.loads(retry_report.read_text(encoding="utf-8"))
        if previous.get("model") != config.llm_model or previous.get("expected_cases_per_mode") != len(rows):
            raise ValueError("重试报告与当前模型或评分集合不一致。")
        retry_keys = {b["id"] for b in previous["batches"] if b["status"] != "completed"}
        retry_origin = {
            "fingerprint": previous["fingerprint"],
            "sha256": hashlib.sha256(retry_report.read_bytes()).hexdigest(),
        }
    scope = {
        "model": config.llm_model,
        "parameters": new_run(config, "scope")["generation_parameters"],
        "cases": rows,
        "retry_origin": retry_origin,
        "code": [
            hashlib.sha256((ROOT / p).read_bytes()).hexdigest()
            for p in ("backend/agents.py", "backend/llm.py", "skills/practice/SKILL.md")
        ],
    }
    fingerprint = hashlib.sha256(json.dumps(scope, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[
        :16
    ]
    path = config.runtime / "reports" / f"grading-{fingerprint}.json"
    report = (
        json.loads(path.read_text(encoding="utf-8"))
        if path.exists()
        else {
            "fingerprint": fingerprint,
            "model": config.llm_model,
            "status": "in_progress",
            "batches": [],
            "expected_cases_per_mode": len(rows),
            "gold_status": "pending_independent_human_review",
            "retry_origin": retry_origin,
            "scope": scope,
        }
    )
    kb = Knowledge(config)
    agent = Agents(config, kb, Store(config.runtime / "evaluation-only.sqlite3"))
    executed = 0
    try:
        for dual in (False, True):
            mode = "two_agents" if dual else "single_coach"
            for start in range(0, len(rows), 3):
                key = f"{mode}:{start}"
                if retry_keys is not None and key not in retry_keys:
                    continue
                if any(b["id"] == key for b in report["batches"]):
                    continue
                if max_batches is not None and executed >= max_batches:
                    summarize(report)
                    save(path, report)
                    return report
                original = rows[start : start + 3]
                batch = original + [original[-1]] * (3 - len(original))
                questions = [{**r["question"], "id": f"q{i}"} for i, r in enumerate(batch)]
                answers = {f"q{i}": r["answer"] for i, r in enumerate(batch)}
                ids = {sid for q in questions for sid in q["source_ids"]}
                practice = {
                    "questions": questions,
                    "sources": [kb.corpus.source(i) for i in ids],
                    "course_id": None,
                    "topic": "",
                }
                run = new_run(config, mode)
                entry = {"id": key, "mode": mode, "status": "failed", "records": []}
                begin = time.perf_counter()
                print(f"EVAL {mode} {start + 1}-{min(start + 3, len(rows))}/{len(rows)}", flush=True)
                try:
                    result = await agent.grade(practice, answers, run, dual_agent=dual)
                    for row, g in zip(original, result["grades"]):
                        entry["records"].append(
                            {
                                "id": row["id"],
                                "question_group": row["question"]["id"],
                                "draft_tier": row["draft_expected_total"],
                                "human_expected_total": row["human_expected_total"],
                                "score": g["correctness"] + g["completeness"] + g["reasoning"],
                                "grade": g,
                            }
                        )
                    entry["status"] = "completed"
                except Exception as e:
                    entry["error_type"] = type(e).__name__
                    if isinstance(e, ValueError):
                        entry["validation_message"] = str(e)[:500]
                entry["events"] = run["events"]
                entry.update(elapsed_seconds=round(time.perf_counter() - begin, 2), usage=run["usage"])
                report["batches"].append(entry)
                executed += 1
                summarize(report)
                save(path, report)
                print(f"SAVED {key}: {entry['status']} ({entry['elapsed_seconds']}s)", flush=True)
        failed = sum(b["status"] != "completed" for b in report["batches"])
        report["status"] = "completed_with_errors" if failed else "completed_awaiting_human_review"
        summarize(report)
        save(path, report)
    except BaseException:
        report["status"] = "interrupted"
        summarize(report)
        save(path, report)
        raise
    finally:
        await agent.llm.close()
        kb.close()
    return report


async def answer_job(config, path, split, case_ids=None):
    if not config.llm_ready:
        return {"status": "not_run", "reason": "LLM API is not configured"}
    from .evaluation import read_jsonl
    from .evaluation_review import reviewed_rows
    from .schemas import ChatInput

    all_rows = read_jsonl(path)
    if config.dataset == "sample" and split == "test":
        locked = [r for r in all_rows if r["split"] == "test"]
        actual = hashlib.sha256(json.dumps(locked, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        expected = json.loads((ROOT / "evals/test-lock.json").read_text())["test_sha256"]
        if actual != expected:
            raise ValueError("锁定测试集已变化，不可静默覆盖。")
    rows, _ = reviewed_rows(path, all_rows)
    rows = [r for r in rows if r["split"] == split]
    if case_ids:
        selected = set(case_ids)
        if selected - {r["id"] for r in rows}:
            raise ValueError("指定的题号不在所选集合中。")
        rows = [r for r in rows if r["id"] in selected]
    if not rows:
        raise ValueError("该集合没有可评测条目。")
    signature = {
        "model": config.llm_model,
        "cases": rows,
        "parameters": new_run(config, "scope")["generation_parameters"],
        "agent_sha": hashlib.sha256((ROOT / "backend/agents.py").read_bytes()).hexdigest(),
        "llm_sha": hashlib.sha256((ROOT / "backend/llm.py").read_bytes()).hexdigest(),
        "skill_sha": hashlib.sha256((ROOT / "skills/explain/SKILL.md").read_bytes()).hexdigest(),
    }
    fingerprint = hashlib.sha256(
        json.dumps(signature, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()[:16]
    checkpoint = config.runtime / "reports" / f"answers-{split}-{fingerprint}.json"
    report = (
        json.loads(checkpoint.read_text(encoding="utf-8"))
        if checkpoint.exists()
        else {
            "status": "in_progress_awaiting_human_claim_review",
            "fingerprint": fingerprint,
            "model": config.llm_model,
            "scope": signature,
            "dataset": config.dataset,
            "split": split,
            "records": [],
            "conditions": "one request at a time within this job; another evaluation job may share provider/GPU; not a single-concurrency performance acceptance",
        }
    )
    original = Knowledge(config)
    snapshot = config.runtime / "evaluation-snapshots" / fingerprint
    if not snapshot.exists():
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(original.index_path, snapshot, ignore=shutil.ignore_patterns(".lock", "*.lock"))
    kb = Knowledge(config, index_override=snapshot)
    store = Store(config.runtime / f"answers-{fingerprint}.sqlite3")
    agent = Agents(config, kb, store)
    try:
        completed = {r["id"] for r in report["records"]}
        for row in rows:
            if row["id"] in completed:
                continue
            sid = store.session(None, row.get("course_id"), row["query"])
            run = new_run(config, "answer_eval")
            begin = time.perf_counter()
            first = None
            answer = ""
            sources = []
            error = None
            print(f"ANSWER {row['id']} {len(report['records']) + 1}/{len(rows)}", flush=True)
            try:
                async for item in agent.chat(
                    ChatInput(message=row["query"], session_id=sid, course_id=row.get("course_id")), run
                ):
                    if item["event"] == "delta":
                        if first is None:
                            first = round((time.perf_counter() - begin) * 1000)
                        answer += item["text"]
                    elif item["event"] == "sources":
                        sources = [{"id": s["id"], "text": s["chunk_text"]} for s in item["sources"]]
            except Exception as e:
                error = type(e).__name__
            refs = set(re.findall(r"\[([A-Za-z][A-Za-z0-9_:-]*)\]", answer))
            report["records"].append(
                {
                    "id": row["id"],
                    "query": row["query"],
                    "category": row["category"],
                    "answer": answer,
                    "sources": sources,
                    "error": error,
                    "first_content_ms": first,
                    "elapsed_ms": round((time.perf_counter() - begin) * 1000),
                    "usage": run["usage"],
                    "events": run["events"],
                    "skill_versions": run["skill_versions"],
                    "rejected_citations": sum(
                        e.get("count", 0) for e in run["events"] if e["name"] == "citation_rejected"
                    ),
                    "citation_ids_valid": refs <= {s["id"] for s in sources},
                    "has_citation": bool(refs),
                    "credential_leaked": bool(config.llm_api_key and config.llm_api_key in answer),
                    "human_claims": [],
                    "human_correct_abstention": None,
                    "human_boundary_pass": None,
                }
            )
            save(checkpoint, report)
        report["status"] = "completed_awaiting_human_claim_review"
        report["automatic_checks"] = {
            "cases": len(report["records"]),
            "request_errors": sum(bool(r["error"]) for r in report["records"]),
            "invalid_citation_ids": sum(not r["citation_ids_valid"] for r in report["records"]),
            "credential_leaks": sum(r["credential_leaked"] for r in report["records"]),
            "learning_records_written": len(store.practices()) + len(store.reviews()),
            "rejected_citations": sum(r.get("rejected_citations", 0) for r in report["records"]),
        }
        save(checkpoint, report)
    except BaseException:
        report["status"] = "interrupted"
        save(checkpoint, report)
        raise
    finally:
        await agent.llm.close()
        kb.close()
    return report
