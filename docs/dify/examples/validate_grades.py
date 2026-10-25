"""Dify Code node example. The backend must repeat these checks before committing."""

import json


def main(grade_set: dict, expected_ids: list, source_ids: list) -> dict:
    grades = grade_set.get("grades")
    if not isinstance(grades, list) or len(grades) != 3 or len(set(expected_ids)) != 3:
        raise ValueError("评分条目数量不正确。")
    found = [g.get("question_id") for g in grades if isinstance(g, dict)]
    if len(found) != 3 or len(set(found)) != 3 or set(found) != set(expected_ids):
        raise ValueError("评分题号不匹配或重复。")
    allowed = set(source_ids)
    for grade in grades:
        for key in ("correctness", "completeness", "reasoning"):
            value = grade.get(key)
            if type(value) is not int or not 0 <= value <= 2:
                raise ValueError("分项评分必须为0、1或2。")
        refs = grade.get("source_ids")
        if not isinstance(refs, list) or not 1 <= len(refs) <= 6 or not set(refs) <= allowed:
            raise ValueError("评分引用了未暴露的课程证据。")
        for key in ("understood", "missing", "misconception", "suggestion"):
            if not isinstance(grade.get(key), str) or len(grade[key]) > 1500:
                raise ValueError("评分反馈字段不符合约定。")
    ordered = sorted(grades, key=lambda grade: expected_ids.index(grade["question_id"]))
    return {"valid": True, "grades_json": json.dumps({"grades": ordered}, ensure_ascii=False)}
