import { useEffect, useRef, useState } from "react";
import ReactMarkdown, { defaultUrlTransform } from "react-markdown";
import {
  ArrowUp,
  ArrowUpRight,
  BookOpen,
  Check,
  ChevronDown,
  ChevronRight,
  CircleHelp,
  FileText,
  Globe2,
  History,
  ListChecks,
  Loader2,
  MessageSquare,
  Moon,
  PanelLeftClose,
  PanelLeftOpen,
  Plus,
  RotateCcw,
  Settings2,
  ShieldCheck,
  Square,
  Sun,
  Trash2,
  X,
} from "lucide-react";
import { api, streamChat } from "./api";

type Source = {
  id: string;
  type: "course" | "web";
  doc_name: string;
  subtopic: string;
  chunk_text: string;
  url?: string;
  version?: string;
  quality_flags?: string[];
};
type Message = {
  role: "user" | "assistant";
  content: string;
  sources?: Source[];
  failed?: boolean;
};
type Page = "chat" | "practice" | "review" | "history" | "settings";
type Course = { id: string; name: string; count: number };
const navigation = [
  { id: "chat", label: "课程答疑", icon: MessageSquare },
  { id: "practice", label: "理解练习", icon: ListChecks },
  { id: "review", label: "薄弱点复习", icon: RotateCcw },
  { id: "history", label: "学习记录", icon: History },
] as const;
const prefNames: Record<string, string> = {
  background: "已有基础",
  goal: "学习目标",
  explanation_style: "讲解偏好",
};

function Content({
  text,
  sources,
  onSource,
}: {
  text: string;
  sources: Source[];
  onSource: (s: Source) => void;
}) {
  let display = text;
  for (const source of sources)
    display = display
      .split(`[${source.id}]`)
      .join(`[${source.id}](source:${source.id})`);
  return (
    <ReactMarkdown
      urlTransform={(u) =>
        u.startsWith("source:") ? u : defaultUrlTransform(u)
      }
      components={{
        a: ({ href, children }) => {
          const source = sources.find((s) => `source:${s.id}` === href);
          return source ? (
            <button
              className="inline-citation"
              onClick={() => onSource(source)}
              aria-label={`查看来源 ${source.id}`}
            >
              {children}
            </button>
          ) : (
            <a href={href} target="_blank" rel="noreferrer">
              {children}
            </a>
          );
        },
      }}
    >
      {display}
    </ReactMarkdown>
  );
}

