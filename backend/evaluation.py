import hashlib
import json
import math

import numpy as np

from .config import ROOT
from .knowledge import Knowledge


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
    from .evaluation_review import reviewed_rows

    merged, review_version = reviewed_rows(path, all_rows)
    rows = [r for r in merged if r["split"] == split]
    if not rows:
        raise ValueError("该集合没有可评测条目。")
    kb = Knowledge(config)
    report = {
        "status": "exploratory",
        "human_reviewed": all(r.get("review_status") == "approved" for r in rows),
        "dataset": config.dataset,
        "dataset_version": kb.corpus.version,
        "split": split,
        "review_version": review_version,
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


async def grading_eval(config, max_batches=None, retry_report=None):
    from .evaluation_jobs import grading_job

    return await grading_job(config, max_batches, retry_report)


async def answer_eval(config, path, split, case_ids=None):
    from .evaluation_jobs import answer_job

    return await answer_job(config, path, split, case_ids)
