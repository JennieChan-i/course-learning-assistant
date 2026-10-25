"""G03: validate scores, calculate totals, then update review state. Serial-chat prototype only."""
import json
from datetime import datetime, timedelta, timezone


def main(grade: str, state_json: str) -> dict:
    state = json.loads(state_json)
    original = state_json
    try:
        pending, data = state["pending"], json.loads(grade)
        assert pending and data["practice_id"] == pending["id"], "练习编号不匹配或已经交卷"
        items = data["items"]
        assert len(items) == 3 and {i["question_id"] for i in items} == {"1", "2", "3"}, "题号缺失或重复"
        questions = {q["id"]: q for q in pending["questions"]}
        errors, totals, passed = [], [], True
        for item in items:
            for dim in ("correctness", "completeness", "reasoning"):
                assert type(item[dim]) is int and 0 <= item[dim] <= 2, "分数必须为0–2整数"
            assert isinstance(item["feedback"], str) and 0 < len(item["feedback"]) <= 1000, "缺少有效反馈"
            assert isinstance(item["misconceptions"], list) and len(item["misconceptions"]) <= 5, "误解点格式错误"
            assert all(isinstance(x, str) and len(x) <= 200 for x in item["misconceptions"]), "误解点过长"
            assert isinstance(item["source_ids"], list) and item["source_ids"], "缺少评分证据"
            assert set(item["source_ids"]) <= set(questions[item["question_id"]]["source_ids"]), "评分引用越界"
            total = sum(item[d] for d in ("correctness", "completeness", "reasoning"))
            item["total"] = total
            totals.append(total)
            passed = passed and total >= 5 and item["correctness"] == 2
            errors.extend(item["misconceptions"])
        now = datetime.now(timezone(timedelta(hours=8))).date()
        topic = pending["topic"]
        reviews = state.setdefault("reviews", {})
        previous = reviews.get(topic)
        if not passed or previous:
            stage = previous.get("stage", 0) if previous else 0
            if not passed:
                stage = 0
            elif previous and previous.get("last_review_date") != now.isoformat():
                stage = min(stage + 1, 2)
            reviews[topic] = {"stage": stage, "due": (now + timedelta(days=[1, 3, 7][stage])).isoformat(),
                              "last_review_date": now.isoformat(), "misconceptions": list(dict.fromkeys(errors))[:6]}
        summary = {"practice_id": pending["id"], "topic": topic, "date": now.isoformat(),
                   "total": sum(totals), "max_total": 18, "passed_all": passed,
                   "review_due": reviews.get(topic, {}).get("due", "")}
        state["results"] = (state.get("results", []) + [summary])[-12:]
        state["pending"] = {}
        view = {"summary": summary, "items": items, "questions": pending["questions"]}
        serialized = json.dumps(state, ensure_ascii=False)
        assert len(serialized) < 70000, "会话状态过大，需要外部学习档案服务"
        lines = [f"本轮总分：{summary['total']}/18；主题：{topic}"]
        for item in sorted(items, key=lambda x: x["question_id"]):
            question = questions[item["question_id"]]
            lines.append(f"\n第{item['question_id']}题：{item['total']}/6"
                         f"（正确性{item['correctness']}、完整性{item['completeness']}、应用理由{item['reasoning']}，各满分2）")
            lines.append("评估反馈：" + item["feedback"])
            lines.append("参考答案：" + question["reference_answer"])
            lines.append("课程依据：" + " ".join("[" + source + "]" for source in item["source_ids"]))
        if summary["review_due"]:
            lines.append("\n下次复习：" + summary["review_due"] + "；采用首版1/3/7天规则。")
        return {"ok": "yes", "state": serialized, "feedback": json.dumps(view, ensure_ascii=False),
                "display": "\n".join(lines), "error": ""}
    except (AssertionError, KeyError, ValueError, TypeError) as exc:
        return {"ok": "no", "state": original, "feedback": "", "display": "", "error": str(exc)[:500]}
