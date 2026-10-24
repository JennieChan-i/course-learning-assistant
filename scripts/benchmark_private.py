"""Measure retrieval latency without treating unreviewed questions as quality labels."""

import json
import statistics

import torch

from backend.config import Settings
from backend.knowledge import Knowledge


def main():
    config = Settings(app_mode="live", dataset="private")
    kb = Knowledge(config)
    try:
        questions = [r["subtopic"] for r in kb.corpus.rows][::29][:30]
        for q in questions[:3]:
            kb.search(q)
        times = [kb.search(q)["elapsed_ms"] for q in questions]
        ordered = sorted(times)
        report = {
            "dataset_version": kb.corpus.version,
            "parent_count": len(kb.corpus.rows),
            "child_count": len(kb.children),
            "query_count": len(times),
            "conditions": "warm, single process and request; topic-derived queries, no gold labels",
            "p50_ms": statistics.median(times),
            "p95_ms": ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))],
            "max_ms": max(times),
            "peak_vram_mb": round(torch.cuda.max_memory_allocated() / 1024**2, 1),
            "quality_metrics": "not_run: independent human labels pending",
        }
        path = config.runtime / "reports/private-latency.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        # Verify every child really maps back to the original parent, without exporting text.
        invalid = [
            c["id"]
            for c in kb.children
            if kb.corpus.parents[c["parent_id"]]["chunk_text"][c["start"] : c["end"]] != c["text"]
        ]
        print(json.dumps({**report, "invalid_offsets": len(invalid)}, indent=2))
    finally:
        kb.close()


if __name__ == "__main__":
    main()
