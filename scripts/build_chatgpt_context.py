"""Package curated project context as seven Markdown files, without API calls."""
import csv
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
import zipfile

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
CONTEXT = ROOT / "docs/chatgpt-context"
SHOWCASE = ROOT / "docs/dify-hands-on/showcase"
OUT = ROOT / "runtime/share/chatgpt-project-context"


def digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    source_hashes = {}

    def read(name):
        text = (ROOT / name).read_text(encoding="utf-8-sig").replace("\r\n", "\n")
        source_hashes[name] = digest(text)
        return text

    files = {}
    for name in ("00-先读我与开场提示词.md", "01-本地项目架构与Agent逻辑.md", "02-Dify复刻映射与模型变更.md"):
        files[name] = read("docs/chatgpt-context/" + name)

    model_note = (
        "> **最新模型优先级：**用户已报告默认 LLM 为 XingChenAGI/Xing4.0-29B，"
        "Embedding 为 BAAI/bge-m3，Rerank 为 BAAI/bge-reranker-v2-m3。"
        "下文 kimi-k3/qwen3.7 是历史教学配置，不代表当前默认或节点已切换。"
        "迁移检查请先读 02；不把 Kimi 的参数限制或旧实验结果套用到新模型。\n\n"
    )
    public = json.loads(read("docs/dify-hands-on/showcase/visual-guide-data.json"))
    assert len(public["nodes"]) == 68 and len(public["edges"]) == 82
    nodes = {n["id"]: n["data"] for n in public["nodes"]}
    chapter = ["# Dify 全部节点参数与连线\n\n", model_note,
               "本附件是配置参考，不是在线实时导出或已通过验收的证明。主流程 64 节点、79 边；只读工具另 4 节点、3 边。"
               "节点手册中的变量表达式必须在自己的 Dify 重新绑定。原始材料的相对链接指向仓库，不保证附件内存在目标。\n\n",
               read("docs/dify-hands-on/showcase/逐节点参数手册.md")]
    chapter.append("\n\n# 补充：只读检索 Workflow 的四个节点\n")
    for key in ("TS", "TQ", "TK", "TE"):
        chapter.append(f"\n## {nodes[key]['title']}\n\n完整参数（代码也在 code 字段中）：\n\n```json\n"
                       + json.dumps(nodes[key], ensure_ascii=False, indent=2) + "\n```\n")
    chapter.append("\n# 完整连线清单（82 条）\n\n|来源节点|出口|目标节点|\n|---|---|---|\n")
    for edge in public["edges"]:
        chapter.append(f"|{nodes[edge['source']]['title']}|{edge['handle']}|{nodes[edge['target']]['title']}|\n")
    chapter.append("\nsource=默认出口；fail-branch=异常分支；false=ELSE；分类节点的 explain/compare/practice/clarify 与条件分支 CASE 必须从对应端口连接。\n")
    files["03-Dify全部节点参数与连线.md"] = "".join(chapter)

    evidence = ["# 我的产品决策、历史证据与未完成验证\n\n", model_note,
                "## 当前判断\n\n本地可运行、可进入个人内测，但完整人工 gold、业务质量、时延和商业效果没有全部验收。"
                "Dify 只有旧配置下的部分链路证据；新默认模型组合未实际验证。本次打包没有发起 AI 或搜索请求。\n\n"
                "下列材料按原版本保留：冻结档案中“Dify 尚为备选”是 2026-10-25 的历史状态，"
                "后续 2026-10-26 建立了草稿并完成部分检查，以最新 Dify 记录和 02 为准。"
                "原始材料的相对链接可在公开仓库查阅，不全包含在附件中。\n"]
    for name in ("docs/decisions.md", "docs/releases/v1.0/README.md", "docs/limitations.md",
                 "docs/dify-hands-on/showcase/验证记录.md"):
        evidence.append(f"\n\n---\n\n## 原始材料：{name}\n\n" + read(name))
    files["04-决策与验证证据.md"] = "".join(evidence)

    code = ["# 关键源码与三个应用 Skills\n\n"
            "这些是本地实际源码，不是 Dify 代码执行节点。Dify 节点代码在 03。"
            "本包没有读取当前 .env 配置；配置类的默认值不能当成当前运行参数。"
            "引用实现细节时请注明原始文件和函数，不假定这里的代码已经在当前模型服务上运行通过。\n"]
    names = [f"backend/{name}.py" for name in
             ("agents", "llm", "schemas", "knowledge", "store", "websearch", "main", "config", "evaluation")]
    for name in names:
        code.append(f"\n\n## 原始文件：{name}\n\n```python\n{read(name).rstrip()}\n```\n")
    for skill in ("explain", "practice", "review"):
        for filename in ("SKILL.md", "manifest.json"):
            name = f"skills/{skill}/{filename}"
            lang = "markdown" if filename.endswith(".md") else "json"
            fence = "````" if lang == "markdown" else "```"
            code.append(f"\n\n## 原始文件：{name}\n\n{fence}{lang}\n{read(name).rstrip()}\n{fence}\n")
    code.append("\n## 项目许可证\n\n```text\n" + read("LICENSE").rstrip() + "\n```\n")
    files["05-关键源码与应用Skills.md"] = "".join(code)

    teaching = ["# Dify 操作步骤与每个节点的产品思考\n\n", model_note]
    for prep in public["prep"]:
        teaching.append(f"\n## 准备：{prep['title']}\n\n{prep['goal']}\n\n")
        teaching.extend(f"{i}. {step}\n\n" for i, step in enumerate(prep["steps"], 1))
        teaching.append("参数对照：\n\n|项目|内容|\n|---|---|\n")
        teaching.extend(f"|{k}|{v.replace(chr(10), '<br>')}|\n" for k, v in prep["fields"])
        teaching.append(f"\n产品思考：{prep['pm']}\n\n验收：{prep['check']}\n")
    teaching.append("\n# 每个节点的用户问题、决策与检查\n")
    for node in public["nodes"]:
        key = node["id"]
        purpose, rationale, check = public["notes"][key]
        teaching.append(f"\n## {node['data']['title']}\n\n- 用户问题：{purpose}\n- 我的决策：{rationale}\n- 复刻后检查：{check}\n")
    teaching.append("\n# 官方说明\n\n")
    teaching.extend(f"- [{title}]({url})\n" for title, url in public["sources"])
    files["06-Dify操作步骤与节点产品思考.md"] = "".join(teaching)

    # Validate the actual transfer payload, including ignored output files.
    env = dotenv_values(ROOT / ".env") if (ROOT / ".env").exists() else {}
    secrets = [env.get(key) for key in ("LLM_API_KEY", "TAVILY_API_KEY")]
    secrets = [value for value in secrets if value and len(value) >= 8]
    private = ROOT / "chunk/all_course_chunks_clean_v1.csv"
    snippets = []
    if private.exists():
        with private.open(encoding="utf-8-sig", newline="") as stream:
            snippets = [row["chunk_text"][:100] for row in csv.DictReader(stream)
                        if len(row.get("chunk_text", "")) >= 100]
    for name, text in files.items():
        if any(secret in text for secret in secrets):
            raise ValueError(f"Credential detected in {name}; package not written")
        if any(part in text for part in snippets):
            raise ValueError(f"Private course excerpt detected in {name}; package not written")
        if re.search(r"(?:sk-[A-Za-z0-9_-]{20,}|tvly-[A-Za-z0-9_-]{20,}|ghp_[A-Za-z0-9]{20,})", text):
            raise ValueError(f"Potential credential in {name}; package not written")
    git_head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True,
                              text=True, check=True).stdout.strip()
    stamp = datetime.now(timezone(timedelta(hours=8)))
    manifest = {"created_at": stamp.isoformat(), "source_git_head": git_head,
                "note": "Working-tree source hashes are authoritative; Git HEAD alone may predate new packaging docs.",
                "files": {name: {"bytes": len(text.encode('utf-8')), "sha256": digest(text)}
                          for name, text in files.items()},
                "source_sha256_lf_normalized": source_hashes,
                "privacy_check": "No configured keys or matching first-100-character private course excerpts detected.",
                "models": {"user_reported_default_llm": "XingChenAGI/Xing4.0-29B",
                           "user_reported_default_embedding": "BAAI/bge-m3",
                           "user_reported_default_rerank": "BAAI/bge-reranker-v2-m3",
                           "node_and_index_migration": "unverified"}}
    for name, text in files.items():
        (OUT / name).write_text(text, encoding="utf-8", newline="\n")
    (OUT / "MANIFEST.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
    archive = OUT.parent / f"课程助手-ChatGPT上下文包-{stamp:%Y-%m-%d}.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for name in [*files, "MANIFEST.json"]:
            z.write(OUT / name, arcname=name)
    with zipfile.ZipFile(archive) as z:
        assert z.testzip() is None
        assert len(z.namelist()) == 8
        for name, meta in manifest["files"].items():
            assert hashlib.sha256(z.read(name)).hexdigest() == meta["sha256"]
    print(f"PASS: 7 Markdown files + manifest, ZIP CRC and SHA-256 verified; {archive.stat().st_size:,} bytes")
    print(archive.relative_to(ROOT))
    print(OUT.relative_to(ROOT))


if __name__ == "__main__":
    main()
