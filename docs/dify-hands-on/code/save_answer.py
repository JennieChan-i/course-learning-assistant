"""Q05: reject unknown bracket IDs; retain only a short explicit dialogue window."""
import json
import re


def main(answer: str, source_ids: str, state_json: str, query: str) -> dict:
    allowed = set(json.loads(source_ids))
    cited = set(re.findall(r"\[([A-Za-z0-9_.:-]{1,80})\]", answer))
    if cited - allowed:
        return {"display": "本轮回答包含未提供的来源编号，已拦截。请换一种问法或检查检索结果。", "state": state_json}
    state = json.loads(state_json)
    state["history"] = (state.get("history", []) + [{"question": query[:500], "answer": answer[:1500]}])[-6:]
    return {"display": answer, "state": json.dumps(state, ensure_ascii=False)}
