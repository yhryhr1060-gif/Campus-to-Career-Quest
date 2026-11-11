import { useState } from "react";
import type { FeedbackType, Judgement } from "../types";
import { isRetryableRecheck, visibleGroups, wordDiff, type FeedbackView, type PointStatus, type PointView } from "../state/feedbackView";

const TYPE: Record<FeedbackType, string> = {
  grammar: "Grammar · 语法", accuracy: "Word choice · 表达准确", logic: "Logic · 逻辑",
  spelling: "Spelling · 拼写", punctuation: "Punctuation · 标点", capitalization: "Capitalization · 大小写",
  task_info: "Task information · 任务信息", politeness: "Politeness · 礼貌", naturalness: "Naturalness · 自然度",
  positive: "Done well · 用得好",
};
const JUDGEMENT: Record<Judgement, { label: string; cls: string }> = {
  clear_error: { label: "Clear error · 明确错误", cls: "fb-error" },
  expression_issue: { label: "Could be clearer · 表达问题", cls: "fb-info" },
  optional: { label: "Optional · 可选建议", cls: "fb-upgrade" },
  uncertain: { label: "AI is not sure · 有争议/不确定", cls: "fb-uncertain" },
};
const STATUS: Record<PointStatus, string> = {
  open: "", praise: "",
  resolved: "Resolved · 已解决",
  still_present: "Still there · 仍存在",
  kept_by_user: "You kept your wording · 你选择保留",
  cannot_judge: "Can't tell · 无法判断",
  not_rechecked: "Not rechecked yet · 尚未复核",
};
const GROUP_TITLE: Record<PointView["group"], string> = {
  layer1: "Start here · 先看这些", layer2: "Language, accuracy and logic · 语言、准确与逻辑",
  layer3: "Optional polish · 可选优化", uncertain: "", praise: "",
};
const FLAG: Record<string, string> = {
  changes_fact: "This suggestion would change a date or number you wrote. Check that it still says what you mean.",
  adds_unstated_fact: "This suggestion adds a detail you did not write. Only use it if it is true.",
  changes_negation: "This suggestion changes a negative into a positive (or the reverse). Check the meaning.",
  large_rewrite: "This rewrites more than the problem needs. A smaller change of your own may be better.",
  reviewer_disagrees: "A second AI reading disagreed with this point.",
  reviewer_unsure: "A second AI reading was not sure about this point.",
  not_reviewed: "This point did not get a second AI reading.",
  style_downgraded: "",
};
const BASIS: Record<string, string> = {
  sentence_unchanged: "That sentence is unchanged.",
  ai_unavailable: "The recheck could not run for this point. You can retry.",
  ai_no_answer: "The recheck gave no answer for this point. You can retry.",
  ai_unsure: "The recheck could not decide.",
  evidence_not_in_draft: "The recheck quoted words that are not in your draft, so its answer was discarded. You can retry.",
  evidence_missing: "The recheck did not provide the required evidence. Its answer was discarded. You can retry.",
  context_recheck_needed: "This earlier sentence-only check needs the revised context. You can retry.",
};

function Point({ view, busy, canDecide, onDecision }: {
  view: PointView; busy: boolean; canDecide: boolean; onDecision: (id: string, decision: "keep" | "clear") => void;
}) {
  const { point, status, recheck } = view;
  const tone = point.type === "positive" ? "fb-positive" : JUDGEMENT[point.judgement].cls;
  const notes = point.flags.map((f) => FLAG[f]).filter(Boolean);
  const retryable = recheck ? isRetryableRecheck(recheck) : false;
  const recheckNote = recheck ? (retryable ? BASIS[recheck.basis] : recheck.note || BASIS[recheck.basis]) : "";
  return (
    <div className={`fb ${tone} ${status === "resolved" ? "fb-done" : ""}`}>
      <div className="fb-label">
        {TYPE[point.type]}{point.type !== "positive" && <> — {JUDGEMENT[point.judgement].label}</>}
      </div>
      {STATUS[status] && <div className={`fb-status fb-status-${status}`}>
        {retryable ? "Recheck incomplete · 复核未完成，可重试" : STATUS[status]}
      </div>}
      <div className="quote">“{point.quote}”</div>
      {point.suggestion && <div>→ {point.suggestion}</div>}
      <div className="muted small">{point.reason}</div>
      {notes.map((note) => <div key={note} className="fb-note small">{note}</div>)}
      {point.review_note && <div className="fb-note small">Second reading: {point.review_note}</div>}
      {recheck && recheckNote && (
        <div className="fb-note small">Recheck: {recheckNote}
          {recheck.current_quote && status !== "still_present" && status !== "kept_by_user" && <> Now: “{recheck.current_quote}”</>}
        </div>
      )}
      {canDecide && (status === "open" || status === "still_present" || status === "not_rechecked") && (
        <button className="link small" disabled={busy} onClick={() => onDecision(point.id, "keep")}>Keep my wording · 保留我的写法</button>
      )}
      {canDecide && status === "kept_by_user" && (
        <button className="link small" disabled={busy} onClick={() => onDecision(point.id, "clear")}>Undo · 撤销保留</button>
      )}
    </div>
  );
}

