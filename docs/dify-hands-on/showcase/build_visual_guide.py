"""Build the standalone, offline visual guide from local DSL. Never calls Dify/LLMs."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import re
import yaml

from guide_content import NOTES, CHAPTERS, PREP, SOURCES

HERE = Path(__file__).resolve().parent


def public_node(node):
    data = deepcopy(node["data"])
    for key in ("selected", "provider_icon"):
        data.pop(key, None)
    if "dataset_ids" in data:
        data["dataset_ids"] = ["选择你自己的完整课程知识库"]
    config = data.get("multiple_retrieval_config", {})
    if config.get("reranking_enable") is False:
        config.pop("reranking_model", None)
    for case in data.get("cases", []):
        for condition in case.get("conditions", []):
            condition.pop("id", None)
    for prompt in data.get("prompt_template", []):
        prompt.pop("id", None)
    if data["type"] == "agent":
        for tool in data.get("agent_parameters", {}).get("tools", {}).get("value", []):
            if tool.get("type") == "workflow":
                tool["provider_name"] = "选择你发布的课程检索 Workflow 工具"
    return {"id": node["id"], "data": data,
            "originalY": node.get("position", {}).get("y", 0)}


def tool_graph(path):
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    nodes = doc["workflow"]["graph"]["nodes"]
    mapping = {n["id"]: {"start": "TS", "knowledge-retrieval": "TQ", "code": "TK", "end": "TE"}[n["data"]["type"]]
               for n in nodes}
    serialized = json.dumps(doc["workflow"]["graph"], ensure_ascii=False)
    for old, new in mapping.items():
        serialized = serialized.replace('"' + old + '"', '"' + new + '"')
        serialized = serialized.replace("#" + old + ".", "#" + new + ".")
    graph = json.loads(serialized)
    titles = {"TS": "TS_工具输入", "TQ": "TQ_完整课程检索", "TK": "TK_整理来源与正文", "TE": "TE_输出课程证据"}
    for n in graph["nodes"]:
        n["data"]["title"] = titles[n["id"]]
    return graph


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dsl", type=Path, nargs="?")
    parser.add_argument("--tool", type=Path)
    parser.add_argument("--from-data", type=Path, help="直接从公开脱敏 JSON 重建，不需要私有 DSL")
    args = parser.parse_args()
    if args.from_data:
        payload = json.loads(args.from_data.read_text(encoding="utf-8"))
        # Editorial notes remain versioned as Python source.
        payload.update(chapters=CHAPTERS, notes=NOTES, prep=PREP, sources=SOURCES)
        nodes, edges = payload["nodes"], payload["edges"]
    else:
        if not args.dsl or not args.tool:
            parser.error("提供 dsl 与 --tool，或使用 --from-data visual-guide-data.json")
        doc = yaml.safe_load(args.dsl.read_text(encoding="utf-8"))
        graph = doc["workflow"]["graph"]
        tool = tool_graph(args.tool)
        nodes = [public_node(n) for n in graph["nodes"] + tool["nodes"]]
        edges = [{"source": e["source"], "target": e["target"], "handle": e.get("sourceHandle", "source")}
                 for e in graph["edges"] + tool["edges"]]
        payload = {"nodes": nodes, "edges": edges, "chapters": CHAPTERS, "notes": NOTES,
                   "prep": PREP, "sources": SOURCES, "version": "2026-10-26 · 教学方案 v1",
                   "conversation": [{"name": v["name"], "type": v["value_type"], "default": v["value"]}
                                    for v in doc["workflow"]["conversation_variables"]]}
    ids = {n["id"] for n in nodes}
    assert len(ids) == len(nodes) == 68
    assert all(e["source"] in ids and e["target"] in ids for e in edges)
    assert all(n["id"] in NOTES for n in nodes)
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    assert not re.search(r"sk-[A-Za-z0-9]{16,}|tvly-[A-Za-z0-9]{10,}", raw)
    # Single-file HTML works on file://; no fetch, CDN, server or credentials needed.
    page = (HERE / "visual-guide.template.html").read_text(encoding="utf-8")
    page = page.replace("__GUIDE_DATA__", raw.replace("</", "<\\/"))
    output = HERE / "课程助手-Dify可视化搭建手册.html"
    output.write_text(page, encoding="utf-8")
    (HERE / "visual-guide-data.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Built offline guide: {len(nodes)} nodes / {len(edges)} connections / {output.stat().st_size:,} bytes")


if __name__ == "__main__":
    main()
