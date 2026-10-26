"""Local feedback, human triage, immutable replay attempts and versioned regression export."""

import hashlib
import json
from typing import Literal

from pydantic import Field

from .observability import clean, finish_snapshot
from .schemas import StrictModel
from .store import now, uid

Reason = Literal[
    "accuracy", "citation", "unhelpful", "grading", "performance", "system_error", "format", "tool", "other"
]
Status = Literal["pending", "confirmed", "in_progress", "verification", "closed", "archived"]


class FeedbackInput(StrictModel):
    run_id: str = Field(min_length=1, max_length=80)
    reason: Reason
    comment: str = Field(default="", max_length=1500)


class CaseUpdate(StrictModel):
    status: Status
    severity: Literal["P0", "P1", "P2"] = "P1"
    category: Literal[
        "unknown",
        "data",
        "retrieval",
        "rerank",
        "context",
        "generation",
        "assessment",
        "tool",
        "runtime",
        "ux",
    ] = "unknown"
    expected_behavior: str = Field(default="", max_length=3000)
    notes: str = Field(default="", max_length=3000)
    fix_reference: str = Field(default="", max_length=120)
    reviewer: str = Field(min_length=1, max_length=80)
    human_confirmed: bool = False


class ReplayInput(StrictModel):
    mode: Literal["frozen_evidence", "current_knowledge"] = "frozen_evidence"
    request_id: str = Field(min_length=8, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")


class ReplayReview(StrictModel):
    verdict: Literal["pass", "fail"]
    notes: str = Field(min_length=1, max_length=3000)
    reviewer: str = Field(min_length=1, max_length=80)
    human_confirmed: bool


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


class Quality:
    transitions = {
        "pending": {"pending", "confirmed", "archived"},
        "confirmed": {"confirmed", "in_progress", "archived"},
        "in_progress": {"in_progress", "verification", "archived"},
        "verification": {"verification", "in_progress", "closed", "archived"},
        "closed": {"closed", "in_progress"},
        "archived": {"archived", "pending"},
    }

    def __init__(self, store, config):
        self.store, self.config = store, config

    def recover_interrupted(self):
        """Call once at server startup; a previous process cannot finish these attempts."""
        with self.store.connect() as c:
            for row in c.execute("SELECT id FROM badcases").fetchall():
                case = self.case(row[0], c)
                changed = False
                for entry in case["replays"]:
                    if entry["status"] == "running":
                        entry.update(
                            status="interrupted",
                            finished_at=now(),
                            error="服务重启中断了复测，请重新发起；不会自动重试API。",
                        )
                        changed = True
                        case["events"].append(
                            {"at": now(), "action": "replay_interrupted", "replay_id": entry["id"]}
                        )
                if changed:
                    self.write(c, case)

    def complete_run(self, run):
        run = finish_snapshot(run, self.config)
        self.store.trace(run)
        names = {event["name"] for event in run["events"]}
        reason = (
            "citation"
            if names & {"citation_rejected", "citation_missing"}
            else "format"
            if "schema_rejected" in names
            else "tool"
            if "tool_rejected" in names
            or any(e.get("warning") and e["name"] == "tool_call" for e in run["events"])
            else "system_error"
            if run["status"] == "failed"
            else None
        )
        if reason and not run.get("is_badcase_replay"):
            self.feedback(FeedbackInput(run_id=run["id"], reason=reason), automatic=True)

    def case(self, case_id, connection=None):
        if connection is None:
            with self.store.connect() as c:
                return self.case(case_id, c)
        row = connection.execute("SELECT * FROM badcases WHERE id=?", (case_id,)).fetchone()
        if not row:
            raise ValueError("问题案例不存在或已删除。")
        return {**dict(row), **json.loads(row["payload"]), "payload": None}

    @staticmethod
    def payload(case):
        return {
            k: v
            for k, v in case.items()
            if k
            not in (
                "id",
                "signature",
                "status",
                "severity",
                "category",
                "created_at",
                "updated_at",
                "payload",
            )
        }

    def write(self, c, case):
        c.execute(
            "UPDATE badcases SET status=?,severity=?,category=?,payload=?,updated_at=? WHERE id=?",
            (
                case["status"],
                case["severity"],
                case["category"],
                json.dumps(clean(self.payload(case), self.config), ensure_ascii=False),
                now(),
                case["id"],
            ),
        )

    def list(self, status=None):
        with self.store.connect() as c:
            ids = [row[0] for row in c.execute("SELECT id FROM badcases ORDER BY updated_at DESC")]
            result = []
            for case_id in ids:
                case = self.case(case_id, c)
                if status and case["status"] != status:
                    continue
                result.append(
                    {
                        k: case[k]
                        for k in (
                            "id",
                            "title",
                            "kind",
                            "reason",
                            "status",
                            "severity",
                            "category",
                            "created_at",
                            "updated_at",
                            "occurrences",
                            "context_redacted",
                        )
                    }
                )
        return result

    def detail(self, case_id):
        case = self.case(case_id)
        with self.store.connect() as c:
            case["feedback"] = [
                json.loads(row[0])
                for row in c.execute(
                    "SELECT payload FROM feedback WHERE case_id=? ORDER BY created_at", (case_id,)
                )
            ]
        if self.unsubmitted(case):
            case.update(
                snapshot={"answer_hidden": True}, original_output=None, replays=[], answer_hidden=True
            )
        return case

    def unsubmitted(self, case):
        practice_id = case.get("snapshot", {}).get("subject", {}).get("practice_id")
        if case["kind"] != "practice" or not practice_id:
            return False
        try:
            return self.store.practice(practice_id)["result"] is None
        except ValueError:
            return False

    def feedback(self, data, automatic=False):
        run = self.store.trace_run(data.run_id)
        snapshot = run.get("snapshot")
        if not snapshot or snapshot.get("redacted"):
            raise ValueError("这条历史记录没有可用的现场快照，请对新运行提交反馈。")
        feedback = clean(
            {
                "run_id": data.run_id,
                "reason": data.reason,
                "comment": data.comment.strip(),
                "origin": "automatic" if automatic else "user",
            },
            self.config,
        )
        encoded = json.dumps(feedback, ensure_ascii=False, sort_keys=True)
        signature = fingerprint(
            {
                k: snapshot.get(k)
                for k in ("kind", "request", "configuration", "memory", "dataset_version", "build")
            }
            | {"reason": data.reason, "error_type": run.get("error_type")}
        )
        with self.store.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            previous = c.execute(
                "SELECT id,case_id FROM feedback WHERE run_id=? AND payload=?", (data.run_id, encoded)
            ).fetchone()
            if previous:
                return {"feedback_id": previous[0], "case_id": previous[1], "duplicate": True}
            row = c.execute("SELECT id FROM badcases WHERE signature=?", (signature,)).fetchone()
            timestamp = now()
            if row:
                case_id = row[0]
                case = self.case(case_id, c)
                case["occurrences"] += 1
                case["run_ids"] = list(dict.fromkeys([*case["run_ids"], data.run_id]))
                if case["status"] == "closed":
                    case["status"] = "in_progress"
                    case["events"].append(
                        {"at": timestamp, "action": "reopened_by_feedback", "run_id": data.run_id}
                    )
                self.write(c, case)
            else:
                case_id = uid()
                request = snapshot.get("request", {})
                title = request.get("message") or request.get("topic") or "练习评分或生成问题"
                payload = {
                    "title": title[:100],
                    "kind": snapshot["kind"],
                    "reason": data.reason,
                    "run_ids": [data.run_id],
                    "occurrences": 1,
                    "snapshot": snapshot,
                    "original_status": run["status"],
                    "original_output": snapshot.get("output"),
                    "expected_behavior": "",
                    "notes": "",
                    "fix_reference": "",
                    "context_redacted": False,
                    "replays": [],
                    "events": [{"at": timestamp, "action": "reported", "origin": feedback["origin"]}],
                }
                c.execute(
                    "INSERT INTO badcases VALUES (?,?,?,?,?,?,?,?)",
                    (
                        case_id,
                        signature,
                        "pending",
                        "P1" if run["status"] == "failed" else "P2",
                        "unknown",
                        json.dumps(payload, ensure_ascii=False),
                        timestamp,
                        timestamp,
                    ),
                )
            feedback_id = uid()
            c.execute(
                "INSERT INTO feedback VALUES (?,?,?,?,?)",
                (feedback_id, data.run_id, case_id, encoded, timestamp),
            )
        return {"feedback_id": feedback_id, "case_id": case_id, "duplicate": False}

    @staticmethod
    def criteria(case):
        return fingerprint(
            {
                "expected_behavior": case["expected_behavior"],
                "category": case["category"],
                "fix_reference": case["fix_reference"],
            }
        )

    def update(self, case_id, data):
        if not data.human_confirmed or not data.reviewer.strip():
            raise ValueError("请由实际审阅者填写姓名并确认已人工核查；模型判断不能冒充人工审核。")
        with self.store.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            case = self.case(case_id, c)
            if data.status not in self.transitions[case["status"]]:
                raise ValueError("请按待核查、已确认、处理中、待验证、已关闭的顺序处理。")
            if case["context_redacted"]:
                raise ValueError("关联学习信息已删除，现场已清除，不能继续复测或确认。")
            if self.unsubmitted(case) and data.status not in ("pending", "archived"):
                raise ValueError("练习尚未提交，先完成作答；参考答案与出题上下文暂不开放。")
            if data.status not in ("pending", "archived") and (
                not data.expected_behavior.strip() or data.category == "unknown"
            ):
                raise ValueError("确认案例前请填写期望行为并选择归因类别。")
            if data.status in ("verification", "closed") and not data.fix_reference.strip():
                raise ValueError("进入验证前请填写修复提交或版本标识。")
            if data.status == "archived" and not data.notes.strip():
                raise ValueError("归档需说明符合预期、重复或信息不足等原因。")
            updated = {**case, **data.model_dump(exclude={"reviewer", "human_confirmed"})}
            if data.status == "closed":
                latest = case["replays"][-1] if case["replays"] else {}
                if (
                    latest.get("status") != "completed"
                    or latest.get("criteria") != self.criteria(updated)
                    or (latest.get("review") or {}).get("verdict") != "pass"
                    or not latest.get("checks", {}).get("execution_succeeded")
                    or not all(latest.get("checks", {}).values())
                ):
                    raise ValueError(
                        "关闭前必须完成与当前标准/修复版本匹配的复测，并由人工确认通过；失败或未审核不能关闭。"
                    )
                if snapshot_requires_live(case) and latest.get("mode_label") == "demo":
                    raise ValueError("演示回放不能关闭真实模式的质量案例。")
                if (
                    updated["category"] in ("data", "retrieval", "rerank")
                    and latest.get("strategy") != "current_knowledge"
                ):
                    raise ValueError("数据、召回或排序问题须用当前知识库复测；固定资料池不验证检索改进。")
            updated["events"].append(
                {
                    "at": now(),
                    "action": "status_update",
                    "from": case["status"],
                    "to": data.status,
                    "reviewer": data.reviewer,
                    "notes": data.notes,
                    "before": {
                        k: case[k] for k in ("category", "severity", "expected_behavior", "fix_reference")
                    },
                    "after": {
                        k: updated[k] for k in ("category", "severity", "expected_behavior", "fix_reference")
                    },
                }
            )
            self.write(c, updated)
        return self.detail(case_id)

    def begin_replay(self, case_id, request):
        with self.store.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            case = self.case(case_id, c)
            if self.unsubmitted(case):
                raise ValueError("练习尚未提交，暂不能复测或导出答案相关上下文。")
            if case["context_redacted"] or case["status"] not in ("confirmed", "in_progress", "verification"):
                raise ValueError("先人工确认案例，再进行隔离复测。")
            previous = next((r for r in case["replays"] if r["request_id"] == request.request_id), None)
            if previous:
                return previous, False
            entry = {
                "id": uid(),
                "request_id": request.request_id,
                "strategy": request.mode,
                "status": "running",
                "created_at": now(),
                "criteria": self.criteria(case),
                "review": None,
            }
            case["replays"].append(entry)
            case["events"].append(
                {"at": now(), "action": "replay_started", "replay_id": entry["id"], "strategy": request.mode}
            )
            self.write(c, case)
            return entry, True

    def finish_replay(self, case_id, replay_id, result):
        with self.store.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            case = self.case(case_id, c)
            entry = next(r for r in case["replays"] if r["id"] == replay_id)
            entry.update(clean(result, self.config), finished_at=now())
            case["events"].append(
                {"at": now(), "action": "replay_finished", "replay_id": replay_id, "status": entry["status"]}
            )
            self.write(c, case)
        return entry

    def review_replay(self, case_id, replay_id, data):
        if not data.human_confirmed or not data.reviewer.strip():
            raise ValueError("复测语义结果需要实际人工审阅确认。")
        with self.store.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            case = self.case(case_id, c)
            entry = next((r for r in case["replays"] if r["id"] == replay_id), None)
            if not entry or entry["status"] != "completed":
                raise ValueError("只有已完成的复测才能人工审核。")
            if entry["criteria"] != self.criteria(case):
                raise ValueError("期望行为或修复版本已变化，请重新复测。")
            if data.verdict == "pass" and (
                not entry.get("checks", {}).get("execution_succeeded")
                or not all(entry.get("checks", {}).values())
            ):
                raise ValueError("结构或隔离检查未通过，不能标为通过。")
            entry["review"] = {**data.model_dump(exclude={"human_confirmed"}), "reviewed_at": now()}
            entry.setdefault("review_history", []).append(dict(entry["review"]))
            case["events"].append(
                {
                    "at": now(),
                    "action": "replay_review",
                    "replay_id": replay_id,
                    "reviewer": data.reviewer,
                    "verdict": data.verdict,
                }
            )
            self.write(c, case)
        return self.detail(case_id)

    def export(self, case_id):
        case = self.case(case_id)
        if self.unsubmitted(case):
            raise ValueError("练习尚未提交，暂不能导出参考答案。")
        if (
            case["context_redacted"]
            or case["status"] in ("pending", "archived")
            or not case["expected_behavior"]
        ):
            raise ValueError("只允许导出已人工确认且保留有效现场的案例。")
        row = {
            "schema": "course-assistant.badcase.v1",
            "id": case_id,
            "split": "regression",
            "case_revision": self.criteria(case),
            "expected_behavior": case["expected_behavior"],
            "category": case["category"],
            "snapshot": case["snapshot"],
            "original_output": case["original_output"],
            "annotation": "human_confirmed_expectation; not an automatic correctness label",
            "exported_at": now(),
        }
        return {
            "filename": f"badcase-{case_id}-{fingerprint(row)[:10]}.jsonl",
            "content": json.dumps(clean(row, self.config), ensure_ascii=False) + "\n",
        }

    def delete(self, case_id):
        with self.store.connect() as c:
            self.case(case_id, c)
            c.execute("DELETE FROM feedback WHERE case_id=?", (case_id,))
            c.execute("DELETE FROM badcases WHERE id=?", (case_id,))
        return {"deleted": True}


def snapshot_requires_live(case):
    return case["snapshot"].get("mode") == "live"
