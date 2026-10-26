import { useEffect, useRef, useState } from "react";
import { Download, RefreshCw, Play, Square, ChevronLeft } from "lucide-react";
import { api } from "./api";
import { reasonNames } from "./Feedback";

const states: Record<string, string> = {
  pending: "待核查",
  confirmed: "已确认",
  in_progress: "处理中",
  verification: "待验证",
  closed: "已关闭",
  archived: "已归档",
};
const categories: Record<string, string> = {
  unknown: "待归因",
  data: "课程数据",
  retrieval: "召回",
  rerank: "排序",
  context: "上下文",
  generation: "回答生成",
  assessment: "练习评估",
  tool: "工具",
  runtime: "运行环境",
  ux: "交互体验",
};
const kinds: Record<string, string> = {
  chat: "课程答疑",
  practice: "练习生成",
  assessment: "作答评分",
};
const replayStates: Record<string, string> = {
  running: "执行中",
  completed: "执行完成，待人工判断",
  failed: "执行失败",
  cancelled: "已取消",
  interrupted: "被中断",
};
const transitions: Record<string, string[]> = {
  pending: ["pending", "confirmed", "archived"],
  confirmed: ["confirmed", "in_progress", "archived"],
  in_progress: ["in_progress", "verification", "archived"],
  verification: ["verification", "in_progress", "closed", "archived"],
  closed: ["closed", "in_progress"],
  archived: ["archived", "pending"],
};

function Json({ value }: { value: any }) {
  return <pre className="quality-json">{JSON.stringify(value, null, 2)}</pre>;
}

