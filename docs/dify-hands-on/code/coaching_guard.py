"""Keep the coach from re-generating scores, dates or unknown citations."""
import json
import re


def main(text: str, validated_feedback: str) -> dict:
    view = json.loads(validated_feedback)
    allowed = {sid for item in view["items"] for sid in item["source_ids"]}
    cited = set(re.findall(r"\[([A-Za-z0-9_.:-]{1,80})\]", text))
    plain = re.sub(r"\[[A-Za-z0-9_.:-]{1,80}\]", "", text)
    if (len(text) > 450 or cited - allowed or re.search(
            r"[0-9０-９]|满分|总分|得分|成绩说明|参考答案|复习日期|下次复习|重排决定下限", plain)):
        return {"display": "建议先回看上方评估反馈中指出的具体误解，对照课程依据，用自己的话解释正确边界。再举一个不同的产品场景检验理解；到期后输入 /复习 进行变式练习。", "status": "fallback"}
    return {"display": text, "status": "passed"}
