import hashlib

import pytest

from backend.config import ROOT
from backend.evaluation_review import EvaluationReview, ReviewInput
from backend.evaluation_jobs import summarize


def test_human_annotations_are_separate_and_validate_sources(tmp_path):
    path = ROOT / "evals/retrieval.jsonl"
    original = hashlib.sha256(path.read_bytes()).hexdigest()
    review = EvaluationReview(tmp_path / "review.sqlite3")
    with pytest.raises(ValueError, match="实际人工"):
        review.save(
            "sample-retrieval",
            "E001",
            ReviewInput(reviewer="test fixture", human_confirmed=False, relevant_ids=["S01"]),
        )
    with pytest.raises(ValueError, match="不存在"):
        review.save(
            "sample-retrieval",
            "E001",
            ReviewInput(reviewer="test fixture", human_confirmed=True, relevant_ids=["FAKE"]),
        )
    review.save(
        "sample-retrieval",
        "E001",
        ReviewInput(reviewer="test fixture", human_confirmed=True, relevant_ids=["S01"]),
    )
    assert review.rows("sample-retrieval")[0]["review_status"] == "approved"
    assert hashlib.sha256(path.read_bytes()).hexdigest() == original
    assert (
        EvaluationReview(tmp_path / "review.sqlite3").rows("sample-retrieval")[0]["reviewer"]
        == "test fixture"
    )


def test_grading_requires_an_actual_score(tmp_path):
    review = EvaluationReview(tmp_path / "review.sqlite3")
    with pytest.raises(ValueError, match="人工总分"):
        review.save("sample-grading", "A01", ReviewInput(reviewer="test fixture", human_confirmed=True))
    review.save(
        "sample-grading",
        "A01",
        ReviewInput(reviewer="test fixture", human_confirmed=True, human_expected_total=0),
    )
    assert review.rows("sample-grading")[0]["human_expected_total"] == 0


def test_evaluation_counts_batch_usage_once_and_does_not_invent_gold():
    report = {
        "batches": [
            {
                "mode": "single_coach",
                "status": "completed",
                "elapsed_seconds": 2,
                "usage": {"prompt_tokens": 100, "completion_tokens": 20},
                "records": [
                    {"id": "A01", "score": 2, "human_expected_total": None},
                    {"id": "A02", "score": 4, "human_expected_total": None},
                ],
            }
        ]
    }
    summarize(report)
    assert report["modes"]["single_coach"]["usage"]["prompt_tokens"] == 100
    assert report["modes"]["single_coach"]["within_one_point"] is None
    assert report["score_disagreement"]["paired_cases"] == 0
