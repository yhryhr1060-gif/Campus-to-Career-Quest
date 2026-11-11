import { useEffect, useRef, useState } from "react";
import { api, newRequestId } from "../api";
import type { PageProps } from "../App";
import { useAutosave } from "../hooks/useAutosave";
import { codePointLength, type SaveSnapshot } from "../state/autosave";
import { buildFeedbackView } from "../state/feedbackView";
import { WritingFeedback } from "../components/WritingFeedback";
import { OperationGate } from "../state/operationGate";
import type { SessionState } from "../types";

const FIELD_LABEL: Record<string, string> = { situation: "Situation", request: "Request", proposal: "Proposal" };

type Thoughts = { prediction: string; backup: string };
function isThoughts(value: unknown): value is Thoughts {
  return typeof value === "object" && value !== null && "prediction" in value && "backup" in value &&
    typeof value.prediction === "string" && typeof value.backup === "string";
}

function SaveStatus({ state, retry, busy }: { state: SaveSnapshot<unknown>; retry: () => void; busy: boolean }) {
  const local = state.localSaved ? "Your changes are saved in this browser." : "Keep this page open until saving succeeds.";
  return (
    <div className={`save-status small save-${state.status}`} role="status" aria-live="polite">
      {state.status === "saved" && "Saved on server."}
      {state.status === "pending" && `Waiting to save. ${local}`}
      {state.status === "saving" && `Saving… ${local}`}
      {state.status === "blocked" && `Above the limit; not saved to the server. ${local}`}
      {state.status === "error" && (
        <><span>Couldn't save to the server. {local}</span> <button className="link" disabled={busy} onClick={retry}>Retry saving</button></>
      )}
    </div>
  );
}

function CharacterCount({ value, limit, id }: { value: string; limit: number; id: string }) {
  const count = codePointLength(value);
  return <div id={id} className={`input-count small ${count > limit ? "over-limit" : "muted"}`} role={count > limit ? "status" : undefined}>
    {count} / {limit} characters{count > limit && ` — shorten by ${count - limit} before continuing. Your full text is kept.`}
  </div>;
}