export function WritingFeedback({ view, draft, loading, busy, onDecision }: {
  view: FeedbackView; draft: string; loading: "check" | "review" | "recheck" | null; busy: boolean;
  onDecision: (id: string, decision: "keep" | "clear") => void;
}) {
  const [revealed, setRevealed] = useState(1);
  const [showUncertain, setShowUncertain] = useState(false);
  const [compare, setCompare] = useState(false);
  const base = view.base;
  const { shown, next } = visibleGroups(view, revealed);
  const count = (group: PointView["group"]) => view.points.filter((p) => p.group === group).length;
  const uncertain = view.points.filter((p) => p.group === "uncertain");
  const issues = view.points.filter((p) => p.status !== "praise");
  const point = (p: PointView) => <Point key={p.point.id} view={p} busy={busy} canDecide onDecision={onDecision} />;

  return (
    <aside className="feedback">
      <h3>Writing teacher (AI) · AI 老师</h3>
      <p className="muted small">
        AI feedback can be wrong and cannot find every error. It is advice: you decide what to change and when to send.
      </p>
      <p className="muted small">Checks cover clear spelling, punctuation and capitalization errors, grammar, word choice,
        logic and task information. Politeness and naturalness suggestions are optional.</p>
      {loading && <p className="muted small" role="status">{loading === "check" ? "Checking this version…"
        : loading === "review" ? "Retrying the second reading… Your first-pass feedback is kept."
          : "Rechecking the earlier points…"}</p>}
      {view.checkPending && !loading && <p className="fb-note small" role="status">
        A check was started for this text, but no completed result is available yet. Retry to recover it; your draft is kept.
      </p>}
      {view.checkFailed && !loading && (
        <p className="fb-alert small" role="status">
          The check of the current text did not finish, so it has <strong>not been assessed</strong>. Your text is safe. You can retry the check.
        </p>
      )}
      {!base && !loading && !view.checkFailed && (
        <p className="muted small">Ask for a check to get feedback on this draft. The most important points are shown first.</p>
      )}
      {base && (
        <>
          <div className={`fb-version small ${view.stale ? "fb-version-old" : ""}`} role="status">
            <strong>Feedback on version {base.version}</strong>
            {!view.stale && " — the text in the editor."}
            {view.stale && !view.rechecked && (
              <> — <strong>an earlier version · 旧版反馈</strong>. You have edited since, so these points describe version {base.version}, not what is in the editor now. Use “Recheck earlier points” to see what your changes did.</>
            )}
            {view.stale && view.rechecked && (
              <> — <strong>an earlier version</strong>, rechecked against your current text. New problems are only found by a full check.</>
            )}
          </div>
          {view.stale && (
            <button className="link small" onClick={() => setCompare(!compare)}>
              {compare ? "Hide comparison" : `Compare with version ${base.version} · 修改前后对照`}
            </button>
          )}
          {view.stale && compare && (
            <div className="diff" aria-label={`Changes since version ${base.version}`}>
              {wordDiff(base.text, draft).map((part, i) =>
                part.kind === "same" ? <span key={i}>{part.text}</span>
                  : part.kind === "removed" ? <del key={i}>{part.text}</del> : <ins key={i}>{part.text}</ins>)}
            </div>
          )}
          {view.reviewRetryable && (
            <p className="fb-note small" role="status">
              The second reading {view.reviewUnavailable ? "has not finished" : "did not return an answer for every point"}.
              {view.reviewUnavailable && " It may still be running or may have failed."}
              {" "}Your first-pass feedback is kept; these points have not all had a second reading.
              {!view.stale ? " Use “Retry second reading” to recover the saved result or retry only the unfinished part."
                : " This belongs to the earlier version. Check your current draft to get feedback on its wording."}
            </p>
          )}
          {view.recheckIncomplete && (
            <p className="fb-note small" role="status">Some recheck answers failed, were missing or had invalid evidence.
              Use “Retry recheck” to retry those points. Valid results are kept.</p>
          )}
          {view.limited && (
            <p className="fb-alert small" role="status">
              Feedback may be incomplete · 反馈可能不完整. Each full check shows at most {view.itemLimit} points.
              {view.omittedCount > 0 && <> {view.omittedCount} additional point{view.omittedCount === 1 ? " was" : "s were"} omitted by this limit.</>}
              {" "}There is no next batch for unchanged text. Work through these points, revise, then run a full check of the revised draft.
              Another check may repeat unresolved points and still miss errors.
            </p>
          )}
          {view.invalidCount > 0 && (
            <p className="fb-note small">{view.invalidCount} model point{view.invalidCount === 1 ? " was" : "s were"} discarded
              because the evidence or required details were invalid. This is not evidence that the draft has no problems.</p>
          )}
          {issues.length === 0 && (
            <p className="muted small">No points were returned for version {base.version}. That does not mean it has no errors — read it once more yourself.</p>
          )}
          {shown.map((group) => (
            <section key={group}>
              <div className="fb-group small">{GROUP_TITLE[group]} ({count(group) + (group === "layer3" ? count("praise") : 0)})</div>
              {view.points.filter((p) => p.group === group).map(point)}
              {group === "layer3" && view.points.filter((p) => p.group === "praise").map(point)}
            </section>
          ))}
          {next && (
            <button className="link small" onClick={() => setRevealed(revealed + 1)}>
              Show next: {GROUP_TITLE[next]} ({count(next) + (next === "layer3" ? count("praise") : 0)})
            </button>
          )}
          {uncertain.length > 0 && (
            <section>
              <div className="fb-group small">
                AI is not sure about {uncertain.length} point{uncertain.length > 1 ? "s" : ""} · 有争议/不确定
              </div>
              <p className="muted small">These may be wrong or a matter of opinion. Decide for yourself, or ask a teacher.</p>
              {showUncertain ? uncertain.map(point)
                : <button className="link small" onClick={() => setShowUncertain(true)}>Show these points</button>}
            </section>
          )}
        </>
      )}
    </aside>
  );
}
