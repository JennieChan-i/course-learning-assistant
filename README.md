# 学知 · 课程智能学习助手

我为 AI 产品经理转岗学习者设计并实现了这款课程配套学习助手：先用课程证据回答问题，再用简答与产品场景题检查理解，最后根据真实作答安排薄弱点复习。

这个项目同时是我的 AI 产品经理实践档案。我记录需求假设、技术取舍、实验和失败案例，区分“已经实现”“已经测试”和“尚未验证”。课程机构可能为课后学习支持付费，这是待验证的商业假设，不是已有客户结论。

![学习工作台](docs/images/workbench.png)

## 当前交付

- React 学习工作台：课程范围、多轮对话、流式回复、来源侧栏、练习、复习、历史与可删除的学习偏好。
- 本地中文 embedding、BM25＋向量混合检索、RRF、rerank 和父子片段引用。
- 两个角色：学习教练与独立练习评估员；LangGraph 限制题目最多修订一次。
- 原生 Tool Calling、三个按需加载的应用 Skills、SQLite 学习记忆与上下文预算。
- 一个可选的 Tavily MCP 基础搜索集成，默认关闭，项目月度上限 300 次。
- 自编课程、120 条公开检索评测候选、40 条评分候选、私有课程评测准备脚本、测试与产品决策档案。

**运行边界：**默认是明确标注的固定演示回放，不调用 LLM，也不评价用户能力。真实模式需要自行配置 API。真实模型回答质量、真实 MCP 联通和学习效果不能由演示回放证明。人工评测标签尚待审阅；当前结果见 [验证记录](docs/validation.md)。

## 快速体验（Windows）

需要 Python 3.12、Node.js 22 或更新版本。首次安装依赖需要网络。

```powershell
# 在项目根目录执行；只体验演示可不安装 CUDA 版 PyTorch
powershell -ExecutionPolicy Bypass -File scripts/setup.ps1 -SkipGpu
powershell -ExecutionPolicy Bypass -File scripts/start.ps1
```

打开 [本机工作台](http://127.0.0.1:8765)。服务仅监听 127.0.0.1，无账号系统，不适用于直接暴露到公网。端口冲突时在 `.env` 设置 `APP_PORT`。

演示使用我独立编写的 16 个教学片段。提问播放固定答案；练习显示固定示例作答和预设反馈。演示和真实运行各用独立数据库，避免演示成绩污染学习档案。

## 启用真实模型

```powershell
# 安装带 CUDA 12.8 的 PyTorch，适配本项目测试的 RTX 5060 Ti
powershell -ExecutionPolicy Bypass -File scripts/setup.ps1
powershell -ExecutionPolicy Bypass -File scripts/download-models.ps1
powershell -ExecutionPolicy Bypass -File scripts/build-index.ps1 -Dataset sample
```

编辑 `.env`：

```dotenv
APP_MODE=live
DATASET=sample
LLM_BASE_URL=https://你的服务商提供的地址/v1
LLM_MODEL=你的模型名称
LLM_API_KEY=你的密钥
# 可选：免费搜索账户的密钥
TAVILY_API_KEY=
```

我使用兼容 OpenAI Chat Completions 的接口约定，要求模型支持原生工具调用和流式输出。部分兼容服务并不支持完整协议，先执行检查：

```powershell
.venv/Scripts/python.exe -m backend.cli doctor
.venv/Scripts/python.exe -m backend.cli probe
powershell -ExecutionPolicy Bypass -File scripts/start.ps1
```

诊断结果保存在 `runtime/live-sample/`。接口兼容性检查会发起少量真实调用；未配置密钥时记录 `not_run`。服务商若不支持当前协议，需要适配后使用，不能靠更改模型名宣称兼容。

本地模型默认是 `BAAI/bge-small-zh-v1.5` 与 `BAAI/bge-reranker-base`。模型在 `models/`，缓存与安装临时文件在 `.cache/`。完整 GPU 依赖及模型需要数 GB 磁盘空间，建议预留 12GB。没有 CUDA 时允许 CPU 推理，速度需要另测。

## 使用自己的课程

私有原始课程未随仓库发布。CSV 必需字段：

```text
chunk_id,chunk_text,doc_id,doc_name,topic,subtopic,content_type,suitable_questions,chunk_order
```

将已有汇总文件保存在 `chunk/all_course_chunks_clean_v1.csv`，然后：

```powershell
.venv/Scripts/python.exe -m backend.cli audit --dataset private
powershell -ExecutionPolicy Bypass -File scripts/build-index.ps1 -Dataset private
```

在 `.env` 设置 `DATASET=private` 并重启。运行中的真实服务独占本地 Qdrant 索引，因此重建索引或离线评测前先停止服务。数据版本变化会生成新的版本目录；旧索引不会被误用于新课程。

课程引用定位到整理片段，不能跳转原视频时间戳。质量报告只做规则筛查，不等同于知识正确性验证。使用 API 时，相关证据和必要上下文会发送给你配置的模型服务；联网搜索只发送精简查询词。

## 如何验证

```powershell
.venv/Scripts/python.exe -m pytest -q
.venv/Scripts/python.exe -m ruff check backend scripts tests
cd frontend
npm run build
cd ..
powershell -ExecutionPolicy Bypass -File scripts/evaluate.ps1 -Dataset sample -Split dev
.venv/Scripts/python.exe -m backend.cli grade-eval
```

检索评测比较 BM25、向量、混合检索、混合检索＋rerank。评分消融比较单教练和教练＋评估员，真实调用需密钥。所有原始报告写入 `runtime/`，公开发布时只复制自编数据报告。

公开候选标签及审阅方法见 [评测说明](evals/README.md)。私有课程运行 `scripts/prepare_private_evals.py` 后，还需人工改写问题并独立标注相关片段；候选来源不是 gold label。

## 项目结构

```text
backend/       API、模型、检索、Agent、记忆、MCP 与评测
frontend/      React 学习工作台
skills/        三个应用内教学 Skill
data/          可公开的自编课程
evals/         可公开的评测候选与测试集版本
scripts/       Windows 安装、启动、建库、评测和发布检查
tests/         接口、状态一致性、工具边界与协议回归测试
docs/          产品定义、架构、决策、验证、面试与演示资料
chunk/         私有课程（Git 忽略）
models/        本地模型（Git 忽略）
runtime/       数据库、索引、报告、日志（Git 忽略）
```

## 我的决策与复盘

- [产品需求与验收](docs/product.md)
- [架构和接口](docs/architecture.md)
- [决策档案](docs/decisions.md)
- [验证结果与未完成项](docs/validation.md)
- [面试复习](docs/interview.md)
- [演示脚本与七天试用表](docs/demo-and-trial.md)
- [已知问题与迭代方向](docs/limitations.md)

代码采用 MIT 许可证。第三方模型与组件保持各自许可证，见 [第三方说明](THIRD_PARTY_NOTICES.md)。请勿将密钥、课程原文、个人学习记录或私有报告提交到公开仓库。
