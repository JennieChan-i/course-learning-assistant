"""P05: validate a question set and independent audit before saving pending work."""
import hashlib
import json


def main(draft: str, audit: str, evidence: str, state_json: str, review_topic: str = "") -> dict:
    state = json.loads(state_json)
    try:
        data, verdict, sources = json.loads(draft), json.loads(audit), json.loads(evidence)
        assert isinstance(data, dict) and isinstance(verdict, dict) and isinstance(sources, list), "JSON类型错误"
        assert verdict.get("approved") is True, "; ".join(verdict.get("issues", [])) or "审核未通过"
        assert isinstance(data.get("topic"), str) and 0 < len(data["topic"]) <= 100, "主题不合法"
        qs = data["questions"]
        assert isinstance(qs, list) and len(qs) == 3, "必须恰好三题"
        assert {q["id"] for q in qs} == {"1", "2", "3"}, "题号必须是字符串1、2、3"
        assert {q["type"] for q in qs} >= {"short_answer", "scenario"}, "缺少简答或场景题"
        allowed = {e["source_id"] for e in sources if not e.get("truncated")}
        for q in qs:
            assert q["type"] in {"short_answer", "scenario"}, "题型错误"
            assert isinstance(q["prompt"], str) and 0 < len(q["prompt"]) <= 500, "题干长度错误"
            assert isinstance(q["reference_answer"], str) and 0 < len(q["reference_answer"]) <= 800, "参考答案错误"
            assert isinstance(q["source_ids"], list) and q["source_ids"], "缺少证据"
            assert set(q["source_ids"]) <= allowed, "引用了未提供或截断的证据"
            assert set(q["rubric"]) == {"correctness", "completeness", "reasoning"}, "评分维度错误"
            assert all(isinstance(v, str) and 0 < len(v) <= 400 for v in q["rubric"].values()), "标准不完整"
        assert not state.get("pending"), "已有待交卷练习"
        topic = review_topic or data["topic"]
        serial = state.get("practice_serial", 0) + 1
        practice_id = hashlib.sha256((str(serial) + draft).encode()).hexdigest()[:16]
        state["practice_serial"] = serial
        state["pending"] = {"id": practice_id, "topic": topic, "questions": qs,
                            "evidence": sources, "is_review": bool(review_topic)}
        # Display only public questions, never the complete internal package.
        public = "本轮主题：" + topic + "\n\n" + "\n\n".join(q["id"] + ". " + q["prompt"] for q in sorted(qs, key=lambda q: q["id"]))
        public += "\n\n请使用以下格式提交：\n/作答\n1. 你的答案\n2. 你的答案\n3. 你的答案"
        return {"ok": "yes", "state": json.dumps(state, ensure_ascii=False), "display": public, "issues": ""}
    except (AssertionError, KeyError, TypeError, ValueError) as exc:
        return {"ok": "no", "state": state_json, "display": "题目未通过审核，没有保存待作答题。", "issues": str(exc)[:500]}
