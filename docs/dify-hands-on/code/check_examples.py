"""Offline rule checks, not an LLM quality or Dify integration evaluation."""
import csv
import importlib.util
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent


def load(name):
    spec = importlib.util.spec_from_file_location(name, HERE / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


router, evidence, gate, commit, save = [load(n) for n in
    ("router", "evidence", "practice_gate", "commit_grade", "save_answer")]


def dump(value):
    return json.dumps(value, ensure_ascii=False)


def draft():
    return {"topic": "RAG 的职责与边界", "questions": [
        {"id": str(i), "type": "scenario" if i == 3 else "short_answer",
         "prompt": "说明检索与生成的边界" + str(i),
         "reference_answer": "内部参考答案：RAG不自动保证正确。",
         "rubric": {k: "0分错误，1分部分正确，2分完整正确" for k in
                    ("correctness", "completeness", "reasoning")}, "source_ids": ["S01"]}
        for i in (1, 2, 3)]}


EVIDENCE = dump([{"source_id": "S01", "text": "自编证据：RAG不保证正确", "truncated": False}])


def pack(state=None, audit=None, data=None):
    return gate.main(dump(data or draft()), dump(audit or {"approved": True, "issues": []}), EVIDENCE,
                     dump(state or {"reviews": {}, "results": [], "history": [], "pending": {}}))


def grade_for(state, good=False):
    return {"practice_id": state["pending"]["id"], "items": [
        {"question_id": str(i), "correctness": 2 if good else 0, "completeness": 2 if good else 1,
         "reasoning": 2 if good else 0, "misconceptions": [] if good else ["误以为RAG保证正确"],
         "feedback": "依据课程判断", "source_ids": ["S01"]} for i in (1, 2, 3)]}


def run_grade(state, good=False, day=25):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, day, 12, tzinfo=timezone(timedelta(hours=8)))
    with patch.object(commit, "datetime", Clock):
        return commit.main(dump(grade_for(state, good)), dump(state))


class TutorialChecks(unittest.TestCase):
    def test_01_router_requires_pending(self):
        self.assertEqual(router.main("/作答", "{}")['action'], "notice")

    def test_02_preferences_deleted_from_context(self):
        first = router.main("/偏好 零基础", "{}")
        second = router.main("/删除偏好", first["state"])
        self.assertNotIn("零基础", second["context"])
        self.assertEqual(json.loads(second["state"])["history"], [])

    def test_03_public_pending_hides_answer(self):
        state = pack()["state"]
        result = router.main("第二题是什么意思", state)
        self.assertIn("pending_public_questions", json.loads(result["context"]))
        self.assertNotIn("内部参考答案", result["context"])

    def test_04_evidence_dedup(self):
        item = {"content": "来源编号：S01\n正文：课程样例"}
        out = evidence.main([item, item])
        self.assertEqual(json.loads(out["source_ids"]), ["S01"])
        self.assertNotIn("正文", out["catalog"])

    def test_05_audit_rejects_without_state_change(self):
        self.assertEqual(pack(audit={"approved": False, "issues": ["不可回答"]})["ok"], "no")

    def test_06_question_whitelist(self):
        data = draft()
        data["questions"][0]["source_ids"] = ["FAKE999"]
        self.assertEqual(pack(data=data)["ok"], "no")

    def test_07_question_display_hides_reference(self):
        out = pack()
        self.assertEqual(out["ok"], "yes")
        self.assertNotIn("内部参考答案", out["display"])

    def test_08_submit_format(self):
        state = pack()["state"]
        self.assertEqual(router.main("/作答\n1.甲\n2.乙", state)["action"], "notice")
        self.assertEqual(router.main("/作答\n1.甲\n2.乙\n3.丙", state)["action"], "submit")
        self.assertEqual(router.main("/作答\n1.甲\n1.乙\n3.丙", state)["action"], "notice")

    def test_09_invalid_grade_preserves_pending(self):
        state = json.loads(pack()["state"])
        data = grade_for(state)
        data["items"][0]["correctness"] = True
        out = commit.main(dump(data), dump(state))
        self.assertEqual(out["ok"], "no")
        self.assertEqual(json.loads(out["state"]), state)

    def test_10_total_and_serial_resubmit(self):
        state = json.loads(pack()["state"])
        out = run_grade(state, good=True)
        self.assertEqual(json.loads(out["feedback"])["summary"]["total"], 18)
        self.assertIn("本轮总分：18/18", out["display"])
        self.assertIn("内部参考答案", out["display"])
        self.assertEqual(router.main("/作答\n1.甲\n2.乙\n3.丙", out["state"])["action"], "notice")

    def test_11_review_intervals(self):
        state = json.loads(run_grade(json.loads(pack()["state"]), good=False)["state"])
        topic = draft()["topic"]
        self.assertEqual(state["reviews"][topic]["due"], "2026-10-26")
        state = json.loads(run_grade(json.loads(pack(state)["state"]), good=True, day=25)["state"])
        self.assertEqual(state["reviews"][topic]["stage"], 0)
        state = json.loads(run_grade(json.loads(pack(state)["state"]), good=True, day=26)["state"])
        self.assertEqual(state["reviews"][topic]["due"], "2026-10-29")
        state = json.loads(run_grade(json.loads(pack(state)["state"]), good=True, day=29)["state"])
        self.assertEqual(state["reviews"][topic]["due"], "2026-11-05")
        state = json.loads(run_grade(json.loads(pack(state)["state"]), good=False, day=30)["state"])
        self.assertEqual(state["reviews"][topic]["stage"], 0)

    def test_12_unknown_citation_not_saved(self):
        out = save.main("结论[FAKE999]", '["S01"]', "{}", "问题")
        self.assertEqual(out["state"], "{}")
        self.assertIn("拦截", out["display"])

    def test_13_search_cap_and_failure_accounting(self):
        state = "{}"
        for _ in range(10):
            out = router.main("/联网 Dify 官方文档", state)
            self.assertEqual(out["action"], "web")
            state = out["state"]  # No success callback: failed attempts still count.
        self.assertEqual(router.main("/联网 Dify 官方文档", state)["action"], "notice")

    def test_14_assets(self):
        samples = list((HERE.parent / "knowledge").glob("*.txt"))
        self.assertEqual(len(samples), 16)
        self.assertTrue(all(len(f.read_text(encoding="utf-8")) < 800 for f in samples))
        with (HERE.parent / "test-cards.csv").open(encoding="utf-8-sig", newline="") as stream:
            cards = list(csv.DictReader(stream))
        self.assertEqual(len(cards), 20)
        self.assertTrue(all(None not in row for row in cards))

    def test_15_malformed_audit(self):
        out = gate.main(dump(draft()), "[]", EVIDENCE, "{}")
        self.assertEqual(out["ok"], "no")


if __name__ == "__main__":
    unittest.main(verbosity=2)
