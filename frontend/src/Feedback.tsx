import { useId, useState } from "react";
import { MessageSquareWarning, Check, X } from "lucide-react";
import { api } from "./api";

export const reasonNames: Record<string, string> = {
  accuracy: "回答不准确",
  citation: "引用不能支持回答",
  unhelpful: "没有解决我的问题",
  grading: "评分有疑问",
  performance: "等待时间过长",
  system_error: "运行出错",
  format: "输出格式异常",
  tool: "工具调用异常",
  other: "其他问题",
};

export default function Feedback({
  runId,
  grading = false,
  disabled = false,
  onOpen,
}: {
  runId?: string;
  grading?: boolean;
  disabled?: boolean;
  onOpen: (caseId: string) => void;
}) {
  const id = useId();
  const [open, setOpen] = useState(false),
    [reason, setReason] = useState(grading ? "grading" : "accuracy"),
    [comment, setComment] = useState(""),
    [saving, setSaving] = useState(false),
    [error, setError] = useState(""),
    [caseId, setCaseId] = useState("");
  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setSaving(true);
    setError("");
    try {
      const result = await api("/feedback", {
        method: "POST",
        body: JSON.stringify({ run_id: runId, reason, comment }),
      });
      setCaseId(result.case_id);
      setOpen(false);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setSaving(false);
    }
  }
  if (!runId) return null;
  return (
    <div className="quality-feedback">
      {caseId ? (
        <p className="feedback-recorded" role="status">
          <Check size={14} />
          已记录为待核查反馈。
          <button className="text-button" onClick={() => onOpen(caseId)}>
            查看问题案例
          </button>
        </p>
      ) : (
        <button
          className="text-button"
          disabled={disabled || saving}
          onClick={() => setOpen(!open)}
          aria-expanded={open}
        >
          <MessageSquareWarning size={14} />
          {grading ? "评分有疑问" : "反馈问题"}
        </button>
      )}
      {open && (
        <form className="feedback-form" onSubmit={submit}>
          <div className="quality-form-row">
            <label htmlFor={id + "reason"}>问题类型</label>
            <select
              id={id + "reason"}
              value={reason}
              onChange={(e) => setReason(e.target.value)}
            >
              {Object.entries(reasonNames)
                .filter(([k]) => !["format", "tool"].includes(k))
                .map(([k, v]) => (
                  <option value={k} key={k}>
                    {v}
                  </option>
                ))}
            </select>
          </div>
          <label htmlFor={id + "comment"}>补充说明（可选）</label>
          <textarea
            id={id + "comment"}
            rows={2}
            maxLength={1500}
            value={comment}
            onChange={(e) => setComment(e.target.value)}
            placeholder="例如：来源讲的是召回，回答却解释成了重排。"
          />
          <p className="small-note">
            附带本次输入、输出与必要证据，仅保存在本机。反馈经核查后才认定为产品问题。
          </p>
          {error && (
            <p className="quality-error" role="alert">
              {error}
            </p>
          )}
          <div className="quality-actions">
            <button type="submit" className="secondary" disabled={saving}>
              {saving ? "正在记录…" : "提交反馈"}
            </button>
            <button
              type="button"
              className="text-button"
              onClick={() => setOpen(false)}
              disabled={saving}
            >
              <X size={14} />
              取消
            </button>
          </div>
        </form>
      )}
    </div>
  );
}
