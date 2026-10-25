# 我为Dify适配层约定的接口与状态

**这是待实现的接口规格，不是当前后端已有能力。** 运行教程前应先完成这些处理器；主产品代码和路由保持不变。只读Agent工具的OpenAPI见 [bridge-openapi.yaml](bridge-openapi.yaml)。

## 1. 哪些能力复用，哪些必须新写

| 可复用当前代码 | 需要新增 |
|---|---|
| Knowledge.search、Corpus.source、本地模型、索引和课程审计 | 专用bridge入口、Bearer鉴权与Docker可达性 |
| QuestionSet、AuditResult、GradeSet、题目校验规则 | 运行票据、票据撤销、动作/课程/权限绑定 |
| Store的练习、成绩、偏好、复习与事务 | 每运行证据摘录记录、一次联网尝试与工具计数 |
| WebSearch的真实MCP与项目月度预算 | 运行级幂等、持久化操作回执与版本绑定 |
| Skill文件、CitationGuard、原有界面 | FastAPI到Dify的服务API客户端、事件转换和停止任务 |
| 现有评测题、人工标注和指标 | Dify执行适配器及迁移对照报告 |

不要直接调用Agents.create_practice/grade作为Dify的“数据库工具”，否则会在HTTP节点里再次执行原LLM流程。领域处理器只做数据、检索与验证。

## 2. 运行创建与权限

前端调用现有应用入口，FastAPI在本机单用户边界内创建运行。run_ticket使用随机不透明值，服务端保存摘要和绑定信息，可设约15分钟有效；生成练习与提交练习各创建一张新票据，学生等待作答不占据运行时长。

建议逻辑字段：

```text
run_id, ticket_hash, user_id, action, role_permissions, course_id
web_enabled, practice_id, review_id, request_payload_hash
kb_version, memory_version, expires_at, cancelled
course_calls, source_calls, web_attempted
workflow_run_id, task_id, dify_conversation_id
```

- 用户身份、课程、联网许可不能靠模型填表确认。
- 单课票据的工具接口不接受course_id覆盖；全部课程是显式权限状态。
- 评分票据默认禁止网页工具。
- 票据失效、取消或记忆版本变化后，后续读取/写入按动作拒绝或重建。
- 搜索前要求本轮已做课程检索；每轮联网尝试通过原子检查最多一次。
- 工具令牌和流程令牌分开授权；票据本身不能绕过服务令牌鉴权。

## 3. 固定HTTP节点的通用请求

除健康检查外，固定HTTP节点使用：

```text
Authorization: Bearer {BRIDGE_FLOW_TOKEN}
Content-Type: application/json
```

只读Agent工具使用另外的工具凭据。错误返回明确的HTTP状态及短错误信息，不能带请求头、Key或完整异常对象。不要把HTTP400的body当正常上下文继续调用模型。

### `/bridge/context`

```json
{"run_ticket":"执行票据"}
```

响应固定为 `{"ok":true,"context":{...}}`。按action控制context：

| action | 可以包含 | 排除 |
|---|---|---|
| chat/review解释 | 当前问题、当前Skill、明确偏好、预算内近期对话、必要复习误解 | 全课程、完整档案、参考答案 |
| generate/review_generate | 指定知识点、课程、必要历史误解、practice/review Skill | 未经确认掌握状态、全历史 |
| submit | 原题、标准、真实作答、对应证据与预期题号 | 教练对学生的主观评价、无关偏好、网页 |

上下文从已绑定请求读取，不接受Dify传来一份替代档案。需要课程检索的字段可显式单列，不能让解析节点猜测自由文本。

### `/bridge/evidence/finalize`

```json
{"run_ticket":"执行票据"}
```

响应例子：

```json
{
  "ok":true,
  "decision":"answer",
  "context":{
    "question":"RAG是什么？",
    "evidence":[{"id":"S01","type":"course","title":"检索增强生成","text":"自编课程摘录"}],
    "source_ids":["S01"],
    "warnings":[],
    "evidence_version":"服务端生成的证据快照版本"
  }
}
```

decision为answer/clarify/insufficient。不得直接使用Agent输出构造证据名单；服务端必须有对应工具返回记录。评分时优先原题来源，所有实际送入评分节点的证据都进入暴露名单。最多6条，网页最多3条，评分无网页。

## 4. 出题校验与持久化

### `/bridge/practices/validate`

```json
{
  "run_ticket":"执行票据",
  "question_set":{"questions":[]},
  "evidence_version":"当前证据快照版本"
}
```

