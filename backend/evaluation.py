import hashlib
import json
import math
import tempfile
import time
from pathlib import Path

import numpy as np

from .agents import Agents
from .config import ROOT
from .knowledge import Knowledge
from .llm import new_run
from .store import Store


def read_jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def retrieval_metrics(ranked, relevant):
    relevant = set(relevant)
    if not relevant:
        return None
    recall = len(set(ranked[:10]) & relevant) / len(relevant)
    dcg = sum(1 / math.log2(i + 2) for i, key in enumerate(ranked[:5]) if key in relevant)
    ideal = sum(1 / math.log2(i + 2) for i in range(min(len(relevant), 5)))
    return {"recall_at_10": recall, "ndcg_at_5": dcg / ideal}


def retrieval_eval(config, path, split):
    if not path.exists():
        return {
            "status": "not_run",
            "reason": "Evaluation questions are missing or not reviewed",
            "expected_path": str(path),
        }
    all_rows = read_jsonl(path)
    rows = [r for r in all_rows if r["split"] == split]
    if config.dataset == "sample" and split == "test":
        expected = json.loads((ROOT / "evals/test-lock.json").read_text())["test_sha256"]
        actual = hashlib.sha256(json.dumps(rows, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        if actual != expected:
            raise ValueError("锁定测试集已变化，请创建新版本并记录原因，不可静默覆盖。")
    kb = Knowledge(config)
    report = {
        "status": "exploratory",
        "human_reviewed": all(r.get("review_status") == "approved" for r in rows),
        "dataset": config.dataset,
        "dataset_version": kb.corpus.version,
        "split": split,
        "embedding_model": config.embedding_model,
        "rerank_model": config.rerank_model,
        "conditions": "single concurrency; warmed models; unanswerable queries excluded from retrieval metric denominators",
        "modes": {},
        "records": [],
    }
    try:
        kb.search("检索证据")
        for mode in ("bm25", "dense", "hybrid", "rerank"):
            metrics, latencies = [], []
            for row in rows:
                r = kb.search(row["query"], row.get("course_id"), limit=10, mode=mode)
                ids = [s["id"] for s in r["sources"]]
                score = retrieval_metrics(ids, row["relevant_ids"])
                if score:
                    metrics.append(score)
                latencies.append(r["elapsed_ms"])
                report["records"].append(
                    {
                        "id": row["id"],
                        "mode": mode,
                        "retrieved_ids": ids,
                        "elapsed_ms": r["elapsed_ms"],
                        "metrics": score,
                    }
                )
            report["modes"][mode] = {
                "answerable_count": len(metrics),
                "total_count": len(rows),
                "recall_at_10": float(np.mean([m["recall_at_10"] for m in metrics])) if metrics else None,
                "ndcg_at_5": float(np.mean([m["ndcg_at_5"] for m in metrics])) if metrics else None,
                "p50_ms": float(np.percentile(latencies, 50)),
                "p95_ms": float(np.percentile(latencies, 95)),
            }
        import torch

        report["device"] = kb.models.device
        report["peak_vram_mb"] = (
            round(torch.cuda.max_memory_allocated() / 1024**2, 1) if torch.cuda.is_available() else None
        )
    finally:
        kb.close()
    report["note"] = (
        "Draft labels are not a human-reviewed acceptance result. Test results must not be used for parameter selection."
    )
    return report


async def grading_eval(config):
    if not config.llm_ready:
        return {"status": "not_run", "reason": "LLM API is not configured", "human_review": "pending"}
    rows = read_jsonl(ROOT / "evals/grading.jsonl")
    report = {"status": "exploratory", "model": config.llm_model, "records": [], "modes": {}}
    kb = Knowledge(config)
    with tempfile.TemporaryDirectory(dir=config.runtime) as temp:
        agent = Agents(config, kb, Store(Path(temp) / "eval.sqlite3"))
        try:
            for dual in (False, True):
                mode = "two_agents" if dual else "single_coach"
                for start in range(0, len(rows), 3):
                    original = rows[start : start + 3]
                    batch = original + [original[-1]] * (3 - len(original))
                    questions = [{**r["question"], "id": f"q{i}"} for i, r in enumerate(batch)]
                    answers = {f"q{i}": r["answer"] for i, r in enumerate(batch)}
                    source_ids = {sid for q in questions for sid in q["source_ids"]}
                    p = {
                        "questions": questions,
                        "sources": [kb.corpus.source(s) for s in source_ids],
                        "course_id": None,
                        "topic": "",
                    }
                    run = new_run(config, f"eval_{mode}")
                    begun = time.perf_counter()
                    result = await agent.grade(p, answers, run, dual_agent=dual)
                    for row, g in zip(original, result["grades"]):
                        total = g["correctness"] + g["completeness"] + g["reasoning"]
                        report["records"].append(
                            {
                                "id": row["id"],
                                "mode": mode,
                                "score": total,
                                "human_expected_total": row["human_expected_total"],
                                "grade": g,
                                "batch_elapsed_ms": round((time.perf_counter() - begun) * 1000),
                                "batch_usage": run["usage"],
                            }
                        )
                valid = [
                    r
                    for r in report["records"]
                    if r["mode"] == mode and r["human_expected_total"] is not None
                ]
                report["modes"][mode] = {
                    "human_scored_count": len(valid),
                    "within_one_point": sum(abs(r["score"] - r["human_expected_total"]) <= 1 for r in valid)
                    / len(valid)
                    if valid
                    else None,
                }
        finally:
            await agent.llm.close()
            kb.close()
    return report


async def answer_eval(config, path, split):
    if not config.llm_ready:
        return {"status": "not_run", "reason": "LLM API is not configured"}
    from .schemas import ChatInput

    rows = [r for r in read_jsonl(path) if r["split"] == split]
    kb = Knowledge(config)
    report = {
        "status": "awaiting_human_claim_review",
        "model": config.llm_model,
        "split": split,
        "dataset_version": kb.corpus.version,
        "records": [],
    }
    with tempfile.TemporaryDirectory(dir=config.runtime) as temp:
        store = Store(Path(temp) / "answers.sqlite3")
        agent = Agents(config, kb, store)
        try:
            for row in rows:
                sid = store.session(None, row.get("course_id"), row["query"])
                run = new_run(config, "answer_eval")
                start = time.perf_counter()
                first = None
                answer, sources, error = "", [], ""
                try:
                    async for item in agent.chat(
                        ChatInput(message=row["query"], session_id=sid, course_id=row.get("course_id")), run
                    ):
                        if item["event"] == "delta":
                            if first is None:
                                first = (time.perf_counter() - start) * 1000
                            answer += item["text"]
                        elif item["event"] == "sources":
                            sources = [{"id": s["id"], "text": s["chunk_text"]} for s in item["sources"]]
                except Exception:
                    error = "request_failed"
                report["records"].append(
                    {
                        "id": row["id"],
                        "query": row["query"],
                        "category": row["category"],
                        "answer": answer,
                        "sources": sources,
                        "error": error,
                        "first_content_ms": first,
                        "elapsed_ms": round((time.perf_counter() - start) * 1000),
                        "usage": run["usage"],
                        "human_claims": [],
                        "human_correct_abstention": None,
                        "human_boundary_pass": None,
                    }
                )
        finally:
            await agent.llm.close()
            kb.close()
    return report
