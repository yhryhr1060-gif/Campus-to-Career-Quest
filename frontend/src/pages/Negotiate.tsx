import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError, newRequestId } from "../api";
import type { PageProps } from "../App";
import { speak, stopSpeaking, ttsAvailable } from "../audio/tts";
import VoiceInput from "../components/VoiceInput";
import { inputMetadata, readInputMetadata, sameInputMetadata, type InputMetadata } from "../audio/voiceDraft";

const MOOD_FACE: Record<string, string> = { neutral: "😐", warm: "🙂", concerned: "😟" };

function readReply(key: string): {text: string; id: string | null; submitted: string | null; uncertain: boolean; asrText: string | null; metadata: InputMetadata | null} {
  try {
    const value = JSON.parse(localStorage.getItem(key) ?? "null");
    if (value && typeof value.text === "string") return {
      text: value.text, id: typeof value.id === "string" ? value.id : null,
      submitted: typeof value.submitted === "string" ? value.submitted : null, uncertain: value.uncertain === true,
      asrText: value.text.trim() && typeof value.asrText === "string" ? value.asrText : null,
      metadata: readInputMetadata(value.metadata) ?? (typeof value.id === "string" ? { input_mode: "typed" } : null),
    };
  } catch { /* local storage may be unavailable */ }
  return {text: "", id: null, submitted: null, uncertain: false, asrText: null, metadata: null};
}

