import { useEffect, useLayoutEffect, useRef, useState, type CSSProperties } from "react";
import { api } from "../api";
import type { PageProps } from "../App";
import type { Hotspot, SessionState, Topic } from "../types";
import { areaProgress, nextStep, topicStates } from "../state/campus";
import { Avatar, Icon, SceneArt, themeFor } from "../components/CampusArt";
import SaveLine, { type SaveState } from "../components/SaveLine";
import SceneStage, { SCENE_PLACES } from "../components/SceneStage";

const KIND_ICON: Record<Hotspot["kind"], string> = { object: "look", person: "talk", moment: "listen" };
const KIND_ZH: Record<Hotspot["kind"], string> = { object: "查看", person: "交谈", moment: "听" };


/** A text message on a phone screen (an object whose text has a sender). */
function PhoneMessage({ spot, clock }: { spot: Hotspot; clock: string }) {
  return (
    <div className="phone" role="group" aria-label={`Phone: ${spot.title}`}>
      <div className="phone-status" aria-hidden="true"><span>{clock}</span><span>▮▮▮ ▰</span></div>
      <div className="phone-head"><Avatar name={spot.speaker} size={28} /><strong>{spot.speaker}</strong></div>
      <div className="phone-body">
        <div className="sms-day" aria-hidden="true">Today</div>
        <p className="sms"><span className="sr-only">{spot.speaker}: </span>{spot.text}</p>
      </div>
    </div>
  );
}

