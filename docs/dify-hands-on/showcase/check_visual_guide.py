"""Check document integrity and data flow without contacting Dify or model APIs."""
import json
from pathlib import Path
import re

HERE = Path(__file__).resolve().parent


def main():
    data = json.loads((HERE / "visual-guide-data.json").read_text(encoding="utf-8"))
    nodes = {n["id"]: n["data"] for n in data["nodes"]}
    assert len(nodes) == 68 and len(data["edges"]) == 82
    assert len(data["notes"]) == 68
    standard = {
        "llm": {"text", "usage", "structured_output"}, "agent": {"text"},
        "knowledge-retrieval": {"result"}, "variable-aggregator": {"output"},
        "tool": {"text", "json"}, "start": {"query"},
    }

    def check_ref(ref):
        if ref[0] in ("sys", "conversation", "context"):
            return
        assert ref[0] in nodes, f"Unknown source node: {ref}"
        source = nodes[ref[0]]
        fields = set(source.get("outputs", {})) if source["type"] == "code" else standard.get(source["type"], set())
        assert len(ref) >= 2 and ref[1] in fields, f"Unknown source field: {ref}"

    def walk(value, key=""):
        if isinstance(value, dict):
            for k, v in value.items():
                walk(v, k)
        elif isinstance(value, list):
            if key.endswith("variable_selector") or key == "value_selector":
                if value:
                    check_ref(value)
            else:
                for v in value:
                    walk(v)
        elif isinstance(value, str):
            for ref in re.findall(r"\{\{#([^#]+)#\}\}", value):
                check_ref(ref.split("."))

    walk(data["nodes"])
    adj = {key: [] for key in nodes}
    indegree = {key: 0 for key in nodes}
    for edge in data["edges"]:
        assert edge["source"] in nodes and edge["target"] in nodes
        adj[edge["source"]].append(edge["target"])
        indegree[edge["target"]] += 1
    queue = [key for key in nodes if indegree[key] == 0]
    assert set(queue) == {"START", "TS"}
    seen = []
    while queue:
        key = queue.pop()
        seen.append(key)
        for target in adj[key]:
            indegree[target] -= 1
            if not indegree[target]:
                queue.append(target)
    assert len(seen) == 68, "Cycle or unreachable component"
    for node in nodes.values():
        if node["type"] == "knowledge-retrieval":
            cfg = node["multiple_retrieval_config"]
            assert cfg["reranking_enable"] is False and cfg["reranking_mode"] == "weighted_score"
        if node["type"] in ("llm", "question-classifier"):
            assert node["model"]["name"] == "kimi-k3"
            assert "temperature" not in node["model"]["completion_params"]
    page = (HERE / "课程助手-Dify可视化搭建手册.html").read_text(encoding="utf-8")
    assert "__GUIDE_DATA__" not in page
    assert not re.search(r'<(?:script|link|img)\b[^>]*(?:src|href)=["\']https?://', page)
    assert "fetch(" not in page and "XMLHttpRequest" not in page and "WebSocket(" not in page
    inline = re.search(r"<script>\s*(.*?)</script>", page, re.S)[1]
    # Syntax checked separately with node --check; this file is intentionally ignored.
    runtime = HERE.parents[2] / "runtime" / "dify-build"
    runtime.mkdir(parents=True, exist_ok=True)
    (runtime / "visual-guide-check.js").write_text(inline, encoding="utf-8")
    print("PASS: 68 nodes, 82 edges, valid data references, acyclic graphs, no API/network loading.")


if __name__ == "__main__":
    main()
