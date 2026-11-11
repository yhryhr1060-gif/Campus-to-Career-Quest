import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import type { PageProps } from "../App";
import type { FeedbackItem, Outcome, SessionState } from "../types";
import SessionExport from "../components/SessionExport";

const CATEGORY: Record<FeedbackItem["category"], { label: string; cls: string }> = {
  error: { label: "Needs revision · 需要修改", cls: "fb-error" },
  info_missing: { label: "Could be clearer · 可以更清楚", cls: "fb-info" },
  upgrade: { label: "Could be more natural · 可以更自然", cls: "fb-upgrade" },
  positive: { label: "Done well · 用得好", cls: "fb-positive" },
};

export default function OutcomePage({ session, setSession, report, onNewGame }: PageProps & { onNewGame: () => Promise<void> }) {
  const [data, setData] = useState<Outcome | null>(null);
  const [flipped, setFlipped] = useState(false);
  const [loadAttempt, setLoadAttempt] = useState(0);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const request = useRef<{ key: string; promise: Promise<Outcome> } | null>(null);
  const actionGeneration = useRef(0);

  useEffect(() => {
    let active = true;
    setData(null);
    setLoadError(null);
    const key = `${session.session_id}:${session.negotiation.attempt}:${loadAttempt}`;
    if (request.current?.key !== key) {
      request.current = { key, promise: api.outcome(session.session_id) };
    }
    request.current.promise.then((result) => {
      if (active) setData(result);
    }).catch((error: unknown) => {
      if (active) setLoadError(error instanceof Error ? error.message : String(error));
    });
    return () => {
      active = false;
      actionGeneration.current += 1;
    };
  }, [session.session_id, session.negotiation.attempt, loadAttempt]);

  if (!data) return (
    <div className="page">
      {loadError ? (
        <section className="panel" role="alert">
          <h2>Couldn't load your reflection</h2>
          <p>{loadError}</p>
          <p className="muted small">Your conversation is still saved.</p>
          <button onClick={() => setLoadAttempt((n) => n + 1)}>Retry · 重试</button>
        </section>
      ) : <p role="status">Loading your reflection…</p>}
      <SessionExport key={session.session_id} sessionId={session.session_id} />
    </div>
  );

  const agreed = data.status === "agreed";
  const paused = session.negotiation.paused || session.negotiation.turn_count >= session.negotiation.max_turns;
  const continueConversation = async () => {
    const generation = ++actionGeneration.current;
    setBusy(true);
    setActionError(null);
    const alreadyContinued = (next: SessionState) => next.phase === "negotiate" && (
      next.negotiation.attempt > session.negotiation.attempt || !next.negotiation.paused
    );
    try {
      // A previous response may have been lost after the server already resumed/restarted.
      const current = await api.getSession(session.session_id);
      if (generation !== actionGeneration.current) return;
      if (alreadyContinued(current)) {
        setSession(current);
        return;
      }
      if (current.negotiation.agreement.status === "confirmed") {
        setSession(current);
        setLoadAttempt((n) => n + 1);
        return;
      }
      const currentlyPaused = current.negotiation.paused || current.negotiation.turn_count >= current.negotiation.max_turns;
      if (currentlyPaused && !paused) {
        setSession(current);
        setActionError("This attempt is now paused. Start a new attempt to continue.");
        return;
      }
      const next = currentlyPaused ? await api.retry(session.session_id) : await api.resume(session.session_id);
      if (generation === actionGeneration.current) setSession(next);
    } catch (error) {
      try {
        const current = await api.getSession(session.session_id);
        if (generation === actionGeneration.current && alreadyContinued(current)) {
          setSession(current);
          return;
        }
      } catch { /* Keep the original failure visible; the next click checks current state first. */ }
      if (generation === actionGeneration.current) {
        setActionError(error instanceof Error ? error.message : String(error));
      }
    } finally {
      if (generation === actionGeneration.current) setBusy(false);
    }
  };

  return (
    <div className="page">
      <h2>Consequences · Back at the library</h2>

      <div className="scene outcome-scene">
        <div className="area area-library">
          <div className="area-label">Library · study desk</div>
          <div className="stickies">
            {data.notes.map((n) => (
              <div key={n} className="sticky">
                {n}
              </div>
            ))}
          </div>
          {!agreed && <p>No new deadline has been agreed.</p>}
        </div>
      </div>

      <div className="endings">
        <div className="card ending">
          <div className="card-kicker">
            {agreed ? `Ending · ${data.ending.id === "staged" ? "B" : "A"}` : "Reflection · 未达成协议"}
          </div>
          <h3>{data.ending.title}</h3>
          <p>{agreed ? `Agreed: ${data.ending.plan}.` : data.ending.plan}</p>
          {data.ending.confirm_quote && (
            <blockquote>
              {data.ending.confirm_quote === "[pressed Confirm]"
                ? "(You confirmed with the Confirm button.)"
                : `You said: “${data.ending.confirm_quote}”`}
            </blockquote>
          )}
        </div>
        {data.other_ending && <button className={`card face-down ${flipped ? "peek" : ""}`} onClick={() => setFlipped(!flipped)}>
          <div className="card-back">?</div>
          <div>{data.other_ending.text}</div>
          {flipped && <div className="muted small">Replay the chapter to find it.</div>}
        </button>}
      </div>

      {!agreed && data.reflection && (
        <section className="panel">
          <h3>What happened · 这次停在哪里</h3>
          <p>{data.reflection.reason}</p>
          {data.reflection.technical_failures > 0 && (
            <p className="muted small">
              Service interruptions: {data.reflection.technical_failures}.
              These were recorded separately from completed learner turns.
            </p>
          )}
          <h3>What you already know</h3>
          <ul>{data.reflection.known_constraints.map((item, i) => <li key={i}>{item}</li>)}</ul>
          <h3>What to try next</h3>
          <ul>{data.reflection.next_steps.map((item, i) => <li key={i}>{item}</li>)}</ul>
        </section>
      )}

      <section className="panel language">
        <h3>Ivy's notes on your English</h3>
        {!data.language_available ? (
          <div role="status">
            <p>Language notes are temporarily unavailable. Your reflection and conversation are saved.</p>
            <button disabled={busy} onClick={() => setLoadAttempt((n) => n + 1)}>
              Retry language notes · 重试语言反馈
            </button>
          </div>
        ) : data.language.length === 0 ? (
          <p className="muted small">No notes this time.</p>
        ) : (
          data.language.map((f, i) => (
            <div key={i} className={`fb ${CATEGORY[f.category].cls}`}>
              <div className="fb-label">{CATEGORY[f.category].label}</div>
              <div className="quote">You said: “{f.quote}”</div>
              {f.suggestion && <div>→ {f.suggestion}</div>}
              <div className="muted small">{f.reason}</div>
            </div>
          ))
        )}
      </section>

      <section className="panel strategy">
        <h3>How you negotiated</h3>
        <ul>
          {data.strategy.map((s) => (
            <li key={s.move} className={s.kind}>
              <strong>{s.kind === "did_well" ? "✓ " : "→ "}</strong>
              {s.text}
              {s.quote && <div className="quote">“{s.quote}”</div>}
            </li>
          ))}
        </ul>
      </section>

      <div className="row">
        <div className="xp big">★ {data.xp} XP</div>
        {!agreed && (
          <button className="primary" disabled={busy} onClick={continueConversation}>
            {busy ? "Opening…" : paused ? "Start a new attempt · 再试一次" : "Continue negotiating · 继续协商"}
          </button>
        )}
        <button
          disabled={busy}
          onClick={async () => {
            setBusy(true);
            try {
              await onNewGame();
            } catch (e) {
              report(e);
            } finally {
              setBusy(false);
            }
          }}
        >
          {agreed ? "Play again" : "Start a new game"}
        </button>
      </div>
      {actionError && <p role="alert">{actionError}</p>}
      <SessionExport key={session.session_id} sessionId={session.session_id} />
    </div>
  );
}