空数组只是显示字段结构，不是有效题目。处理器使用QuestionSet校验，再检查唯一题号、两种题型、合法来源、当前版本。成功返回 `ok=true` 和经过规范化的question_set；格式错误返回可供一次修复的错误位置，不写入练习。

### `/bridge/practices/save`

```json
{
  "run_ticket":"执行票据",
  "question_set":{"questions":[]},
  "audit":{"approved":true,"issues":[]},
  "evidence_version":"当前证据快照版本"
}
```

处理顺序：

1. 验证活动票据、动作、课程与快照；不接受提交动作调用此接口。
2. 检查题目与AuditResult结构，确认审核通过，并记录原始Dify运行ID以追溯审核节点。
3. 后端再次检查题目来源；模型批准不能覆盖代码校验失败。
4. 检查 `(run_id, save_practice)` 幂等记录，已有结果则返回原practice_id。
5. 在同一SQLite事务里保存练习与幂等结果；不能先存题后另一次提交幂等标记。
6. 使用与public_practice一致的过滤逻辑返回题干，不返回内部reference_answer/rubric。

示意响应：

```json
{
  "ok":true,
  "practice_id":"后端创建的ID",
  "questions":[{"id":"q1","topic":"RAG","kind":"short_answer","prompt":"题干示例","source_ids":["S01"]}]
}
```

实际响应必须包含三题。工作流审核的真实性通过受信Dify服务API、受限画布和运行记录追溯；字段approved本身不是来自学生的授权。编辑Dify流程的开发者被视为可信维护者，不能把此接口当作面向任意用户的远程成绩API。

## 5. 评分校验、回执与提交

### `/bridge/grades/validate`

```json
{
  "run_ticket":"提交票据",
  "grade_set":{"grades":[]},
  "evidence_version":"评估员实际读取的证据版本"
}
```

仍需实际三条成绩；题号、答案及题目版本以数据库为准。处理器检查分项整数0–2、题号集合、非空来源与已暴露课程证据，并将规范化成绩临时保存到服务端，生成回执：

```json
{
  "ok":true,
  "validation_receipt":"后端创建的短期回执",
  "feedback_context":{"grades":[]}
}
```

回执绑定run_id、practice_id、作答哈希、成绩哈希、证据与课程版本。教练只拿feedback_context，不重新提交一份改写的成绩。此时还没有更新长期学习结果。

### `/bridge/grades/commit`

```json
{
  "run_ticket":"提交票据",
  "validation_receipt":"校验回执",
  "coaching":"教练根据已校验成绩给出的学习建议"
}
```

1. 从服务端回执读取成绩，不接受客户端额外的总分、stage或due_at。
2. 复查票据未取消、学生作答与题目版本未变化、回执未过期。
3. 使用BEGIN IMMEDIATE事务；已完成则返回原结果，不再写第二份证据。
4. 复用当前Store.finish_practice的评分与复习逻辑；幂等记录与业务提交需要在同一事务或采用业务唯一键保证一致。
5. 提交后返回公开成绩与复习结果，日志记录Dify和本地run_id。

如果网络在提交成功后断开，重试应拿到原结果。取消与提交同时发生时，以事务检查和最终数据库状态为准，不能向用户保证“关闭页面必然撤销已完成成绩”。

## 6. 证据、联网与记忆规则

- 每个工具返回都登记实际摘录；finalize、LLM输入与引用验证使用同一份版本。
- 网页搜索每轮一次：在网络调用前原子标记web_attempted，再调用现有WebSearch。月度次数只由现有WebSearch预留一次，网关不要再重复预留。
- 任何动作都不能通过换数据库、改Dify会话ID或清空学习记录重置月度计数。
- 只保存用户明确提供偏好与有效学习证据；Dify内置Memory关闭时仍需显式裁剪后端history。
- 删除偏好后使旧记忆版本票据失效，并处理Dify上下文缓存与日志副本。
- 写入操作不是Agent工具；自定义工具契约里没有任意URL和任意数据库操作。

## 7. 实现后的最小合同测试

至少验证：无Bearer、票据过期、伪造course_id、评分票据联网、同轮两次搜索、超限/失败计数、假来源、重复题号、分数越界、未答题提交、审核失败保存、重复保存/提交、取消后提交、版本变化、删除偏好后的旧票据、事务失败回滚。

这些合同检查完成后再连Dify画布；当前项目的37项测试不覆盖尚未实现的bridge协议。
