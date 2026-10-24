from backend.config import ROOT
from backend.evaluation import read_jsonl, retrieval_metrics


def test_retrieval_metrics_do_not_count_unknowns_as_success():
    assert retrieval_metrics(["x"], []) is None
    assert retrieval_metrics(["a", "b"], ["a", "b"])["recall_at_10"] == 1
    assert retrieval_metrics(["x", "a"], ["a"])["ndcg_at_5"] < 1


def test_group_split_and_human_review_truth():
    rows = read_jsonl(ROOT / "evals/retrieval.jsonl")
    assert len(rows) == 120
    dev = {r["group"] for r in rows if r["split"] == "dev"}
    test = {r["group"] for r in rows if r["split"] == "test"}
    assert dev.isdisjoint(test)
    assert sum(r["split"] == "dev" for r in rows) == 60
    assert all(r["review_status"] != "approved" for r in rows)
