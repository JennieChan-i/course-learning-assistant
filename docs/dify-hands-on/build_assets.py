"""Regenerate tutorial assets exclusively from the repository's public sample."""
import csv
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent


def obj(properties, required=None):
    return {"type": "object", "properties": properties,
            "required": required or list(properties), "additionalProperties": False}


def array(item, **limits):
    return {"type": "array", "items": item, **limits}


def text(**limits):
    return {"type": "string", **limits}


def main():
    knowledge = HERE / "knowledge"
    knowledge.mkdir(exist_ok=True)
    with (ROOT / "data/sample_courses.csv").open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    for row in rows:
        body = (f"来源编号：{row['chunk_id']}\n课程编号：{row['doc_id']}\n课程：{row['doc_name']}\n"
                f"主题：{row['subtopic']}\n资料性质：本项目自编演示课程，非完整私人课程\n\n正文：\n{row['chunk_text']}\n")
        (knowledge / f"{row['chunk_id']}.txt").write_text(body, encoding="utf-8")
    schemas = HERE / "schemas"
    schemas.mkdir(exist_ok=True)
    sid = array(text(), minItems=1, maxItems=6)
    question = obj({"id": text(enum=["1", "2", "3"]),
                    "type": text(enum=["short_answer", "scenario"]),
                    "prompt": text(minLength=1, maxLength=500),
                    "reference_answer": text(minLength=1, maxLength=800),
                    "rubric": obj({key: text(minLength=1, maxLength=400)
                                   for key in ("correctness", "completeness", "reasoning")}),
                    "source_ids": sid})
    score = obj({"question_id": text(enum=["1", "2", "3"]),
                 **{key: {"type": "integer", "minimum": 0, "maximum": 2}
                    for key in ("correctness", "completeness", "reasoning")},
                 "misconceptions": array(text(maxLength=200), maxItems=5),
                 "feedback": text(minLength=1, maxLength=1000), "source_ids": sid})
    values = {
        "questions": obj({"topic": text(minLength=1, maxLength=100),
                          "questions": array(question, minItems=3, maxItems=3)}),
        "audit": obj({"approved": {"type": "boolean"}, "issues": array(text(), maxItems=10)}),
        "grade": obj({"practice_id": text(), "items": array(score, minItems=3, maxItems=3)})
    }
    for name, value in values.items():
        (schemas / f"{name}.schema.json").write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    chapters = ["01-创建应用与产品边界.md", "02-知识库与引用答疑.md", "03-状态与语义识别.md",
                "04-练习与双角色评估.md", "05-记忆Skills与复习.md", "06-Agent工具与MCP.md", "07-调试评测与发布.md"]
    parts = ["# Dify 从 0 到 1：课程智能学习助手完整实操讲义\n\n"
             "我以已登录的 Dify Cloud 中文页面为起点，先搭可用的学习闭环，再验证 Agent 与 MCP。"
             "核查日期：2026-10-25。下面是连续阅读版，目录与状态说明见 [教程入口](README.md)。"
             "这是待学习者亲手搭建的教程；离线代码检查不代表云端应用已经验收。\n"]
    parts += [(HERE / f).read_text(encoding="utf-8") for f in chapters]
    parts += ["# 附录 A：可复制提示词\n", (HERE / "prompts.md").read_text(encoding="utf-8")]
    parts += ["# 附录 B：代码节点填写与全文\n", (HERE / "code/README.md").read_text(encoding="utf-8")]
    for filename in ("router.py", "evidence.py", "save_answer.py", "practice_gate.py", "commit_grade.py"):
        parts.append(f"## {filename}\n\n```python\n" + (HERE / "code" / filename).read_text(encoding="utf-8") + "```\n")
    parts.append("# 附录 C：结构化输出 Schema\n")
    for filename in ("questions.schema.json", "audit.schema.json", "grade.schema.json"):
        parts.append(f"## {filename}\n\n```json\n" + (schemas / filename).read_text(encoding="utf-8") + "```\n")
    parts += ["# 附录 D：产品经理工作簿\n", (HERE / "产品经理工作簿.md").read_text(encoding="utf-8")]
    # This appendix originally lives one folder deeper than the compiled lecture.
    parts = [part.replace("](router.py)", "](code/router.py)").replace("](evidence.py)", "](code/evidence.py)") for part in parts]
    (HERE / "完整实操讲义.md").write_text("\n\n---\n\n".join(parts), encoding="utf-8")
    print(f"Generated {len(rows)} public knowledge files, {len(values)} schemas and the combined lecture.")


if __name__ == "__main__":
    main()
