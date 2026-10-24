# 评测资料

我将检索、答案可信度、学习反馈与运行性能分别评估，避免用一个总分掩盖不同问题。

`retrieval.jsonl` 含 120 条针对自编课程的问题：80 条可回答、20 条资料未覆盖、20 条对抗输入。24 个知识或风险组按组分成 60 条开发集和 60 条测试集。同一组的近义问题不跨集合。

这些问题与相关片段标签是**开发者编写的候选稿，尚未获得独立人工审阅**。未审阅时报告标为 exploratory，不用于宣称 Recall ≥90% 的正式验收已通过。检索指标只在可回答题上计算，其他题用于答案边界和安全检查。

`grading.jsonl` 包含 10 个题目、每题 4 档候选作答。`draft_expected_total` 是初始分层，不是人工真值。审阅者需检查题意、重新完善评分标准、填写 `human_expected_total` 并将 `review_status` 改为 `approved`。不把复制原文自动视作满分。

锁定测试集时在 `test-lock.json` 保存内容哈希；修改标签需要新版本、审阅记录和原因。开发阶段只运行 dev。所有正式阈值都要同时报告样本量、人工标注状态与适用范围。

私有课程的题目和答案保存在 `runtime/live-private/evals/`，不进入公开仓库。公开样例的结果不能代表 877 个私有课程片段的效果。

答案审阅表至少包括：问题、系统答案、事实主张、支持来源、是否确实支持、缺失证据处理、首次有效内容时间、完整耗时。对抗输入检查是否泄露配置、越权写入、伪造来源或绕过搜索限制。

## 本地人工审阅入口

启动服务后访问 `http://127.0.0.1:8765/evaluation`。我把候选问题、完整课程片段、待评分作答和人工标注表放在同一页；人工确认不会由开发代理代选。检索与评分标注保存在 `runtime/evaluation-review.sqlite3`，关联原文件哈希；原数据改变后旧标注不会自动套用。评测程序自动读取匹配版本的标注。公开题面保持锁定，私有工作表可独立改写问题。

```powershell
# 一次最多新增两个评分批次，相同版本重新执行跳过已有记录
.venv/Scripts/python.exe -m backend.cli grade-eval --dataset sample --max-batches 2
# 仅复测指定报告的失败批次；另存新报告，保留失败基线
.venv/Scripts/python.exe -m backend.cli grade-eval --dataset sample --retry-report runtime/live-sample/reports/grading-原报告指纹.json
# 显式选择开发集回归子集，不替代完整评测
.venv/Scripts/python.exe -m backend.cli answer-eval --dataset sample --split dev --case-ids E001,E101,E102
```

每批最多三份评分样例。尾批补齐的条目只满足三题协议，不计入有效样本；Token与耗时按实际调用计入一次。结构错误最多重新生成一次，超时不自动循环重试。所有恢复、修复后的结果记录新版本，不和原基线拼接成一次无失败的完整实验。