export default function Quality({
  initialId,
  busy,
  onBusy,
}: {
  initialId?: string;
  busy: boolean;
  onBusy: (value: boolean) => void;
}) {
  const [rows, setRows] = useState<any[]>([]),
    [filter, setFilter] = useState(""),
    [item, setItem] = useState<any>(null),
    [tab, setTab] = useState("process"),
    [message, setMessage] = useState(""),
    [error, setError] = useState(""),
    [loading, setLoading] = useState(false);
  const [draft, setDraft] = useState<any>({}),
    [reviewer, setReviewer] = useState(""),
    [confirmed, setConfirmed] = useState(false),
    [mode, setMode] = useState("frozen_evidence"),
    [reviewNotes, setReviewNotes] = useState(""),
    [verdict, setVerdict] = useState("fail"),
    [reviewConfirmed, setReviewConfirmed] = useState(false);
  const controller = useRef<AbortController | null>(null);
  useEffect(() => {
    load(initialId);
    return () => controller.current?.abort();
  }, []);
  async function load(id?: string) {
    setLoading(true);
    setError("");
    try {
      const list = await api("/badcases");
      setRows(list);
      if (id) await open(id);
      else if (!item && list.length) await open(list[0].id);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }
  async function open(id: string) {
    try {
      const data = await api(`/badcases/${id}`);
      setItem(data);
      setDraft({
        status: data.status,
        severity: data.severity,
        category: data.category,
        expected_behavior: data.expected_behavior,
        notes: data.notes,
        fix_reference: data.fix_reference,
      });
      setConfirmed(false);
      setReviewConfirmed(false);
      setReviewNotes("");
      setVerdict("fail");
      setMessage("");
      setError("");
    } catch (e: any) {
      setError(e.message);
    }
  }
  async function save(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    try {
      await api(`/badcases/${item.id}`, {
        method: "PUT",
        body: JSON.stringify({
          ...draft,
          reviewer,
          human_confirmed: confirmed,
        }),
      });
      await load(item.id);
      setMessage("处理记录已保存，原始现场保持不变。");
    } catch (e: any) {
      setError(e.message);
    }
  }
  async function runReplay() {
    onBusy(true);
    setError("");
    setMessage(
      "正在隔离复测。真实模式会调用当前LLM；本次不联网，不修改正式学习记录。",
    );
    controller.current = new AbortController();
    try {
      await api(`/badcases/${item.id}/replays`, {
        method: "POST",
        signal: controller.current.signal,
        body: JSON.stringify({ mode, request_id: crypto.randomUUID() }),
      });
      await load(item.id);
      setMessage(
        "复测执行已结束。请对照期望行为审阅，自动检查不等于语义正确。",
      );
    } catch (e: any) {
      setError(
        e.name === "AbortError"
          ? "已停止等待复测，可刷新查看取消或完成状态。"
          : e.message,
      );
    } finally {
      onBusy(false);
    }
  }
  async function reviewReplay(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    try {
      const latest = item.replays.at(-1);
      await api(`/badcases/${item.id}/replays/${latest.id}/review`, {
        method: "PUT",
        body: JSON.stringify({
          reviewer,
          notes: reviewNotes,
          verdict,
          human_confirmed: reviewConfirmed,
        }),
      });
      await load(item.id);
      setMessage("已记录人工复测判断。满足关闭条件后，可在处理页关闭案例。");
    } catch (e: any) {
      setError(e.message);
    }
  }
  async function exportCase() {
    try {
      const data = await api(`/badcases/${item.id}/export`, { method: "POST" });
      const url = URL.createObjectURL(
        new Blob([data.content], { type: "application/jsonl" }),
      );
      const a = document.createElement("a");
      a.href = url;
      a.download = data.filename;
      a.click();
      URL.revokeObjectURL(url);
      setMessage(
        "已导出本地回归样例。私有课程与作答不要上传到公开仓库；锁定测试集未修改。",
      );
    } catch (e: any) {
      setError(e.message);
    }
  }
  async function deleteCase() {
    if (
      !confirm(
        "删除此案例及关联反馈、复测记录？原始学习记录仍保留；已下载的文件需自行删除。",
      )
    )
      return;
    try {
      await api(`/badcases/${item.id}`, { method: "DELETE" });
      setItem(null);
      setRows(await api("/badcases"));
      setMessage("案例及关联反馈、复测记录已删除。");
    } catch (e: any) {
      setError(e.message);
    }
  }
  const latest = item?.replays?.at(-1);
  return (
    <section className="quality-page">
      <div className="quality-heading">
        <div>
          <h1>问题案例</h1>
          <p>核查反馈、保留现场、验证修复。待核查反馈不等于已确认缺陷。</p>
        </div>
        <button
          className="secondary"
          onClick={() => load(item?.id)}
          disabled={busy || loading}
        >
          <RefreshCw size={15} />
          {loading ? "加载中…" : "刷新"}
        </button>
      </div>
      {error && (
        <p className="quality-error" role="alert">
          {error}
        </p>
      )}
      {message && (
        <p className="quality-notice" role="status">
          {message}
        </p>
      )}
      <div className="quality-layout">
        <aside className="quality-list">
          <label htmlFor="case-filter">处理状态</label>
          <select
            id="case-filter"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
          >
            <option value="">全部状态</option>
            {Object.entries(states).map(([k, v]) => (
              <option key={k} value={k}>
                {v}
              </option>
            ))}
          </select>
          <p className="small-note">
            共 {rows.length} 个案例 ·{" "}
            {rows.filter((r) => r.status === "pending").length} 个待核查
          </p>
          {!rows.length && !loading && (
            <div className="quality-empty">
              <h2>从一条真实反馈开始</h2>
              <p>
                在回答下方或评分结果旁反馈问题。新运行的确定性异常也会进入待核查列表。
              </p>
            </div>
          )}
          <div className="quality-case-list">
            {rows
              .filter((r) => !filter || r.status === filter)
              .map((r) => (
                <button
                  className={`quality-case ${item?.id === r.id ? "selected" : ""}`}
                  key={r.id}
                  onClick={() => open(r.id)}
                  disabled={busy}
                >
                  <span>
                    {states[r.status]} · {r.severity}
                  </span>
                  <strong>{r.title}</strong>
                  <small>
                    {kinds[r.kind]} · {reasonNames[r.reason]} · {r.occurrences}{" "}
                    条报告
                  </small>
                </button>
              ))}
          </div>
        </aside>
        <div className="quality-detail">
          {item ? (
            <>
              <header className="quality-case-heading">
                <p className="small-note">
                  {kinds[item.kind]} · {item.id.slice(0, 10)} ·{" "}
                  {new Date(item.created_at).toLocaleString("zh-CN")}
                </p>
                <h2>{item.title}</h2>
                <div className="quality-actions">
                  <span className="topic-tag">{states[item.status]}</span>
                  <span>{categories[item.category]}</span>
                  <button
                    className="text-button"
                    onClick={exportCase}
                    disabled={
                      busy ||
                      ["pending", "archived"].includes(item.status) ||
                      item.answer_hidden
                    }
                  >
                    <Download size={14} />
                    导出回归样例
                  </button>
                  <button
                    className="text-button danger"
                    onClick={deleteCase}
                    disabled={busy}
                  >
                    删除案例
                  </button>
                </div>
              </header>
              {item.context_redacted && (
                <p className="quality-notice">
                  关联学习信息已删除，现场与复测上下文已清除，不能再次导出或复测。
                </p>
              )}
              {item.answer_hidden && (
                <p className="quality-notice">
                  练习尚未提交，参考答案和出题上下文暂不开放。
                </p>
              )}
              <div className="quality-tabs" aria-label="案例详情">
                {[
                  ["process", "核查与处理"],
                  ["snapshot", "运行现场"],
                  ["replay", "隔离复测"],
                  ["history", "处理历史"],
                ].map(([k, v]) => (
                  <button
                    key={k}
                    aria-pressed={tab === k}
                    onClick={() => setTab(k)}
                  >
                    {v}
                  </button>
                ))}
              </div>
              {tab === "process" && (
                <form className="quality-editor" onSubmit={save}>
                  <div className="quality-feedback-list">
                    {item.feedback?.map((f: any, i: number) => (
                      <p key={i}>
                        <strong>
                          {f.origin === "automatic"
                            ? "自动异常线索"
                            : "用户反馈"}{" "}
                          · {reasonNames[f.reason]}
                        </strong>
                        <br />
                        {f.comment || "未填写补充说明。"}
                      </p>
                    ))}
                  </div>
                  <div className="quality-grid">
                    <label>
                      处理状态
                      <select
                        value={draft.status}
                        onChange={(e) =>
                          setDraft({ ...draft, status: e.target.value })
                        }
                      >
                        {(transitions[item.status] || []).map((k) => (
                          <option value={k} key={k}>
                            {states[k]}
                          </option>
                        ))}
                      </select>
                    </label>
                    <label>
                      严重度
                      <select
                        value={draft.severity}
                        onChange={(e) =>
                          setDraft({ ...draft, severity: e.target.value })
                        }
                      >
                        <option>P0</option>
                        <option>P1</option>
                        <option>P2</option>
                      </select>
                    </label>
                    <label>
                      归因类别
                      <select
                        value={draft.category}
                        onChange={(e) =>
                          setDraft({ ...draft, category: e.target.value })
                        }
                      >
                        {Object.entries(categories).map(([k, v]) => (
                          <option value={k} key={k}>
                            {v}
                          </option>
                        ))}
                      </select>
                    </label>
                  </div>
                  <p className="small-note">
                    P0：信息或学习状态受损；P1：核心任务和质量问题；P2：体验问题。归因不确定时先保留待核查。
                  </p>
                  <label>
                    期望行为与验证标准
                    <textarea
                      value={draft.expected_behavior}
                      onChange={(e) =>
                        setDraft({
                          ...draft,
                          expected_behavior: e.target.value,
                        })
                      }
                      maxLength={3000}
                      rows={3}
                      placeholder="描述怎样才算正确，例如：明确说明重排不能补回未召回的资料，并引用支持这一结论的课程证据。"
                    />
                  </label>
                  <label>
                    核查依据与处理说明
                    <textarea
                      value={draft.notes}
                      onChange={(e) =>
                        setDraft({ ...draft, notes: e.target.value })
                      }
                      maxLength={3000}
                      rows={3}
                    />
                  </label>
                  <label>
                    修复提交或版本
                    <input
                      value={draft.fix_reference}
                      onChange={(e) =>
                        setDraft({ ...draft, fix_reference: e.target.value })
                      }
                      maxLength={120}
                      placeholder="例如：提交SHA或明确的修复版本"
                    />
                  </label>
                  <label>
                    实际审阅者
                    <input
                      value={reviewer}
                      onChange={(e) => setReviewer(e.target.value)}
                      maxLength={80}
                      required
                    />
                  </label>
                  <label className="quality-check">
                    <input
                      type="checkbox"
                      checked={confirmed}
                      onChange={(e) => setConfirmed(e.target.checked)}
                      required
                    />
                    我已实际核查，以上是人工处理判断。
                  </label>
                  <button
                    className="primary"
                    disabled={busy || item.context_redacted}
                  >
                    保存处理记录
                  </button>
                  <p className="small-note">
                    关闭需有对应修复版本的复测，并由人工确认通过。归档需填写原因。
                  </p>
                </form>
              )}
              {tab === "snapshot" && (
                <div className="quality-snapshot">
                  <p>
                    原始运行状态：{item.original_status}
                    。快照仅保存在本机；复测使用当前程序，不保证外部API产生完全相同的输出。
                  </p>
                  <details open>
                    <summary>当时的输出</summary>
                    <Json value={item.original_output} />
                  </details>
                  <details>
                    <summary>输入、上下文、证据与检索候选</summary>
                    <Json value={item.snapshot} />
                  </details>
                </div>
              )}
              {tab === "replay" && (
                <div className="quality-replay">
                  <p>
                    复测使用临时学习数据库，不会更新你的成绩、偏好或复习计划。真实模式会消耗当前LLM
                    Token，每次只执行一轮任务；不发起新的联网搜索。
                  </p>
                  <label>
                    复测范围
                    <select
                      value={mode}
                      onChange={(e) => setMode(e.target.value)}
                      disabled={busy}
                    >
                      <option value="frozen_evidence">
                        固定历史资料池：检查生成与评分
                      </option>
                      <option value="current_knowledge">
                        当前知识库：重跑检索与生成
                      </option>
                    </select>
                  </label>
                  <p className="small-note">
                    固定资料池不能验证召回和重排改进；当前知识库模式会受资料变化影响。历史网页不会重新联网核实。
                  </p>
                  <div className="quality-actions">
                    <button
                      className="primary"
                      onClick={runReplay}
                      disabled={
                        busy ||
                        !["confirmed", "in_progress", "verification"].includes(
                          item.status,
                        ) ||
                        item.context_redacted ||
                        item.answer_hidden
                      }
                    >
                      <Play size={15} />
                      开始隔离复测
                    </button>
                    {busy && (
                      <button
                        className="secondary"
                        onClick={() => controller.current?.abort()}
                      >
                        <Square size={14} />
                        停止复测
                      </button>
                    )}
                  </div>
                  {latest && (
                    <>
                      <h3>{replayStates[latest.status] || latest.status}</h3>
                      <p className="small-note">
                        {latest.mode_label === "demo"
                          ? "固定演示回放，不是实际AI质量验证"
                          : "真实模型调用"}{" "}
                        ·{" "}
                        {latest.elapsed_ms
                          ? (latest.elapsed_ms / 1000).toFixed(2) + " 秒"
                          : ""}
                      </p>
                      {latest.error && (
                        <p className="quality-error">{latest.error}</p>
                      )}
                      <details open>
                        <summary>复测输出</summary>
                        <Json value={latest.output} />
                      </details>
                      <details>
                        <summary>自动检查、配置与工具事件</summary>
                        <Json
                          value={{
                            checks: latest.checks,
                            strategy: latest.strategy,
                            scope: latest.scope,
                            configuration: latest.configuration,
                            events: latest.events,
                            usage: latest.usage,
                          }}
                        />
                      </details>
                      <details>
                        <summary>本次复测的证据与检索记录</summary>
                        <Json
                          value={{
                            evidence: latest.evidence,
                            retrievals: latest.retrievals,
                            prompt_version: latest.prompt_version,
                            skill_versions: latest.skill_versions,
                          }}
                        />
                      </details>
                      {latest.status === "completed" && (
                        <form
                          onSubmit={reviewReplay}
                          className="quality-editor"
                        >
                          <h3>人工审阅复测结果</h3>
                          {latest.review && (
                            <p className="small-note">
                              已有判断：
                              {latest.review.verdict === "pass"
                                ? "通过"
                                : "未通过"}{" "}
                              · {latest.review.reviewer}
                            </p>
                          )}
                          <label>
                            审阅结果
                            <select
                              value={verdict}
                              onChange={(e) => setVerdict(e.target.value)}
                            >
                              <option value="fail">未通过</option>
                              <option value="pass">通过</option>
                            </select>
                          </label>
                          <label>
                            人工判断依据
                            <textarea
                              value={reviewNotes}
                              onChange={(e) => setReviewNotes(e.target.value)}
                              required
                              rows={3}
                              maxLength={3000}
                            />
                          </label>
                          <label>
                            实际审阅者
                            <input
                              value={reviewer}
                              onChange={(e) => setReviewer(e.target.value)}
                              required
                              maxLength={80}
                            />
                          </label>
                          <label className="quality-check">
                            <input
                              type="checkbox"
                              checked={reviewConfirmed}
                              onChange={(e) =>
                                setReviewConfirmed(e.target.checked)
                              }
                              required
                            />
                            我已对照期望行为与证据实际核查。
                          </label>
                          <button className="secondary" disabled={busy}>
                            保存人工复验判断
                          </button>
                        </form>
                      )}
                    </>
                  )}
                  {item.replays.length > 1 && (
                    <details>
                      <summary>
                        此前 {item.replays.length - 1} 次复测（含失败）
                      </summary>
                      <Json value={item.replays.slice(0, -1)} />
                    </details>
                  )}
                </div>
              )}
              {tab === "history" && (
                <ol className="quality-history">
                  {item.events.map((event: any, i: number) => (
                    <li key={i}>
                      <strong>
                        {event.action === "status_update"
                          ? `${states[event.from]} → ${states[event.to]}`
                          : event.action === "reported"
                            ? "反馈进入待核查"
                            : event.action === "replay_review"
                              ? "人工复测判断"
                              : event.action === "replay_started"
                                ? "发起隔离复测"
                                : event.action === "replay_finished"
                                  ? `复测结束：${replayStates[event.status] || event.status}`
                                  : event.action === "replay_interrupted"
                                    ? "服务重启中断复测"
                                    : event.action === "context_redacted"
                                      ? "关联现场已清除"
                                      : "新反馈重新打开案例"}
                      </strong>
                      <p>{event.notes || event.verdict || ""}</p>
                      {event.after && (
                        <details>
                          <summary>当次归因、标准与修复版本</summary>
                          <Json
                            value={{ before: event.before, after: event.after }}
                          />
                        </details>
                      )}
                      <small>
                        {event.reviewer || "系统"} ·{" "}
                        {new Date(event.at).toLocaleString("zh-CN")}
                      </small>
                    </li>
                  ))}
                </ol>
              )}
              <button
                className="text-button quality-back"
                onClick={() => {
                  setItem(null);
                  setTab("process");
                }}
              >
                <ChevronLeft size={14} />
                返回列表
              </button>
            </>
          ) : (
            rows.length > 0 && (
              <p className="quality-empty">
                从左侧选择一个案例，查看反馈和运行现场。
              </p>
            )
          )}
        </div>
      </div>
    </section>
  );
}