export default function Negotiate({ content, session, setSession, report }: PageProps) {
  const sid = session.session_id;
  const neg = session.negotiation;
  const ag = neg.agreement;
  const replyKey = `ccq_reply:${sid}:${neg.attempt}`;
  const contextKey = useRef(replyKey);
  contextKey.current = replyKey;
  const [text, setText] = useState(() => readReply(replyKey).text);
  const textRef = useRef(text);
  textRef.current = text;
  const [asrText, setAsrText] = useState<string | null>(() => readReply(replyKey).asrText);
  const [voiceBusy, setVoiceBusy] = useState(false);
  const voiceBusyRef = useRef(false);
  const [busy, setBusy] = useState(false);
  const [replyUnavailable, setReplyUnavailable] = useState(false);
  const [deliveryUnknown, setDeliveryUnknown] = useState(() => readReply(replyKey).uncertain);
  const [openingLoading, setOpeningLoading] = useState(false);
  const [openingError, setOpeningError] = useState<string | null>(null);
  const [voiceOn, setVoiceOn] = useState(ttsAvailable());
  const pendingId = useRef<string | null>(readReply(replyKey).id);
  const pendingText = useRef<string | null>(readReply(replyKey).submitted);
  const pendingMetadata = useRef<InputMetadata | null>(readReply(replyKey).metadata);
  const logEnd = useRef<HTMLDivElement>(null);
  const spokenCount = useRef(neg.turns.length);

  const alive = useRef(true);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  const setVoiceActivity = useCallback((active: boolean) => {
    voiceBusyRef.current = active;
    if (alive.current) setVoiceBusy(active);
  }, []);
  const storeReply = (value: string, uncertain: boolean, source = asrText) => {
    try {
      localStorage.setItem(replyKey, JSON.stringify({text: value, id: pendingId.current, submitted: pendingText.current,
        uncertain, asrText: source, metadata: pendingMetadata.current}));
    } catch { /* the current tab still retains the input */ }
  };
  useEffect(() => {
    const saved = readReply(replyKey);
    setText(saved.text);
    textRef.current = saved.text;
    setAsrText(saved.asrText);
    pendingId.current = saved.id;
    pendingText.current = saved.submitted;
    pendingMetadata.current = saved.metadata;
    setDeliveryUnknown(saved.uncertain);
    setReplyUnavailable(false);
    spokenCount.current = neg.turns.length;
  }, [replyKey]);

  // Recover a completed opening before retrying a response that may have been lost.
  const openingInFlight = useRef<string | null>(null);
  const startOpening = useCallback(async () => {
    if (openingInFlight.current === replyKey) return;
    openingInFlight.current = replyKey;
    setOpeningLoading(true);
    setOpeningError(null);
    try {
      const saved = await api.getSession(sid);
      if (!alive.current || contextKey.current !== replyKey) return;
      if (saved.phase !== "negotiate" || saved.negotiation.turns.length > 0) {
        setSession(saved);
        return;
      }
      const started = await api.start(sid);
      if (!alive.current || contextKey.current !== replyKey) return;
      setSession(started);
      if (started.available === false) setOpeningError("Prof. Chen's opening is temporarily unavailable. Retry when you are ready.");
    } catch (error) {
      if (alive.current && contextKey.current === replyKey) setOpeningError(error instanceof Error ? error.message : String(error));
    } finally {
      if (openingInFlight.current === replyKey) openingInFlight.current = null;
      if (alive.current && contextKey.current === replyKey) setOpeningLoading(false);
    }
  }, [replyKey, sid, setSession]);

  const startedFor = useRef<string | null>(null);
  useEffect(() => {
    if (neg.turns.length === 0 && startedFor.current !== replyKey) {
      startedFor.current = replyKey;
      void startOpening();
    }
  }, [replyKey, neg.turns.length, startOpening]);

  // Read out each new line from Prof. Chen.
  useEffect(() => {
    const fresh = neg.turns.slice(spokenCount.current).filter((t) => t.speaker === "npc");
    spokenCount.current = neg.turns.length;
    const last = fresh[fresh.length - 1];
    if (last && voiceOn && !voiceBusy) speak(last.text);
    logEnd.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [neg.turns, voiceOn, voiceBusy]);

  useEffect(() => () => stopSpeaking(), []);

  const send = async () => {
    const t = text.trim();
    if (!t || busy || voiceBusyRef.current || openingLoading || neg.turns.length === 0 || Array.from(text).length > content.input_limits.turn) return;
    setBusy(true);
    // An unchanged message keeps its id when retried; edited messages get a new id.
    const metadata = inputMetadata(text, asrText);
    if (!pendingId.current || pendingText.current !== t || !sameInputMetadata(pendingMetadata.current, metadata)) {
      pendingId.current = newRequestId();
      pendingMetadata.current = metadata;
    }
    pendingText.current = t;
    const submittedMetadata = pendingMetadata.current ?? metadata;
    storeReply(text, true);
    try {
      const res = await api.turn(sid, t, pendingId.current, submittedMetadata.input_mode, submittedMetadata.asr_text);
      if (!alive.current || contextKey.current !== replyKey) return;
      setSession(res);
      setDeliveryUnknown(false);
      if (res.available === false) {
        storeReply(text, false);
        setReplyUnavailable(true);
        return;
      }
      pendingId.current = null;
      pendingText.current = null;
      pendingMetadata.current = null;
      setReplyUnavailable(false);
      setText("");
      textRef.current = "";
      setAsrText(null);
      try { localStorage.removeItem(replyKey); } catch { /* input cleared in this tab */ }
    } catch (e) {
      if (!alive.current || contextKey.current !== replyKey) return;
      const uncertain = !(e instanceof ApiError && e.status >= 400 && e.status < 500);
      setDeliveryUnknown(uncertain);
      storeReply(text, uncertain);
      setReplyUnavailable(true);
      report(e);
      try {
        const saved = await api.getSession(sid);
        if (alive.current && contextKey.current === replyKey) setSession(saved);
      } catch { /* Retry retains the same request id and input metadata. */ }
    } finally {
      if (alive.current && contextKey.current === replyKey) setBusy(false);
    }
  };

  const act = async (fn: () => Promise<typeof session>) => {
    if (busy || voiceBusyRef.current) return;
    setBusy(true);
    try {
      const next = await fn();
      if (alive.current && contextKey.current === replyKey) setSession(next);
    } catch (e) {
      if (!alive.current || contextKey.current !== replyKey) return;
      report(e);
      // A lost response may follow a successful finish/confirm/restart.
      try {
        const saved = await api.getSession(sid);
        if (alive.current && contextKey.current === replyKey) setSession(saved);
      } catch { /* keep the current page and retry control */ }
    } finally {
      if (alive.current && contextKey.current === replyKey) setBusy(false);
    }
  };

  const restartAttempt = () => act(async () => {
    const current = await api.getSession(sid);
    if (current.negotiation.attempt !== neg.attempt || !current.negotiation.paused || current.phase !== "negotiate") return current;
    return api.retry(sid);
  });

  const lastNpc = [...neg.turns].reverse().find((t) => t.speaker === "npc");
  const closed = ag.status === "confirmed" || neg.paused;
  const replyLength = Array.from(text).length;
  const overLimit = replyLength > content.input_limits.turn;
  const canCompose = !busy && !voiceBusy && !openingLoading && neg.turns.length > 0 && !closed;

  return (
    <div className="page negotiate">
      <div className="talk">
        <div className="talk-head">
          <h2>
            Negotiate · Prof. Chen <span className="face">{MOOD_FACE[lastNpc?.mood ?? "neutral"]}</span>
          </h2>
          <div className="muted small">
            Turn {neg.turn_count} / {neg.max_turns}
            {neg.attempt > 1 && ` · attempt ${neg.attempt}`}
            {ttsAvailable() && (
              <label className="voice-toggle">
                <input type="checkbox" checked={voiceOn} disabled={voiceBusy} onChange={(e) => setVoiceOn(e.target.checked)} /> voice
              </label>
            )}
          </div>
        </div>

        <div className="log" aria-live="polite">
          {neg.turns.map((t, i) => (
            <div key={i} className={`bubble ${t.speaker}`}>
              <div className="who">
                {t.speaker === "npc" ? "Prof. Chen" : "You"}
                {t.speaker === "npc" && voiceOn && (
                  <button className="link small" disabled={voiceBusy} onClick={() => speak(t.text, 0.8)} title="Play again, slower">
                    ↻ replay
                  </button>
                )}
              </div>
              <div>{t.text}</div>
            </div>
          ))}
          <div ref={logEnd} />
        </div>

        {neg.turns.length === 0 && (
          <div role="status">
            <p>{openingLoading ? "Waiting for Prof. Chen to begin…" : openingError ?? "The conversation has not started yet."}</p>
            {!openingLoading && <button onClick={() => void startOpening()}>Retry opening</button>}
          </div>
        )}

        {replyUnavailable && !closed && !deliveryUnknown && (
          <p className="muted small" role="status">
            Prof. Chen's reply is temporarily unavailable. Your message is still below, and no turn was used.
            You can retry or edit your message.
          </p>
        )}
        {deliveryUnknown && !closed && (
          <p className="muted small" role="status">The reply did not reach this page. Retry the same message to recover its result before editing; it will not be counted twice.</p>
        )}

        {ag.status === "confirmed" ? (
          <div className="agreed-bar">
            <div className="stamp">AGREED</div>
            <div>
              <div>{ag.confirmed?.describe}</div>
              <button className="primary" disabled={busy || voiceBusy} onClick={() => act(() => api.finish(sid))}>
                Leave the office →
              </button>
            </div>
          </div>
        ) : neg.paused ? (
          <div className="paused-bar">
            <p>The conversation is paused. You can try again from the start of this conversation.</p>
            <button className="primary" disabled={busy || voiceBusy} onClick={restartAttempt}>
              Retry from this goal
            </button>
          </div>
        ) : (
          <div className="composer">
            <textarea
              rows={2}
              value={text}
              placeholder="Type your reply, or record it and check the transcript…"
              onChange={(e) => {
                const value = e.target.value;
                const source = value.trim() ? asrText : null;
                if (!value.trim() || pendingText.current !== value.trim()) {
                  pendingId.current = null;
                  pendingText.current = null;
                  pendingMetadata.current = null;
                }
                setText(value);
                textRef.current = value;
                setAsrText(source);
                storeReply(value, false, source);
                setReplyUnavailable(false);
              }}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  send();
                }
              }}
              disabled={!canCompose || deliveryUnknown}
              aria-label="Your reply"
              aria-describedby="reply-length"
              aria-invalid={overLimit}
            />
            <button className="primary" disabled={!canCompose || !text.trim() || overLimit} onClick={send}>
              {busy ? "Waiting…" : replyUnavailable || deliveryUnknown ? "Retry message" : "Send"}
            </button>
          </div>
        )}
        {!closed && <VoiceInput key={replyKey} sessionId={sid} attempt={neg.attempt}
          disabled={!canCompose || deliveryUnknown} hasDraft={text.length > 0} onBusyChange={setVoiceActivity}
          onTranscript={(transcript) => {
            if (!alive.current || contextKey.current !== replyKey || textRef.current.length > 0) return;
            pendingId.current = null;
            pendingText.current = null;
            pendingMetadata.current = null;
            setAsrText(transcript);
            setText(transcript);
            textRef.current = transcript;
            setReplyUnavailable(false);
            storeReply(transcript, false, transcript);
          }} />}
        {!closed && asrText !== null && <p className="muted small" role="status">
          {inputMetadata(text, asrText).input_mode === "voice_edited" ? "You edited the transcript." : "Transcript ready."}
          {" "}Check the wording, then press Send when you are ready. It has not been sent yet.
        </p>}
        {!closed && <p id="reply-length" className="muted small" role={overLimit ? "alert" : undefined}>
          {replyLength} / {content.input_limits.turn} characters.
          {overLimit ? ` Remove ${replyLength - content.input_limits.turn} characters before sending. Your full text is kept here.` : ` ${content.input_limits.turn - replyLength} remaining.`}
        </p>}
        {ag.status !== "confirmed" && <div className="row">
          <button disabled={busy || voiceBusy || openingLoading || deliveryUnknown} onClick={() => act(() => api.finish(sid))}>Finish for now · Review</button>
          <span className="muted small">Review what is still unresolved. You can continue afterwards.</span>
        </div>}
      </div>

      <aside className="aids">
        <section className="panel">
          <h3>Your key points</h3>
          {neg.key_points.length === 0 ? (
            <p className="muted small">(no checked points from your email)</p>
          ) : (
            <ul>
              {neg.key_points.map((k) => (
                <li key={k.field}>
                  <span className="muted small">{k.field}</span>
                  <br />
                  {k.text}
                </li>
              ))}
            </ul>
          )}
        </section>

        {ag.status === "proposed" && ag.proposal && (
          <section className="panel proposal">
            <h3>Proposed — waiting for you</h3>
            <p>{ag.proposal.describe}</p>
            <button className="primary" disabled={busy || voiceBusy || deliveryUnknown} onClick={() => act(() => api.confirm(sid))}>
              Confirm
            </button>
            <p className="muted small">Or say it: “Yes, that's right.”</p>
          </section>
        )}

        <section className="panel">
          <h3>Expression cards</h3>
          <ul className="cards">
            {content.expressions.map((e) => (
              <li key={e.id}>
                <div className="expr">{e.text}</div>
                <div className="muted small">{e.function}</div>
              </li>
            ))}
          </ul>
        </section>

        <section className="panel">
          <h3>Hints</h3>
          {!neg.hints_unlocked ? (
            <p className="muted small">Hints open if the conversation gets stuck.</p>
          ) : (
            <>
              {neg.hints.map((h) => (
                <div key={h.level} className="hint">
                  <span className="muted small">
                    L{h.level} · {h.kind}
                  </span>
                  <div>{h.text}</div>
                </div>
              ))}
              {neg.hints.length < content.hint_count && (
                <button disabled={busy || voiceBusy || closed || deliveryUnknown} onClick={() => act(() => api.hint(sid))}>
                  {neg.hints.length === 0 ? "Show a hint" : "One more"}
                </button>
              )}
              <p className="muted small">Hints never cost anything.</p>
            </>
          )}
        </section>
      </aside>
    </div>
  );
}