export default function App() {
  const [page, setPage] = useState<Page>("chat"),
    [status, setStatus] = useState<any>(null);
  const [courses, setCourses] = useState<Course[]>([]),
    [courseId, setCourseId] = useState("");
  const [sessions, setSessions] = useState<any[]>([]),
    [sessionId, setSessionId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]),
    [input, setInput] = useState("");
  const [web, setWeb] = useState(false),
    [busy, setBusy] = useState(false),
    [stage, setStage] = useState("");
  const [error, setError] = useState(""),
    [notice, setNotice] = useState("");
  const [source, setSource] = useState<Source | null>(null),
    [sidebar, setSidebar] = useState(window.innerWidth > 800);
  const [practice, setPractice] = useState<any>(null),
    [topic, setTopic] = useState(""),
    [answers, setAnswers] = useState<Record<string, string>>({});
  const [reviews, setReviews] = useState<any[]>([]),
    [practices, setPractices] = useState<any[]>([]),
    [prefs, setPrefs] = useState<any[]>([]),
    [traces, setTraces] = useState<any[]>([]);
  const [prefKey, setPrefKey] = useState("explanation_style"),
    [prefValue, setPrefValue] = useState("");
  const [theme, setTheme] = useState(
    localStorage.getItem("course-theme") || "system",
  );
  const controller = useRef<AbortController | null>(null),
    bottom = useRef<HTMLDivElement>(null),
    composer = useRef<HTMLTextAreaElement>(null);
  const course = courses.find((c) => c.id === courseId);
  const isDemo = status?.mode === "demo";

  async function refresh() {
    const results = await Promise.all([
      api("/status"),
      api("/courses"),
      api("/sessions"),
      api("/reviews"),
      api("/practices"),
      api("/preferences"),
      api("/traces"),
    ]);
    setStatus(results[0]);
    setCourses(results[1]);
    setSessions(results[2]);
    setReviews(results[3]);
    setPractices(results[4]);
    setPrefs(results[5]);
    setTraces(results[6]);
  }
  useEffect(() => {
    refresh().catch((e) => setError(e.message));
    return () => controller.current?.abort();
  }, []);
  useEffect(() => {
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    const apply = () => {
      document.documentElement.dataset.theme =
        theme === "system" ? (media.matches ? "dark" : "light") : theme;
    };
    apply();
    media.addEventListener("change", apply);
    localStorage.setItem("course-theme", theme);
    return () => media.removeEventListener("change", apply);
  }, [theme]);
  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "instant" });
  }, [messages, stage]);
  useEffect(() => {
    const escape = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setSource(null);
        if (window.innerWidth <= 800) setSidebar(false);
      }
    };
    window.addEventListener("keydown", escape);
    return () => window.removeEventListener("keydown", escape);
  }, []);

  function navigate(next: Page) {
    setPage(next);
    setError("");
    setNotice("");
    setSource(null);
    if (window.innerWidth <= 800) setSidebar(false);
  }
  function newChat(id = courseId) {
    if (busy) return;
    setCourseId(id);
    setSessionId(null);
    setMessages([]);
    setInput("");
    navigate("chat");
    composer.current?.focus();
  }
  async function openSession(s: any) {
    if (busy) return;
    try {
      const history = await api(`/sessions/${s.id}`);
      setCourseId(s.course_id || "");
      setSessionId(s.id);
      setMessages(
        history.map((m: any) => ({
          role: m.role,
          content: m.content,
          sources: m.metadata?.sources || [],
        })),
      );
      navigate("chat");
    } catch (e: any) {
      setError(e.message);
    }
  }
  async function send(
    value = input,
    overrides?: { courseId: string; skill: string },
  ) {
    if (!value.trim() || busy) return;
    setError("");
    setNotice("");
    setInput("");
    setPage("chat");
    setBusy(true);
    setStage(isDemo ? "播放演示回放" : "正在理解问题");
    if (overrides) {
      setCourseId(overrides.courseId || "");
      setSessionId(null);
    }
    const initial: Message[] = overrides ? [] : messages;
    setMessages([
      ...initial,
      { role: "user", content: value },
      { role: "assistant", content: "", sources: [] },
    ]);
    controller.current = new AbortController();
    let complete = false;
    try {
      await streamChat(
        {
          message: value,
          course_id: (overrides?.courseId ?? courseId) || null,
          session_id: overrides ? null : sessionId,
          web_enabled: web,
          skill: overrides?.skill || "explain",
        },
        controller.current.signal,
        (e) => {
          if (e.event === "start") setSessionId(e.session_id);
          if (e.event === "status") setStage(e.text);
          if (e.event === "delta") {
            setStage("");
            setMessages((prev) =>
              prev.map((m, i) =>
                i === prev.length - 1
                  ? { ...m, content: m.content + e.text }
                  : m,
              ),
            );
          }
          if (e.event === "sources")
            setMessages((prev) =>
              prev.map((m, i) =>
                i === prev.length - 1 ? { ...m, sources: e.sources } : m,
              ),
            );
          if (e.event === "warning") setNotice(e.text);
          if (e.event === "done") complete = true;
          if (e.event === "error") throw new Error(e.text);
        },
      );
      if (!complete) throw new Error("连接提前结束，回答未保存，请重试。");
    } catch (e: any) {
      const message =
        e.name === "AbortError" ? "已停止生成，本轮回答未保存。" : e.message;
      setError(message);
      setMessages((prev) =>
        prev.map((m, i) =>
          i === prev.length - 1
            ? { ...m, content: message, failed: true, sources: [] }
            : m,
        ),
      );
      controller.current?.abort();
    } finally {
      setBusy(false);
      setStage("");
      refresh().catch(() => {});
    }
  }
  async function makePractice(review?: any) {
    if (busy) return;
    controller.current = new AbortController();
    setBusy(true);
    setStage(isDemo ? "准备固定练习回放" : "教练正在出题，评估员将独立检查");
    setError("");
    setSource(null);
    setPage("practice");
    try {
      const p = await api("/practices", {
        method: "POST",
        signal: controller.current.signal,
        body: JSON.stringify({
          course_id: review?.course_id || courseId || null,
          topic: review?.topic || topic,
          review_id: review?.id || null,
        }),
      });
      setPractice(p);
      setAnswers(p.demo_answers || {});
      await refresh();
    } catch (e: any) {
      setError(
        e.name === "AbortError"
          ? "已停止生成练习。已完成的练习仍可在学习记录中查看。"
          : e.message,
      );
    } finally {
      setBusy(false);
      setStage("");
    }
  }
  async function submitPractice() {
    controller.current = new AbortController();
    setBusy(true);
    setError("");
    setStage(isDemo ? "播放固定反馈" : "评估员正在核对作答与课程依据");
    try {
      const p = await api(`/practices/${practice.id}/submit`, {
        method: "POST",
        signal: controller.current.signal,
        body: JSON.stringify({ answers }),
      });
      setPractice(p);
      await refresh();
    } catch (e: any) {
      setError(
        e.name === "AbortError"
          ? "已停止等待评分，可在学习记录中检查完成状态或稍后重试。"
          : e.message,
      );
    } finally {
      setBusy(false);
      setStage("");
    }
  }
  async function savePref() {
    try {
      await api("/preferences", {
        method: "PUT",
        body: JSON.stringify({ key: prefKey, value: prefValue }),
      });
      setPrefValue("");
      await refresh();
      setNotice("学习偏好已保存，之后的回答会按需参考。");
    } catch (e: any) {
      setError(e.message);
    }
  }
  async function remove(path: string) {
    try {
      await api(path, { method: "DELETE" });
      await refresh();
    } catch (e: any) {
      setError(e.message);
    }
  }
  function selectPractice(p: any) {
    setPractice(p);
    setAnswers(p.result?.answers || p.demo_answers || {});
    navigate("practice");
  }
  const title = navigation.find((n) => n.id === page)?.label || "设置";

  return (
    <div
      className={`app ${sidebar ? "has-sidebar" : ""} ${source ? "has-source" : ""}`}
    >
      {sidebar && (
        <>
          <button
            className="sidebar-scrim"
            aria-label="关闭导航"
            onClick={() => setSidebar(false)}
          />
          <aside className="sidebar">
            <div className="brand">
              <span className="brand-mark">
                <BookOpen size={18} />
              </span>
              <span>
                学知<span className="brand-sub">课程学习工作台</span>
              </span>
              <button
                className="icon-button"
                aria-label="收起导航"
                onClick={() => setSidebar(false)}
              >
                <PanelLeftClose size={18} />
              </button>
            </div>
            <button
              className="new-chat"
              onClick={() => newChat()}
              disabled={busy}
            >
              <Plus size={16} /> 新的学习对话{" "}
              <span className="shortcut">＋</span>
            </button>
            <nav aria-label="功能导航">
              {navigation.map((n) => (
                <button
                  key={n.id}
                  className={`nav-item ${page === n.id ? "active" : ""}`}
                  onClick={() => navigate(n.id)}
                  disabled={busy && page !== n.id}
                >
                  <n.icon size={17} />
                  {n.label}
                  {n.id === "review" && reviews.length > 0 && (
                    <span className="count">{reviews.length}</span>
                  )}
                </button>
              ))}
            </nav>
            <div className="sidebar-section">
              <span>我的课程</span>
              <span>{courses.length}</span>
            </div>
            <div className="course-list">
              <button
                className={`course-item ${!courseId ? "selected" : ""}`}
                onClick={() => newChat("")}
                disabled={busy}
              >
                <span className="course-dot" />
                全部课程
              </button>
              {courses.map((c) => (
                <button
                  className={`course-item ${courseId === c.id ? "selected" : ""}`}
                  key={c.id}
                  title={c.name}
                  onClick={() => newChat(c.id)}
                  disabled={busy}
                >
                  <span className="course-dot" />
                  {c.name.replace(/^\d+[-_ ]*/, "")}
                </button>
              ))}
            </div>
            {sessions.length > 0 && (
              <>
                <div className="sidebar-section">
                  <span>最近对话</span>
                </div>
                <div className="recent-list">
                  {sessions.slice(0, 3).map((s) => (
                    <button
                      key={s.id}
                      className={`recent-item ${s.id === sessionId ? "selected" : ""}`}
                      onClick={() => openSession(s)}
                      disabled={busy}
                    >
                      {s.title}
                    </button>
                  ))}
                </div>
              </>
            )}
            <div className="sidebar-bottom">
              <button
                className={`nav-item ${page === "settings" ? "active" : ""}`}
                onClick={() => navigate("settings")}
              >
                <Settings2 size={17} />
                设置与学习偏好
              </button>
              <div className="local-label">
                <span className="status-dot" />
                {isDemo ? "自编课程 · 演示模式" : "本地运行 · 个人工作空间"}
              </div>
            </div>
          </aside>
        </>
      )}
      <main className="main">
        <header className="topbar">
          <div className="breadcrumb">
            {!sidebar && (
              <button
                className="icon-button"
                aria-label="展开导航"
                onClick={() => setSidebar(true)}
              >
                <PanelLeftOpen size={19} />
              </button>
            )}
            <span>{title}</span>
            <ChevronRight size={14} />
            <strong>
              {course?.name.replace(/^\d+[-_ ]*/, "") || "全部课程"}
            </strong>
          </div>
          <div className="top-actions">
            <span className={`mode-badge ${isDemo ? "demo" : ""}`}>
              {isDemo ? "演示回放" : "本地工作台"}
            </span>
            <button
              className="icon-button"
              aria-label="切换明暗主题"
              onClick={() =>
                setTheme(
                  document.documentElement.dataset.theme === "dark"
                    ? "light"
                    : "dark",
                )
              }
            >
              {document.documentElement.dataset.theme === "dark" ? (
                <Sun size={17} />
              ) : (
                <Moon size={17} />
              )}
            </button>
          </div>
        </header>
        {error && (
          <div className="alert error" role="alert">
            <CircleHelp size={17} />
            <span>{error}</span>
            <button
              className="icon-button"
              aria-label="关闭错误提示"
              onClick={() => setError("")}
            >
              <X size={15} />
            </button>
          </div>
        )}
        {notice && (
          <div className="alert notice" role="status">
            <CircleHelp size={17} />
            <span>{notice}</span>
            <button
              className="icon-button"
              aria-label="关闭提示"
              onClick={() => setNotice("")}
            >
              <X size={15} />
            </button>
          </div>
        )}
        {page === "chat" && (
          <section className="chat-page">
            <div className="chat-scroll">
              {messages.length === 0 ? (
                <div className="welcome">
                  <div className="welcome-symbol">
                    <BookOpen size={27} strokeWidth={1.4} />
                  </div>
                  <h1>把知识，学成自己的。</h1>
                  <p>
                    从一个没想透的问题开始。
                    <br />
                    结合课程找依据，用练习检验理解。
                  </p>
                  <div className="suggestions">
                    {[
                      "用一个例子解释 RAG 的工作过程",
                      "检索和重排分别解决什么问题？",
                      "课程没有答案时，助手应该怎么做？",
                    ].map((s, i) => (
                      <button key={s} onClick={() => send(s)} disabled={busy}>
                        <span className="suggestion-label">
                          {["理解概念", "比较方法", "应用到产品"][i]}
                        </span>
                        <span>{s}</span>
                        <ArrowUpRight size={16} />
                      </button>
                    ))}
                  </div>
                  <div className="grounding-note">
                    <ShieldCheck size={15} />
                    <span>回答附课程依据，资料不足时明确说明</span>
                  </div>
                  {isDemo && (
                    <div className="demo-intro">
                      当前为固定演示回放，使用 16 个自编课程片段。
                      <br />
                      真实模型模式可在配置完成后启用。
                    </div>
                  )}
                </div>
              ) : (
                <div className="message-list">
                  {messages.map((m, i) => (
                    <article
                      className={`message ${m.role} ${m.failed ? "failed" : ""}`}
                      key={i}
                    >
                      {m.role === "assistant" && (
                        <div className="assistant-label">
                          <span className="mini-mark">
                            <BookOpen size={13} />
                          </span>
                          学习教练{isDemo && <span>演示回放</span>}
                        </div>
                      )}
                      <div className="message-content">
                        {m.role === "assistant" ? (
                          <Content
                            text={m.content}
                            sources={m.sources || []}
                            onSource={setSource}
                          />
                        ) : (
                          m.content
                        )}
                      </div>
                      {!!m.sources?.length && (
                        <div className="source-list">
                          {m.sources.map((s, j) => (
                            <button key={s.id} onClick={() => setSource(s)}>
                              <FileText size={13} />
                              {j + 1}. {s.subtopic || s.doc_name}
                              <ChevronRight size={12} />
                            </button>
                          ))}
                        </div>
                      )}
                      {m.role === "assistant" &&
                        m.content &&
                        !m.failed &&
                        !busy && (
                          <button
                            className="text-button follow-up"
                            onClick={() => {
                              setTopic(
                                input ||
                                  messages[Math.max(0, i - 1)]?.content ||
                                  "",
                              );
                              navigate("practice");
                            }}
                          >
                            <ListChecks size={14} />
                            围绕这个知识点练习
                          </button>
                        )}
                    </article>
                  ))}
                  <div ref={bottom} />
                </div>
              )}
              {stage && (
                <div className="thinking" role="status">
                  <Loader2 size={15} className="spin" />
                  {stage}
                </div>
              )}
            </div>
            <div className="composer-wrap">
              <form
                className="composer"
                onSubmit={(e) => {
                  e.preventDefault();
                  send();
                }}
              >
                <textarea
                  ref={composer}
                  value={input}
                  onChange={(e) => setInput(e.target.value)}
                  placeholder={
                    isDemo
                      ? "输入问题，体验课程答疑演示…"
                      : "这里有哪个知识点，你还想再弄明白一点？"
                  }
                  aria-label="学习问题"
                  maxLength={4000}
                  rows={2}
                  disabled={busy}
                  onKeyDown={(e) => {
                    if (
                      e.key === "Enter" &&
                      !e.shiftKey &&
                      !e.nativeEvent.isComposing
                    ) {
                      e.preventDefault();
                      send();
                    }
                  }}
                />
                <div className="composer-tools">
                  <div className="scope-label">
                    <BookOpen size={14} />
                    <select
                      aria-label="检索课程范围"
                      value={courseId}
                      disabled={busy}
                      onChange={(e) => newChat(e.target.value)}
                    >
                      <option value="">全部课程</option>
                      {courses.map((c) => (
                        <option key={c.id} value={c.id}>
                          {c.name}
                        </option>
                      ))}
                    </select>
                    <ChevronDown size={12} />
                  </div>
                  <button
                    type="button"
                    className={`web-toggle ${web ? "on" : ""}`}
                    aria-pressed={web}
                    disabled={busy || isDemo}
                    onClick={() => setWeb(!web)}
                    title="开启后仅在需要时搜索外部资料"
                  >
                    <Globe2 size={14} />
                    <span>联网补充</span>
                  </button>
                  {busy ? (
                    <button
                      type="button"
                      className="send-button"
                      aria-label="停止生成"
                      onClick={() => controller.current?.abort()}
                    >
                      <Square size={14} />
                    </button>
                  ) : (
                    <button
                      type="submit"
                      className="send-button"
                      aria-label="发送问题"
                      disabled={!input.trim()}
                    >
                      <ArrowUp size={18} />
                    </button>
                  )}
                </div>
              </form>
              <p className="composer-foot">
                {isDemo
                  ? "演示回放不产生真实 AI 回答或学习评估"
                  : "请核对引用来源。模型回答可能有误。"}
                <span>Enter 发送 · Shift + Enter 换行</span>
              </p>
            </div>
          </section>
        )}
        {page === "practice" && (
          <section className="content-page">
            <div className="page-heading">
              <div>
                <p className="eyebrow">理解，比记住更进一步</p>
                <h1>用三道题，检验所学。</h1>
                <p>概念理解、方案选择，以及你的理由。</p>
              </div>
              {practice && (
                <button
                  className="secondary"
                  disabled={busy}
                  onClick={() => {
                    setPractice(null);
                    setAnswers({});
                  }}
                >
                  新一轮练习
                  <Plus size={15} />
                </button>
              )}
            </div>
            {!practice ? (
              <>
                <div className="practice-setup">
                  <label htmlFor="practice-topic">想检验哪个知识点？</label>
                  <input
                    id="practice-topic"
                    value={topic}
                    onChange={(e) => setTopic(e.target.value)}
                    placeholder="例如：为什么检索之后还需要重排"
                    maxLength={300}
                  />
                  <p>
                    范围：{course?.name || "全部课程"}
                    。留空时根据课程生成关键知识练习。
                  </p>
                  <button
                    className="primary"
                    onClick={() => makePractice()}
                    disabled={busy}
                  >
                    {busy ? (
                      <Loader2 className="spin" size={16} />
                    ) : (
                      <ListChecks size={16} />
                    )}{" "}
                    {isDemo ? "体验三题回放" : "生成三道练习题"}
                  </button>
                </div>
                <div className="workflow-note">
                  <div>
                    <span>理解</span>
                    <p>用自己的话解释概念</p>
                  </div>
                  <ChevronRight size={17} />
                  <div>
                    <span>应用</span>
                    <p>在产品情境中作选择</p>
                  </div>
                  <ChevronRight size={17} />
                  <div>
                    <span>复习</span>
                    <p>针对具体误解再练习</p>
                  </div>
                </div>
              </>
            ) : (
              <>
                {practice.demo && (
                  <div className="inline-note">
                    <CircleHelp size={17} />
                    以下为固定题目、示例作答和预设反馈，用于体验流程，不是对你的能力评价。
                  </div>
                )}
                <div className="questions">
                  {practice.questions.map((q: any, i: number) => {
                    const g = practice.result?.grades.find(
                      (v: any) => v.question_id === q.id,
                    );
                    return (
                      <article className="question" key={q.id}>
                        <div className="question-meta">
                          <span className="question-index">{i + 1}</span>
                          <span>
                            {q.kind === "scenario" ? "产品场景题" : "简答题"}
                          </span>
                          <span className="topic-tag">{q.topic}</span>
                          {g && (
                            <strong className="grade">
                              {g.correctness + g.completeness + g.reasoning}
                              <span> / 6</span>
                            </strong>
                          )}
                        </div>
                        <h2>{q.prompt}</h2>
                        <label className="sr-only" htmlFor={`answer-${q.id}`}>
                          第 {i + 1} 题作答
                        </label>
                        <textarea
                          id={`answer-${q.id}`}
                          value={answers[q.id] || ""}
                          onChange={(e) =>
                            setAnswers({ ...answers, [q.id]: e.target.value })
                          }
                          placeholder="写下你的理解，说明选择背后的理由…"
                          rows={4}
                          maxLength={2500}
                          readOnly={!!practice.result || practice.demo}
                          disabled={busy}
                        />
                        {g && (
                          <div className="feedback">
                            <div className="score-breakdown">
                              <span>概念 {g.correctness}/2</span>
                              <span>完整 {g.completeness}/2</span>
                              <span>理由 {g.reasoning}/2</span>
                            </div>
                            {g.understood && (
                              <p>
                                <strong>已理解</strong>
                                {g.understood}
                              </p>
                            )}
                            {g.missing && (
                              <p>
                                <strong>待补充</strong>
                                {g.missing}
                              </p>
                            )}
                            {g.misconception && (
                              <p>
                                <strong>需辨析</strong>
                                {g.misconception}
                              </p>
                            )}
                            <p>
                              <strong>下一步</strong>
                              {g.suggestion}
                            </p>
                            <details>
                              <summary>参考答案与评分标准</summary>
                              <p>{q.reference_answer}</p>
                              <ul>
                                {q.rubric.map((r: string) => (
                                  <li key={r}>{r}</li>
                                ))}
                              </ul>
                            </details>
                            <div className="source-list">
                              {q.source_ids.map((id: string) => (
                                <button
                                  key={id}
                                  onClick={() =>
                                    setSource(
                                      practice.sources.find(
                                        (s: Source) => s.id === id,
                                      ),
                                    )
                                  }
                                >
                                  <FileText size={13} />
                                  {id}
                                </button>
                              ))}
                            </div>
                          </div>
                        )}
                      </article>
                    );
                  })}
                </div>
                {!practice.result ? (
                  <div className="submit-row">
                    <span>完成三道题后，一起查看反馈。</span>
                    <button
                      className="primary"
                      disabled={
                        busy ||
                        practice.questions.some(
                          (q: any) => !answers[q.id]?.trim(),
                        )
                      }
                      onClick={submitPractice}
                    >
                      {busy ? (
                        <Loader2 size={16} className="spin" />
                      ) : (
                        <Check size={16} />
                      )}{" "}
                      {practice.demo ? "查看演示反馈" : "提交并查看反馈"}
                    </button>
                  </div>
                ) : (
                  <div className="coach-feedback">
                    <h2>接下来怎么学</h2>
                    <Content
                      text={practice.result.coaching}
                      sources={practice.sources}
                      onSource={setSource}
                    />
                    <button
                      className="secondary"
                      onClick={() => navigate("review")}
                    >
                      查看薄弱点复习 <ArrowUpRight size={15} />
                    </button>
                  </div>
                )}
              </>
            )}
            {stage && (
              <div className="thinking" role="status">
                <Loader2 size={15} className="spin" />
                {stage}
                {busy && (
                  <button
                    type="button"
                    className="text-button"
                    onClick={() => controller.current?.abort()}
                  >
                    停止任务
                  </button>
                )}
              </div>
            )}
          </section>
        )}
        {page === "review" && (
          <section className="content-page">
            <div className="page-heading">
              <div>
                <p className="eyebrow">从具体误解出发</p>
                <h1>让没想透的，再清楚一点。</h1>
                <p>这些复习建议来自已完成的练习，不代表对能力的最终判断。</p>
              </div>
            </div>
            {!reviews.length ? (
              <div className="empty-state">
                <RotateCcw size={30} strokeWidth={1.3} />
                <h2>还没有需要复习的知识点</h2>
                <p>先完成一轮练习。出现遗漏或误解时，这里会安排针对性复习。</p>
                <button
                  className="primary"
                  onClick={() => navigate("practice")}
                >
                  开始理解练习
                </button>
              </div>
            ) : (
              <>
                <p className="small-note">
                  按 1、3、7 天间隔安排；答错回到短间隔。同一天不会连续升级。
                  {isDemo ? "当前为演示记录。" : ""}
                </p>
                {reviews.map((r) => (
                  <article className="review-row" key={r.id}>
                    <div>
                      <span className={`review-due ${r.due ? "due" : ""}`}>
                        {r.due
                          ? "待复习"
                          : `${new Date(r.due_at).toLocaleDateString("zh-CN", { month: "long", day: "numeric" })}复习`}
                      </span>
                      <h2>{r.topic}</h2>
                      <p>{r.misconception}</p>
                    </div>
                    <div className="review-actions">
                      <button
                        className="text-button"
                        disabled={busy}
                        onClick={() =>
                          send(
                            `请帮我重新理解「${r.topic}」。上次的误解是：${r.misconception}`,
                            { courseId: r.course_id, skill: "review" },
                          )
                        }
                      >
                        再讲一遍
                      </button>
                      <button
                        className="secondary"
                        onClick={() => makePractice(r)}
                        disabled={busy}
                      >
                        变式练习 <ArrowUpRight size={14} />
                      </button>
                    </div>
                  </article>
                ))}
              </>
            )}
          </section>
        )}
        {page === "history" && (
          <section className="content-page">
            <div className="page-heading">
              <div>
                <p className="eyebrow">每一步都有迹可循</p>
                <h1>学习记录</h1>
                <p>回看问题、练习和真实反馈，找到下一步。</p>
              </div>
            </div>
            <h2 className="section-title">
              练习记录 <span>{practices.length}</span>
            </h2>
            {!practices.length && (
              <p className="muted">完成或创建一轮练习后，会显示在这里。</p>
            )}
            {practices.map((p) => (
              <button
                className="history-row"
                key={p.id}
                onClick={() => selectPractice(p)}
              >
                <ListChecks size={18} />
                <div>
                  <strong>{p.topic || "课程理解练习"}</strong>
                  <span>
                    {new Date(p.created_at).toLocaleString("zh-CN")} ·{" "}
                    {p.demo ? "演示回放" : p.result ? "已完成" : "待作答"}
                  </span>
                </div>
                <ChevronRight size={17} />
              </button>
            ))}
            <h2 className="section-title">
              历史对话 <span>{sessions.length}</span>
            </h2>
            {sessions.map((s) => (
              <div className="history-row" key={s.id}>
                <MessageSquare size={18} />
                <button
                  className="history-title"
                  onClick={() => openSession(s)}
                >
                  <strong>{s.title}</strong>
                  <span>{new Date(s.created_at).toLocaleString("zh-CN")}</span>
                </button>
                <button
                  className="icon-button"
                  aria-label={`删除对话 ${s.title}`}
                  onClick={() => {
                    if (confirm("删除这段对话及运行追踪？")) {
                      remove(`/sessions/${s.id}`);
                      if (sessionId === s.id) newChat();
                    }
                  }}
                >
                  <Trash2 size={16} />
                </button>
              </div>
            ))}
            <details className="trace-panel">
              <summary>运行记录 · {traces.length} 次</summary>
              <p>仅记录流程节点、工具、耗时和 Token，不展示内部思维链。</p>
              {traces.map((t) => (
                <details key={t.id}>
                  <summary>
                    {t.kind} · {t.status} · {t.elapsed_ms || 0} ms ·{" "}
                    {t.mode === "demo" ? "固定回放" : t.model}
                  </summary>
                  <pre>{JSON.stringify(t, null, 2)}</pre>
                </details>
              ))}
            </details>
            <button
              className="danger-text"
              disabled={busy}
              onClick={() => {
                if (
                  confirm(
                    "删除所有练习、复习及对应运行记录？学习偏好和对话保留。",
                  )
                ) {
                  remove("/learning-records");
                  setPractice(null);
                }
              }}
            >
              <Trash2 size={14} />
              清除练习与复习记录
            </button>
          </section>
        )}
        {page === "settings" && (
          <section className="content-page">
            <div className="page-heading">
              <div>
                <p className="eyebrow">由你决定如何学习</p>
                <h1>设置与学习偏好</h1>
                <p>只保存你明确提供的信息，可随时修改或删除。</p>
              </div>
            </div>
            <h2 className="section-title">学习偏好</h2>
            {prefs.map((p) => (
              <div className="preference-row" key={p.key}>
                <div>
                  <strong>{prefNames[p.key]}</strong>
                  <p>{p.value}</p>
                </div>
                <button
                  className="icon-button"
                  aria-label={`修改${prefNames[p.key]}`}
                  onClick={() => {
                    setPrefKey(p.key);
                    setPrefValue(p.value);
                  }}
                >
                  编辑
                </button>
                <button
                  className="icon-button"
                  aria-label={`删除${prefNames[p.key]}`}
                  disabled={busy}
                  onClick={() => remove(`/preferences/${p.key}`)}
                >
                  <Trash2 size={16} />
                </button>
              </div>
            ))}
            <div className="preference-form">
              <label htmlFor="pref-key">新增或修改偏好</label>
              <select
                id="pref-key"
                value={prefKey}
                onChange={(e) => setPrefKey(e.target.value)}
              >
                {Object.entries(prefNames).map(([k, v]) => (
                  <option key={k} value={k}>
                    {v}
                  </option>
                ))}
              </select>
              <textarea
                aria-label="偏好内容"
                value={prefValue}
                onChange={(e) => setPrefValue(e.target.value)}
                placeholder="例如：我有传统产品经验，请多用业务场景解释技术概念。"
                maxLength={400}
                rows={3}
              />
              <button
                className="primary"
                disabled={!prefValue.trim() || busy}
                onClick={savePref}
              >
                保存偏好
              </button>
            </div>
            <h2 className="section-title">外观</h2>
            <div className="theme-options" aria-label="主题">
              {[
                ["system", "跟随系统"],
                ["light", "浅色"],
                ["dark", "深色"],
              ].map(([v, l]) => (
                <button
                  key={v}
                  className={theme === v ? "selected" : ""}
                  aria-pressed={theme === v}
                  onClick={() => setTheme(v)}
                >
                  {l}
                </button>
              ))}
            </div>
            <h2 className="section-title">运行状态</h2>
            <dl className="status-grid">
              <dt>当前模式</dt>
              <dd>{isDemo ? "固定演示回放，不调用 LLM" : "真实模型运行"}</dd>
              <dt>课程资料</dt>
              <dd>
                {status?.dataset === "private"
                  ? "本地私有课程"
                  : "公开自编样例"}{" "}
                · {status?.parent_count || 0} 个片段
              </dd>
              <dt>语言模型</dt>
              <dd>{status?.llm_ready ? status.llm_model : "未配置"}</dd>
              <dt>回答推理强度</dt>
              <dd>
                {status?.coach_reasoning_effort === "low"
                  ? "轻度推理"
                  : status?.coach_reasoning_effort || "服务默认"}
              </dd>
              <dt>评分推理强度</dt>
              <dd>{status?.assessor_reasoning_effort || "服务默认"}</dd>
              <dt>本地索引</dt>
              <dd>{status?.index_ready ? "已构建" : "尚未构建"}</dd>
              <dt>推理设备</dt>
              <dd>
                {status?.device === "not_loaded"
                  ? "尚未加载模型"
                  : status?.device}
              </dd>
              <dt>联网搜索</dt>
              <dd>
                {status?.search_access_mode === "keyless"
                  ? "免密免费访问（受服务限额约束）"
                  : status?.search_ready
                    ? "已配置密钥"
                    : "未配置"}{" "}
                · 本月 {status?.search_used || 0}/{status?.search_limit || 300}{" "}
                次
              </dd>
            </dl>
            <details className="setup-help">
              <summary>如何启用真实模型？</summary>
              <ol>
                <li>在项目的 .env 中填写接口地址、模型名称和密钥。</li>
                <li>运行模型下载与索引构建脚本。</li>
                <li>
                  设置 APP_MODE=live，选择 DATASET=sample 或
                  private，再重新启动。
                </li>
              </ol>
              <p>免费搜索密钥为可选项。详细操作与兼容性检查见项目 README。</p>
            </details>
          </section>
        )}
      </main>
      {source && (
        <>
          <button
            className="source-scrim"
            aria-label="关闭来源面板"
            onClick={() => setSource(null)}
          />
          <aside className="source-panel" aria-label="来源详情">
            <header>
              <span>
                <FileText size={16} />
                来源详情
              </span>
              <button
                className="icon-button"
                aria-label="关闭来源"
                onClick={() => setSource(null)}
              >
                <X size={18} />
              </button>
            </header>
            <div className="source-body">
              <span className="source-type">
                {source.type === "web" ? "外部补充" : "课程依据"}
              </span>
              <h2>{source.subtopic || source.doc_name}</h2>
              <p className="source-course">{source.doc_name}</p>
              <div className="source-id">
                {source.id}
                {source.version && ` · ${source.version.slice(0, 8)}`}
              </div>
              <hr />
              <p className="source-text">{source.chunk_text}</p>
              {source.url && (
                <a
                  className="external-source"
                  href={source.url}
                  target="_blank"
                  rel="noreferrer"
                >
                  打开原始网页 <ArrowUpRight size={15} />
                </a>
              )}
              {!!source.quality_flags?.length && (
                <p className="inline-note">
                  此片段含待复核标记，请结合课程语境核对。
                </p>
              )}
              <div className="source-disclaimer">
                <ShieldCheck size={16} />
                <p>
                  {source.type === "web"
                    ? "外部资料不属于课程结论，也不会自动写入课程知识库。"
                    : "这是课程整理片段。引用定位到片段，不代表已经核对全部事实。"}
                </p>
              </div>
            </div>
          </aside>
        </>
      )}
    </div>
  );
}
