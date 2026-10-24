import hashlib
import json
import sqlite3

from pydantic import Field

from .config import ROOT
from .knowledge import Corpus
from .schemas import StrictModel
from .store import now


class ReviewInput(StrictModel):
    reviewer: str = Field(min_length=1, max_length=80)
    human_confirmed: bool
    query: str = Field(default="", max_length=2000)
    relevant_ids: list[str] = Field(default_factory=list, max_length=20)
    human_expected_total: int | None = Field(default=None, ge=0, le=6)
    notes: str = Field(default="", max_length=2000)


class EvaluationReview:
    def __init__(self, path=None):
        self.path = path or ROOT / "runtime/evaluation-review.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as c:
            c.execute(
                "CREATE TABLE IF NOT EXISTS annotations(set_id TEXT,case_id TEXT,base_hash TEXT,payload TEXT,PRIMARY KEY(set_id,case_id))"
            )

    def location(self, name):
        paths = {
            "sample-retrieval": ROOT / "evals/retrieval.jsonl",
            "sample-grading": ROOT / "evals/grading.jsonl",
            "private-retrieval": ROOT / "runtime/live-private/evals/retrieval.jsonl",
        }
        if name not in paths or not paths[name].exists():
            raise ValueError("评测集不存在。")
        return paths[name]

    def rows(self, name):
        path = self.location(name)
        base = hashlib.sha256(path.read_bytes()).hexdigest()
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        with sqlite3.connect(self.path) as c:
            updates = {
                r[0]: json.loads(r[1])
                for r in c.execute(
                    "SELECT case_id,payload FROM annotations WHERE set_id=? AND base_hash=?", (name, base)
                )
            }
        return [{**r, **updates.get(r["id"], {})} for r in rows]

    def sets(self):
        result = []
        for name in ("sample-retrieval", "sample-grading", "private-retrieval"):
            try:
                rows = self.rows(name)
            except ValueError:
                continue
            result.append(
                {
                    "id": name,
                    "count": len(rows),
                    "reviewed": sum(r.get("review_status") == "approved" for r in rows),
                }
            )
        return result

    def case(self, name, case_id):
        row = next((r for r in self.rows(name) if r["id"] == case_id), None)
        if not row:
            raise ValueError("评测条目不存在。")
        path = (
            ROOT / "chunk/all_course_chunks_clean_v1.csv"
            if name.startswith("private")
            else ROOT / "data/sample_courses.csv"
        )
        corpus = Corpus(path)
        ids = (
            row.get("relevant_ids")
            or row.get("candidate_source_ids")
            or row.get("question", {}).get("source_ids", [])
        )
        return {"case": row, "sources": [corpus.source(i) for i in ids if i in corpus.parents]}

    def save(self, name, case_id, data):
        row = self.case(name, case_id)["case"]
        if not data.human_confirmed:
            raise ValueError("只有实际人工审阅后才能标记通过。")
        payload = {
            "review_status": "approved",
            "reviewer": data.reviewer,
            "reviewed_at": now(),
            "review_notes": data.notes,
        }
        if name.endswith("grading"):
            if data.human_expected_total is None:
                raise ValueError("请填写人工总分。")
            payload["human_expected_total"] = data.human_expected_total
        else:
            if row["category"] == "answerable" and not data.relevant_ids:
                raise ValueError("可回答题必须标注相关来源。")
            if row["category"] != "answerable" and data.relevant_ids:
                raise ValueError("无答案/对抗题不应标成可检索答案。")
            path = (
                ROOT / "chunk/all_course_chunks_clean_v1.csv"
                if name.startswith("private")
                else ROOT / "data/sample_courses.csv"
            )
            corpus = Corpus(path)
            for i in data.relevant_ids:
                source = corpus.source(i)
                if row.get("course_id") and source["doc_id"] != row["course_id"]:
                    raise ValueError("来源超出题目的课程范围。")
            payload["relevant_ids"] = data.relevant_ids
            if name.startswith("private"):
                if not data.query.strip():
                    raise ValueError("请填写独立改写的问题。")
                payload["query"] = data.query.strip()
        base = hashlib.sha256(self.location(name).read_bytes()).hexdigest()
        with sqlite3.connect(self.path) as c:
            c.execute(
                "INSERT OR REPLACE INTO annotations VALUES (?,?,?,?)",
                (name, case_id, base, json.dumps(payload, ensure_ascii=False)),
            )
        return self.case(name, case_id)

    def source(self, name, source_id):
        self.location(name)
        path = (
            ROOT / "chunk/all_course_chunks_clean_v1.csv"
            if name.startswith("private")
            else ROOT / "data/sample_courses.csv"
        )
        return Corpus(path).source(source_id)


def reviewed_rows(path, base_rows):
    if not (ROOT / "runtime/evaluation-review.sqlite3").exists():
        return base_rows, None
    review = EvaluationReview()
    for name in ("sample-retrieval", "sample-grading", "private-retrieval"):
        try:
            location = review.location(name)
        except ValueError:
            continue
        if location.resolve() == path.resolve():
            rows = review.rows(name)
            version = hashlib.sha256(
                json.dumps(rows, sort_keys=True, ensure_ascii=False).encode()
            ).hexdigest()[:16]
            return rows, version
    return base_rows, None
