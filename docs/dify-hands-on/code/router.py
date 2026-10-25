"""Paste into Dify Python code node R01. No network, files, or third-party imports."""
import json
import re
from datetime import datetime, timedelta, timezone


def main(query: str, saved_state: str, scope: str = "all") -> dict:
    today = datetime.now(timezone(timedelta(hours=8))).date().isoformat()
    try:
        state = json.loads(saved_state or "{}")
        if not isinstance(state, dict):
            raise ValueError("state")
    except (ValueError, TypeError):
        return dict(action="notice", topic="", answers="{}", pending="{}", context="{}",
                    state=saved_state or "{}", notice="学习状态格式异常，请新建聊天；本轮没有调用模型。", review_topic="")
    for key, default in {"preferences": "", "pending": {}, "reviews": {}, "results": [],
                         "history": [], "web_attempts": 0, "scope": "all"}.items():
        state.setdefault(key, default)
    scope = scope or "all"
    if state["scope"] != scope:
        state.update(scope=scope, pending={}, history=[])
    q = query.strip()
    action, topic, answers, notice, review_topic = "qa", q, {}, "", ""
    if q.startswith("/偏好 "):
        state["preferences"] = q[len("/偏好 "):].strip()[:200]
        state["history"] = []
        action, notice = "notice", "已保存你明确提供的偏好；旧近期对话不再进入模型上下文。"
    elif q == "/删除偏好":
        state["preferences"], state["history"] = "", []
        action, notice = "notice", "已清空本会话偏好与近期上下文。聊天气泡和平台日志不由此删除。"
    elif q == "/退出练习":
        state["pending"] = {}
        action, notice = "notice", "已退出待作答练习，没有更新掌握状态。"
    elif q == "/记录":
        action = "notice"
        notice = "最近有效成绩（仅本会话）\n" + json.dumps(state["results"][-12:], ensure_ascii=False)
    elif q == "/复习":
        due = [(k, v) for k, v in state["reviews"].items() if v.get("due", "9999") <= today]
        due.sort(key=lambda item: item[1]["due"])
        if state["pending"]:
            action, notice = "notice", "还有待作答练习，请先 /作答 或 /退出练习。"
        elif due:
            review_topic, record = due[0]
            action, topic = "practice", review_topic
        else:
            action, notice = "notice", "今天没有到期复习；可输入 /练习 知识点 主动练习。"
    elif q.startswith("/练习"):
        topic = q[len("/练习"):].strip()
        if state["pending"]:
            action, notice = "notice", "已有待作答题，请先交卷或输入 /退出练习。"
        elif not topic:
            action, notice = "notice", "请指定一个知识点，例如 /练习 RAG 的职责与边界。"
        else:
            action = "practice"
    elif q.startswith("/作答"):
        action = "submit"
        if not state["pending"]:
            action, notice = "notice", "没有待作答题；重复交卷不会再次记成绩。"
        else:
            body = q[len("/作答"):].strip()
            parts = re.findall(r"(?ms)^([123])[.、．]\s*(.*?)(?=^[123][.、．]|\Z)", body)
            if len(parts) != 3 or {key for key, _ in parts} != {"1", "2", "3"}:
                action, notice = "notice", "请按三行题号交卷：1.答案、2.答案、3.答案；每题都要填写。"
            elif any(not value.strip() or len(value) > 2000 for _, value in parts):
                action, notice = "notice", "每题答案须为 1–2000 字；本轮未评分。"
            else:
                answers = {key: value.strip() for key, value in parts}
    elif q.startswith("/联网"):
        topic = q[len("/联网"):].strip()
        if not topic:
            action, notice = "notice", "请在 /联网 后写必要的公开检索词，不要填写私人课程或作答记录。"
        elif state["web_attempts"] >= 10:
            action, notice = "notice", "本会话已达 10 次搜索尝试上限，本轮没有联网。"
        else:
            state["web_attempts"] += 1
            action = "web"
    elif not q or q.startswith("/"):
        action, notice = "notice", "可直接问课程；或使用 /练习 知识点、/作答、/复习、/偏好 内容、/删除偏好、/记录、/退出练习、/联网 公开关键词。"
    context = {"preferences": state["preferences"], "recent_dialogue": state["history"][-6:],
               "course_scope": scope}
    if state["pending"]:
        context["pending_public_questions"] = [
            {"id": q["id"], "prompt": q["prompt"]} for q in state["pending"]["questions"]
        ]
    if review_topic:
        context["previous_misconceptions"] = state["reviews"][review_topic].get("misconceptions", [])
    return dict(action=action, topic=topic, answers=json.dumps(answers, ensure_ascii=False),
                pending=json.dumps(state["pending"], ensure_ascii=False),
                context=json.dumps(context, ensure_ascii=False),
                state=json.dumps(state, ensure_ascii=False), notice=notice, review_topic=review_topic)