export default function Explore({ content, session, setSession, report, goTo, setLeaveGuard, setSceneMode }: PageProps) {
  const sid = session.session_id;
  const [here, setHere] = useState(session.location || content.start_area);
  const [open, setOpen] = useState<string | null>(null);
  const [saves, setSaves] = useState<Record<string, SaveState>>({});
  const [placeSave, setPlaceSave] = useState<"ok" | "error">("ok");
  const alive = useRef(true);
  // One campus request at a time, in order, so a slow answer can never undo a newer one.
  const queue = useRef<Promise<unknown>>(Promise.resolve());
  const enqueue = (work: () => Promise<SessionState>, done: (ok: boolean) => void) => {
    const next = queue.current.then(async () => {
      try {
        const state = await work();
        if (alive.current) { setSession(state); done(true); }
      } catch {
        if (alive.current) done(false);
      }
    });
    queue.current = next;
    return next;
  };
  useEffect(() => {
    alive.current = true;
    setLeaveGuard(() => queue.current.then(() => undefined));   // finish saving before leaving for the editor
    return () => { alive.current = false; setLeaveGuard(null); };
  }, [setLeaveGuard]);

  const area = content.areas.find((a) => a.id === here) ?? content.areas[0];
  // Places with scene art take over the page; the sidebar becomes a rail (see App).
  const staged = SCENE_PLACES.has(area.id);
  useLayoutEffect(() => {
    setSceneMode(staged);
    return () => setSceneMode(false);
  }, [staged, setSceneMode]);
  const theme = themeFor(area.id);
  const spots = content.hotspots.filter((h) => h.area === area.id);
  const current = spots.find((h) => h.id === open) ?? null;
  const step = nextStep(session);
  const firstMoment = session.notes.length === 0 && session.places_visited.length <= 1 && !session.prep.draft.trim();

  const record = (clueId: string, hotspotId: string, topicId?: string) => {
    setSaves((s) => ({ ...s, [clueId]: "saving" }));
    void enqueue(() => api.viewHotspot(sid, hotspotId, topicId),
      (ok) => setSaves((s) => ({ ...s, [clueId]: ok ? "saved" : "error" })));
  };
  const look = (h: Hotspot) => {
    setOpen(h.id);
    if (!session.notes.includes(h.id) && saves[h.id] !== "saving") record(h.id, h.id);
  };
  const ask = (h: Hotspot, topic: Topic) => {
    const id = `${h.id}:${topic.id}`;
    if (!session.notes.includes(id) && saves[id] !== "saving") record(id, h.id, topic.id);
  };
  const go = (areaId: string) => {
    if (areaId === here) return;
    setHere(areaId);
    setOpen(null);
    void enqueue(() => api.setLocation(sid, areaId), (ok) => setPlaceSave(ok ? "ok" : "error"));
  };
  const write = () => { void goTo("prepare").catch(report); };
  const placeStyle = (id: string) => {
    const t = themeFor(id);
    return { "--place": t.color, "--place-ink": t.ink, "--place-tint": t.tint } as CSSProperties;
  };

  const detail = (spot: Hotspot) => {
    const saved = session.notes.includes(spot.id) ? "saved" : saves[spot.id];
    const saveLine = <SaveLine state={saved} what="this" retry={() => record(spot.id, spot.id)} />;
    if (spot.kind === "person") {
      const topics = topicStates(spot, session.notes);
      const shownTopics = topics.filter(({ topic, state }) => state === "asked" || saves[`${spot.id}:${topic.id}`] !== undefined);
      const choices = topics.filter((t) => !shownTopics.includes(t));
      return (
        <div className="chat">
          <div className="chat-head"><Avatar name={spot.speaker || spot.label} size={40} />
            <div><strong>{spot.label}</strong><div className="muted small">{spot.title.split(" · ").slice(1).join(" · ")}</div></div>
          </div>
          <ol className="thread">
            <li className="msg them">
              <div className="bubble-them"><span className="sr-only">{spot.speaker}: </span>{spot.text}</div>
              {saveLine}
            </li>
            {shownTopics.map(({ topic, state }) => {
              const id = `${spot.id}:${topic.id}`;
              return (
                <li key={topic.id} className="msg-pair">
                  <div className="msg me"><div className="bubble-me"><span className="sr-only">You: </span>{topic.ask}</div></div>
                  <div className="msg them">
                    <div className="bubble-them"><span className="sr-only">{spot.speaker}: </span>{topic.reply}</div>
                    <SaveLine state={state === "asked" ? "saved" : saves[id]} what="this answer" retry={() => ask(spot, topic)} />
                  </div>
                </li>
              );
            })}
          </ol>
          {choices.length > 0 && (
            <div className="choices">
              <div className="choices-label">Ask {spot.label} · 选择一个问题</div>
              {choices.map(({ topic, state }) => (
                <div key={topic.id} className="choice-row">
                  <button className={`choice ${state}`} disabled={state === "locked" || saves[`${spot.id}:${topic.id}`] === "saving"}
                    onClick={() => ask(spot, topic)}>
                    <Icon name="chevron" /> {topic.ask}
                  </button>
                  {state === "locked" && (
                    <span className="muted small"> {session.notes.includes(spot.id) ? "Ask the question above first · 先问上一个" : "Available once your first chat is saved · 保存后可问"}</span>
                  )}
                </div>
              ))}
            </div>
          )}
          {choices.length === 0 && <p className="muted small">You have asked {spot.label} everything for now.</p>}
        </div>
      );
    }
    if (spot.kind === "object" && spot.speaker) {
      return (
        <div className="phone-wrap">
          <PhoneMessage spot={spot} clock={content.story_clock_label.split(", ").pop() ?? ""} />
          <div className="phone-side">
            <div className="card-kicker">{spot.title}</div>
            {saveLine}
          </div>
        </div>
      );
    }
    if (spot.kind === "moment") {
      return (
        <div className="overheard">
          <div className="card-kicker"><Icon name="listen" /> {spot.title} · 你听到了</div>
          <div className="msg them">
            <div className="who">{spot.speaker}</div>
            <div className="bubble-them">{spot.text}</div>
          </div>
          {saveLine}
        </div>
      );
    }
    return (
      <div className="paper">
        <div className="card-kicker">{spot.title}</div>
        <p className="paper-text">{spot.text}</p>
        {saveLine}
      </div>
    );
  };

  if (staged) {
    return (
      <div className="campus scene-page">
        <SceneStage content={content} session={session} area={area} open={open} saves={saves}
          placeSaveFailed={placeSave === "error"} onLook={look} onAsk={ask} onRetry={record} onGo={go} onWrite={write} onClose={() => setOpen(null)}
          onRetryPlace={() => { void enqueue(() => api.setLocation(sid, here), (ok) => setPlaceSave(ok ? "ok" : "error")); }} />
      </div>
    );
  }

  return (
    <div className="page campus" style={placeStyle(area.id)}>
      <div className="campus-head">
        <h2>{content.location.split(" · ")[0]}</h2>
        <span className="clock-chip">{content.story_clock_label}</span>
      </div>

      {firstMoment && content.intro && <p className="intro">{content.intro}</p>}

      <div className="guide" role="note">
        <div>
          <div className="card-kicker">Next · 下一步</div>
          <div>{step.message}</div>
        </div>
        <button className="primary big" onClick={write}><Icon name="write" /> {step.action}</button>
      </div>

      <nav className="map" aria-label="Places on campus">
        {content.areas.map((a) => {
          const progress = areaProgress(content, session.notes, a.id);
          const isHere = a.id === here;
          return (
            <button key={a.id} className={`place ${isHere ? "here" : ""}`} style={placeStyle(a.id)}
              aria-current={isHere ? "location" : undefined} onClick={() => go(a.id)}>
              <SceneArt areaId={a.id} className="place-thumb" />
              <span className="place-text">
                <span className="place-name">{a.label}</span>
                <span className="place-meta small">
                  {isHere ? <span className="here-badge"><Icon name="pin" /> You are here · 你在这里</span>
                    : <span>{themeFor(a.id).zh}</span>}
                  <span className="progress">{progress.seen}/{progress.total} looked at</span>
                </span>
              </span>
            </button>
          );
        })}
      </nav>
      {placeSave === "error" && (
        <p className="save-line small save-line-error" role="status">
          <strong>Your place on campus was not saved</strong> (the game could not be reached). You can keep reading, but a
          reload may put you back where you were.{" "}
          <button className="link" onClick={() => { void enqueue(() => api.setLocation(sid, here), (ok) => setPlaceSave(ok ? "ok" : "error")); }}>
            Retry
          </button>
        </p>
      )}

      <section className="scene-now" aria-label={area.label}>
        <div className="scene-banner">
          <SceneArt areaId={area.id} className="scene-art" />
          <div className="scene-text">
            <div className="scene-zh small">{theme.zh}</div>
            <h3>{area.label}</h3>
            <p>{area.description}</p>
          </div>
        </div>
        <div className="scene-items" role="group" aria-label="Things here">
          {spots.map((h) => {
            const seen = session.notes.includes(h.id);
            return (
              <button key={h.id} className={`hotspot kind-${h.kind} ${seen ? "seen" : ""} ${open === h.id ? "open" : ""}`}
                aria-expanded={open === h.id} onClick={() => look(h)}>
                <Icon name={KIND_ICON[h.kind]} /> {h.label}
                <span className="kind-zh" aria-hidden="true">{KIND_ZH[h.kind]}</span>
                {seen && <span className="tick"> ✓</span>}
                {saves[h.id] === "error" && !seen && <span className="unsaved"> · not saved</span>}
              </button>
            );
          })}
          {area.id === "library" && (
            <button className="hotspot kind-write" onClick={write}><Icon name="write" /> Open your laptop and write</button>
          )}
          {area.id === "office" && (
            <button className="hotspot kind-write" onClick={write}><Icon name="write" /> Email him before you knock</button>
          )}
        </div>

        {current && (
          <div className="detail card" role="region" aria-label={current.title}>
            {detail(current)}
          </div>
        )}
        {!current && <p className="muted small scene-hint">Click something or someone here, or choose another place above. · 点击这里的物品或人物，或换一个地点。</p>}
      </section>
    </div>
  );
}
