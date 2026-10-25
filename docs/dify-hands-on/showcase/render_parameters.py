"""Render every node's UI settings and full parameters from a UI-exported DSL."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import yaml


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dsl", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--knowledge-name", default="Course_Learning_Full_qwen3_7_v1")
    args = parser.parse_args()
    document = yaml.safe_load(args.dsl.read_text(encoding="utf-8"))
    graph = document["workflow"]["graph"]
    nodes, edges = graph["nodes"], graph["edges"]
    by_id = {n["id"]: n["data"]["title"] for n in nodes}

    def selector(value):
        if not value:
            return "不绑定"
        return by_id.get(value[0], {"sys": "系统变量", "conversation": "会话变量"}.get(value[0], value[0])) + " / " + "/".join(value[1:])

    def pretty(value):
        return json.dumps(value, ensure_ascii=False, indent=2)

    public = deepcopy(document)
    for node in public["workflow"]["graph"]["nodes"]:
        data = node["data"]
        if "dataset_ids" in data:
            data["dataset_ids"] = ["选择你工作空间中的" + args.knowledge_name]
        if data.get("multiple_retrieval_config", {}).get("reranking_enable") is False:
            data["multiple_retrieval_config"].pop("reranking_model", None)
        if data["type"] == "agent":
            for tool in data.get("agent_parameters", {}).get("tools", {}).get("value", []):
                if tool.get("type") == "workflow":
                    tool["provider_name"] = "选择你发布的课程证据检索工具"
        data.pop("provider_icon", None)
    public_nodes = {node["id"]: node["data"] for node in public["workflow"]["graph"]["nodes"]}
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "nodes-parameters.json").write_text(pretty(public_nodes), encoding="utf-8")
    sections = ["# 我的 Dify 教学方案：逐节点参数手册\n\n"
                "本文件由本地教学方案 DSL 生成，包含未全部实测的修正参数，不是当前云端草稿的实时镜像。"
                "可交互查看[仿 Dify 画布手册](课程助手-Dify可视化搭建手册.html)。每节对应一个节点，列出界面填写项、完整提示词或代码、变量绑定和连线。"
                "完整机器参数见[nodes-parameters.json](nodes-parameters.json)，其中工作空间专属ID已用中文标记替代，密钥不在文档中。\n\n"
                "变量必须使用Dify选择器插入；这里的`{{#节点ID.字段#}}`是DSL变量表达式，手工搭建时按节点名称选择同一个字段。"
                "`系统变量 / query`就是当前用户消息；`会话变量 / learning_state`是当前聊天状态。\n\n"
                "通用约束：不配置自动聊天Memory、不启用视觉/文件上传；教练与评估员独立上下文；模型或工具失败不自动重试。"
                "少数字段省略表示采用平台默认值，以对应JSON为准。\n"]
    sections.append("## 节点目录\n\n|ID|节点|类型|\n|---|---|---|\n" +
                    "\n".join(f"|{n['id']}|{n['data']['title']}|{n['data']['type']}|" for n in nodes))
    for n in nodes:
        key, data = n["id"], n["data"]
        lines = [f"## {key} · {data['title']}", f"类型：`{data['type']}`；说明：{data.get('desc', '')}"]
        incoming = [f"{by_id[e['source']]}（出口 {e.get('sourceHandle', 'source')}）" for e in edges if e["target"] == key]
        outgoing = [f"{e.get('sourceHandle', 'source')} → {by_id[e['target']]}" for e in edges if e["source"] == key]
        lines += ["上游：" + ("；".join(incoming) or "流程起点"), "下游：" + ("；".join(outgoing) or "本分支结束")]
        kind = data["type"]
        if kind == "code":
            lines.append("### 输入变量\n\n|名称|绑定来源|类型|\n|---|---|---|\n" + "\n".join(
                f"|{v['variable']}|{selector(v.get('value_selector'))}|{v.get('value_type', '由上游推导')}|" for v in data.get("variables", [])))
            lines.append("### Python代码（整段复制）\n\n```python\n" + data["code"] + "\n```")
            lines.append("输出变量：" + "；".join(f"`{k}`：{v['type']}" for k, v in data["outputs"].items()))
        elif kind == "llm":
            lines.append("### 模型与上下文\n\n```json\n" + pretty({"model": data["model"], "context": data.get("context"),
                         "memory": data.get("memory", "关闭/未配置"), "vision": data.get("vision"),
                         "structured_output_enabled": data.get("structured_output_enabled", False)}) + "\n```")
            for prompt in data.get("prompt_template", []):
                lines.append("### " + prompt.get("role", "prompt").upper() + "\n\n```text\n" + prompt["text"] + "\n```")
            if data.get("structured_output_enabled"):
                lines.append("### 结构化输出Schema\n\n```json\n"+pretty(data["structured_output"]["schema"])+"\n```")
        elif kind == "agent":
            lines.append("Agent策略：`"+data.get("agent_strategy_provider_name", "")+" / "+data.get("agent_strategy_name", "")+"`；Memory关闭。")
            for name, value in public_nodes[key].get("agent_parameters", {}).items():
                lines.append("### 参数 " + name + "\n\n绑定类型：`" + value["type"] + "`。")
                content = value["value"]
                lines.append("```"+("text" if isinstance(content, str) else "json")+"\n"+
                             (content if isinstance(content, str) else pretty(content))+"\n```")
            lines.append("输出：text为Agent最终文本；工具过程在运行详情查看。不能用‘模型声称调用了’替代真实工具轨迹。")
        elif kind == "if-else":
            for case in data["cases"]:
                lines.append("分支 `"+case["case_id"]+"`；组合方式 "+case["logical_operator"]+"：")
                lines.extend("- "+selector(c.get("variable_selector"))+" `"+c["comparison_operator"]+"` `"+str(c.get("value", ""))+"`" for c in case["conditions"])
            lines.append("其他情况走ELSE/false出口。")
        elif kind == "knowledge-retrieval":
            lines.append("知识库：选择你工作空间中的"+args.knowledge_name+"；查询文本："+selector(data["query_variable_selector"]))
            lines.append("召回设置：\n\n```json\n"+pretty({k: v for k, v in public_nodes[key].items() if k not in ("title", "desc", "selected", "type", "dataset_ids")})+"\n```")
        elif kind == "assigner":
            for item in data["items"]:
                lines.append("写入目标："+selector(item["variable_selector"])+"；操作：`"+item["operation"]+"`；来源："+selector(item["value"])+"。")
        elif kind in ("variable-aggregator", "variable-assigner"):
            lines.append("输出类型："+data["output_type"]+"；输入按顺序："+"；".join(selector(v) for v in data["variables"]))
            lines.append("只选择互斥分支实际执行的输出，不拼接多个字符串。输出字段为output。")
        elif kind == "answer":
            lines.append("回复框填写：\n\n```text\n"+data["answer"]+"\n```")
        elif kind == "tool":
            lines.append("提供方类型：`"+data["provider_type"]+"`；服务标识：`"+data["provider_id"]+"`；工具：`"+data["tool_name"]+"`。")
            lines.append("### 每个工具参数\n\n```json\n"+pretty(data.get("tool_parameters", {}))+"\n```")
            unused = [p["name"] for p in data.get("paramSchemas", []) if p["name"] not in data.get("tool_parameters", {})]
            lines.append("以下可选参数不传，采用服务默认："+"、".join(unused))
        else:
            lines.append("```json\n"+pretty(public_nodes[key])+"\n```")
        if "retry_config" in data:
            lines.append("失败重试设置：`"+json.dumps(data["retry_config"], ensure_ascii=False)+"`。")
        if "error_strategy" in data:
            lines.append("异常处理：`"+data["error_strategy"]+"`；实际去向见下游列表。")
        sections.append("\n\n".join(lines))
    (args.output / "逐节点参数手册.md").write_text("\n\n---\n\n".join(sections), encoding="utf-8")
    print(f"Rendered {len(nodes)} node parameter sections")


if __name__ == "__main__":
    main()
