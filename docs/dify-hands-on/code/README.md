# 代码节点怎么填写

这些是可以复制进 Dify“代码执行”节点的独立 Python 文件，不需要在 Dify 安装本项目，也不需要运行服务器。每个节点只复制对应文件全文。输入名称须和函数参数一致，输出类型须与返回值一致；多写/漏写输出都可能导致节点失败。[官方代码节点说明](https://docs.dify.ai/zh/cloud/use-dify/nodes/code)

| 节点 | 文件 | 输入 | 输出：全部 String |
|---|---|---|---|
| R01 | router.py | query、saved_state、scope | action、topic、answers、pending、context、state、notice、review_topic |
| K02、P00b、检索工具证据整理 | evidence.py | results=知识检索/result | evidence、catalog、source_ids、has_evidence |
| Q05 | save_answer.py | answer、source_ids、state_json、query | display、state |
| P05、P10 | practice_gate.py | draft、audit、evidence、state_json、review_topic | ok、state、display、issues |
| G03 | commit_grade.py | grade、state_json | ok、state、feedback、display、error |

第 4 章还有一个仅三行的对象转 JSON 节点：输入 value 选实际 `structured_output` 对象，输出 json 为 String。不要把它的输入改成 LLM/text 后仍声明为 dict。

## 状态赋值必须遵守的顺序

1. 用户输入→R01→覆盖 learning_state=R01/state→任务分流。
2. 成功答疑→Q05→覆盖 learning_state=Q05/state→显示 Q05/display。
3. 通过审题→P05 或 P10→覆盖对应 state→只显示 display。
4. 有效评分→G03→覆盖 G03/state→显示代码生成的分数和参考答案→教练辅导。
5. 未通过审题/评分不覆盖状态；保留原待作答题。

所有后续节点的 state_json 从 **R01/state** 取本轮状态，不从旧的会话变量再读一次，避免忽略本轮入口已发生的修改。分支中互斥地写入，不从失败出口连到成功写入节点。

## 能保证与不能保证

代码检查引用编号存在性、题目形状、评分范围，计算分数与日期；它不替代课程正确性、题目可答性或人工评分质量评测。原型仅覆盖单用户顺序操作，没有数据库锁/事务、全局搜索预算、跨聊天档案、全量作答归档或自动提醒。JSON 字符串也不是数据库。

样例状态只保留最近12轮成绩摘要，近期6个问答对；完整历史要通过平台日志查看并另行制定保留策略。按主题字符串匹配复习：同义词写成两个主题时不会自动合并，演示时沿用稳定主题名称；生产版应有标准知识点 ID 与人工可纠正的映射，不能把这个原型说成已经完成知识追踪模型。

## 本地检查

在项目根目录的 PowerShell：

```powershell
.\.venv\Scripts\python.exe docs/dify-hands-on/code/check_examples.py
```

检查使用自编证据和固定数据，不调用 LLM 或搜索服务。它证明复制代码的规则可执行，不证明已经在你的 Dify 实例完成全部配置，也不证明 AI 输出质量。所有真实模型/Agent/MCP 验收都应在预览与日志中重新执行。