export default function Prepare({ content, session, setSession, report, goTo, setLeaveGuard }: PageProps) {
  const sid = session.session_id;
  const prep = session.prep;
  const limits = content.input_limits;
  const draftSave = useAutosave({
    key: `ccq:${sid}:prep:draft`, initial: prep.draft,
    enabled: !prep.email_final && !prep.done,
    isValue: (value): value is string => typeof value === "string",
    valid: (value) => codePointLength(value) <= limits.email,
    save: (value) => api.saveDraft(sid, value),
  });
  const thoughtsSave = useAutosave<Thoughts>({
    key: `ccq:${sid}:prep:thoughts`, initial: { prediction: prep.prediction ?? "", backup: prep.backup_plan ?? "" },
    enabled: prep.summary_confirmed && !prep.done, isValue: isThoughts,
    valid: (value) => codePointLength(value.prediction) <= limits.prediction && codePointLength(value.backup) <= limits.backup_plan,
    save: (value) => api.savePrediction(sid, value.prediction, value.backup),
  });
  const draft = draftSave.value;
  const { prediction, backup } = thoughtsSave.value;
  const draftTooLong = codePointLength(draft) > limits.email;
  const thoughtsTooLong = codePointLength(prediction) > limits.prediction || codePointLength(backup) > limits.backup_plan;
  // Feedback lives on the server with the draft version it was written for (session.prep.versions),
  // so it survives a refresh and can never be mistaken for feedback on newer text.
  const view = buildFeedbackView(prep.versions ?? [], draft);
  const [feedbackLoading, setFeedbackLoading] = useState<"check" | "review" | "recheck" | null>(null);
  const [confirmSend, setConfirmSend] = useState(false);
  const [busy, setBusy] = useState(false);
  const [operations] = useState(() => new OperationGate());
  const pendingFeedback = useRef<{ key: string; id: string } | null>(null);
  useEffect(() => {
    operations.activate();
    return () => operations.deactivate();
  }, [operations]);
  // Going back to campus first writes the latest draft to the server. Text over the limit cannot be
  // stored there; it stays in this browser, as the status line under the editor already says.
  const leave = useRef<() => Promise<void>>(async () => undefined);
  leave.current = async () => {
    if (!draftTooLong) await draftSave.flush();
    if (!thoughtsTooLong) await thoughtsSave.flush();
  };
  useEffect(() => {
    setLeaveGuard(() => leave.current());
    return () => setLeaveGuard(null);
  }, [setLeaveGuard]);
  const backToCampus = (
    <button className="back-campus" disabled={busy} onClick={() => { void goTo("explore"); }}>
      ← Back to campus · 回到校园 <span className="muted small">(your writing is saved first)</span>
    </button>
  );
  const acceptSession = (token: number, next: SessionState) => {
    if (operations.accepts(token)) setSession(next);
  };

  const recoverAdvancedState = async (token: number, allowEditRecovery = false): Promise<boolean> => {
    try {
      const fresh = await api.getSession(sid);
      if (!operations.accepts(token)) return false;
      const editedAgain = allowEditRecovery && !!prep.email_final && !fresh.prep.email_final &&
        !fresh.prep.summary_confirmed && !fresh.prep.done &&
        (fresh.phase === "explore" || fresh.phase === "prepare");
      const advanced = (!prep.email_final && !!fresh.prep.email_final) ||
        (!prep.summary_confirmed && fresh.prep.summary_confirmed) || fresh.prep.done ||
        fresh.phase === "negotiate" || fresh.phase === "outcome";
      if (!advanced && !editedAgain) return false;
      if (editedAgain) draftSave.reset(fresh.prep.draft);
      if (fresh.prep.email_final) draftSave.discardCache();
      if (fresh.prep.done) thoughtsSave.discardCache();
      acceptSession(token, fresh);
      return true;
    } catch { return false; }
  };

  const run = async (fn: (token: number) => Promise<boolean | void>, allowEditRecovery = false) => {
    const token = operations.begin();
    if (token === null) return;
    setBusy(true);
    let advanced = false;
    try {
      // Finish every old save before an operation that changes the preparation state.
      await draftSave.flush();
      await thoughtsSave.flush();
      if (!operations.accepts(token)) return;
      draftSave.pause();
      thoughtsSave.pause();
      advanced = (await fn(token)) === true;
    } catch (e) {
      if (!operations.accepts(token)) return;
      draftSave.pause();
      thoughtsSave.pause();
      advanced = await recoverAdvancedState(token, allowEditRecovery);
      if (!advanced && operations.accepts(token)) report(e);
    } finally {
      if (operations.accepts(token)) {
        if (!advanced) { draftSave.resume(); thoughtsSave.resume(); }
        setBusy(false);
        operations.finish(token);
      }
    }
  };

  const withFeedback = (kind: "check" | "recheck") => run(async (token) => {
    setFeedbackLoading(kind === "check" && !view.stale && view.reviewRetryable ? "review" : kind);
    // A lost response reuses its request ID. An explicit retry after a received failure starts a new attempt.
    const key = `${kind}:${draft}`;
    if (pendingFeedback.current?.key !== key) pendingFeedback.current = { key, id: newRequestId() };
    const requestId = pendingFeedback.current.id;
    try {
      const next = kind === "check" ? await api.feedback(sid, draft, requestId) : await api.recheck(sid, draft, requestId);
      if (!operations.accepts(token)) return;
      pendingFeedback.current = null;
      acceptSession(token, next);
    } catch (e) {
      // The server stores the result with its version even if this page stopped waiting for it.
      if (!operations.accepts(token)) return;
      try { acceptSession(token, await api.getSession(sid)); } catch { /* keep what is on screen */ }
      throw e;
    } finally {
      if (operations.accepts(token)) setFeedbackLoading(null);
    }
  });

  const decide = (id: string, decision: "keep" | "clear") => run(async (token) => {
    if (view.base) acceptSession(token, await api.feedbackDecision(sid, view.base.version, id, decision));
  });

  const sendNow = () => run(async (token) => {
    const next = await api.send(sid, draft, confirmSend);
    if (!operations.accepts(token)) return;
    draftSave.discardCache();
    setConfirmSend(false);
    acceptSession(token, next);
    return true;
  });
  // Open points never block sending: they are shown once, then the learner decides.
  const needsReminder = view.open.length > 0 || view.uncertainCount > 0 || view.checkFailed || view.checkPending ||
    view.reviewRetryable || view.limited;

  // ---- step 3: predict, then go to the office
  if (prep.summary_confirmed) {
    return (
      <div className="page">
        {backToCampus}
        <h2>Prepare · Before you knock</h2>
        <p>Your email has been sent. Before you go in, jot down two quick thoughts. They are only recorded, not scored.</p>
        <label className="field">
          <span>What do you think Prof. Chen will say?</span>
          <textarea rows={3} value={prediction} disabled={busy} aria-describedby="prediction-count"
            onChange={(e) => thoughtsSave.update({ ...thoughtsSave.value, prediction: e.target.value })} />
          <CharacterCount value={prediction} limit={limits.prediction} id="prediction-count" />
        </label>
        <label className="field">
          <span>Your backup plan, if he says no:</span>
          <textarea rows={3} value={backup} disabled={busy} aria-describedby="backup-count"
            onChange={(e) => thoughtsSave.update({ ...thoughtsSave.value, backup: e.target.value })} />
          <CharacterCount value={backup} limit={limits.backup_plan} id="backup-count" />
        </label>
        <SaveStatus state={thoughtsSave} busy={busy} retry={() => run(async () => undefined)} />
        <button
          className="primary"
          disabled={busy || thoughtsTooLong}
          onClick={() => run(async (token) => {
            const next = await api.prediction(sid, prediction, backup);
            if (!operations.accepts(token)) return;
            thoughtsSave.discardCache();
            acceptSession(token, next);
            return true;
          })}
        >
          Go to Prof. Chen's office →
        </button>
      </div>
    );
  }

  // ---- step 2: check the summary Prof. Chen will read
  if (prep.email_final) {
    const summary = prep.summary ?? [];
    return (
      <div className="page">
        {backToCampus}
        <h2>Prepare · Email sent</h2>
        <div className="card">
          <div className="card-kicker">Prof. Chen will read these points.</div>
          {summary.length === 0 ? (
            <p className="muted">
              No points could be checked against your email's own words. Prof. Chen will ask you about the details.
            </p>
          ) : (
            <ul className="summary">
              {summary.map((f) => (
                <li key={f.field}>
                  <strong>{FIELD_LABEL[f.field]}:</strong> {f.value}
                  <div className="quote">from your email: “{f.quote}”</div>
                </li>
              ))}
            </ul>
          )}
        </div>
        <div className="row">
          <button
            disabled={busy}
            onClick={() =>
              run(async (token) => {
                const s = await api.editAgain(sid);
                if (!operations.accepts(token)) return;
                draftSave.reset(s.prep.draft);
                acceptSession(token, s);
                return true;
              }, true)
            }
          >
            ← Back to edit
          </button>
          <button className="primary" disabled={busy} onClick={() => run(async (token) => {
            acceptSession(token, await api.confirmSummary(sid));
            return true;
          })}>
            Confirm
          </button>
        </div>
      </div>
    );
  }

  // ---- step 1: write the email
  return (
    <div className="page">
      {backToCampus}
      <h2>Prepare · Write to Prof. Chen</h2>
      <p className="muted">
        Explain your situation and suggest a plan. Prof. Chen will read the main points before you meet.
      </p>
      <div className="prep-grid">
        <div>
          <textarea
            className="email"
            rows={14}
            value={draft}
            disabled={busy}
            placeholder="Dear Prof. Chen,…"
            onChange={(e) => { draftSave.update(e.target.value); setConfirmSend(false); }}
            aria-label="Email to Prof. Chen"
            aria-describedby="email-count"
          />
          <CharacterCount value={draft} limit={limits.email} id="email-count" />
          <SaveStatus state={draftSave} busy={busy} retry={() => run(async () => undefined)} />
          <div className="row">
            <button disabled={busy || draftTooLong || !draft.trim() || (!view.stale && !!view.base && !view.reviewRetryable)}
              title={!view.stale && view.base && !view.reviewRetryable ? "This version already has a saved check. This is not a guarantee that all errors were found." : undefined}
              onClick={() => withFeedback("check")}>
              {feedbackLoading === "review" ? "Retrying second reading…" : feedbackLoading === "check" ? "Checking…"
                : !view.stale && view.reviewRetryable ? "Retry second reading · 重试二次检查"
                : view.checkFailed || view.checkPending ? "Retry check · 重试"
                : view.base ? "Full check of this version · 完整检查" : "Check this draft · 检查草稿"}
            </button>
            {view.base && view.stale && (
              <button disabled={busy || draftTooLong || !draft.trim() || (view.rechecked && !view.recheckIncomplete)}
                onClick={() => withFeedback("recheck")}>
                {feedbackLoading === "recheck" ? "Rechecking…" : view.recheckIncomplete ? "Retry recheck · 重试复核" : "Recheck earlier points · 复核修改"}
              </button>
            )}
            <button
              className="primary"
              disabled={busy || draftTooLong || codePointLength(draft.trim()) < 20}
              onClick={() => (needsReminder && !confirmSend ? setConfirmSend(true) : sendNow())}
            >
              Send email
            </button>
          </div>
          {codePointLength(draft.trim()) < 20 && draft.trim() !== "" && (
            <p className="muted small">An email needs at least 20 characters before it can be sent. This is a limit of the game, not language feedback.</p>
          )}
          {confirmSend && (
            <div className="send-check" role="alertdialog" aria-label="Before you send">
              <strong>Before you send · 发送前提醒</strong>
              <ul className="small">
                {view.open.length > 0 && view.feedbackState === "changed_since_check" && (
                  <li>You changed the text after the check, and {view.open.length} earlier point{view.open.length > 1 ? "s have" : " has"} not been rechecked.</li>
                )}
                {view.open.length > 0 && view.feedbackState !== "changed_since_check" && (
                  <li>{view.open.length} point{view.open.length > 1 ? "s are" : " is"} still open (not changed, or the recheck could not tell).</li>
                )}
                {view.uncertainCount > 0 && <li>{view.uncertainCount} point{view.uncertainCount > 1 ? "s are" : " is"} marked “AI is not sure”.</li>}
                {view.checkFailed && <li>The check of this exact text did not finish, so it has not been assessed.</li>}
                {view.checkPending && <li>The check of this text is still pending. Its result may arrive later.</li>}
                {view.reviewRetryable && <li>Some first-pass points still need a second reading because that step failed or omitted an answer.</li>}
                {view.limited && <li>This feedback may be incomplete. A check returns at most {view.itemLimit} points.</li>}
              </ul>
              <p className="small">You do not need a perfect email. It is your decision.</p>
              <div className="row">
                <button disabled={busy} onClick={() => setConfirmSend(false)}>Keep editing · 继续修改</button>
                <button className="primary" disabled={busy} onClick={sendNow}>Send anyway · 仍然发送</button>
              </div>
            </div>
          )}
        </div>
        <WritingFeedback view={view} draft={draft} loading={feedbackLoading} busy={busy} onDecision={decide} />
      </div>
    </div>
  );
}
