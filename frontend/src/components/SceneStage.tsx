// Story-scene presentation of a campus place. The scene fills the window: backdrop, then the people and
// things that can be clicked, then a light interface at the edges. Nothing is open on arrival, only a
// caption; a low dialogue box appears when something is opened. It only presents: looking, asking,
// moving and saving are the same handlers the classic view uses.
// All four places use this frame. The coffee corner has its own background and Ivy as a figure. The
// library, corridor and office have a backdrop picture each (SceneBackdrops.tsx); their things are
// labelled markers pinned to the objects in the picture and move with it when the window crops it.
// A place with no picture at all falls back to the small SVG drawing as a labelled placeholder.
import { useEffect, useRef, useState, type CSSProperties } from "react";
import type { Area, Content, Hotspot, SessionState, Topic } from "../types";
import { areaProgress, nextStep, topicStates } from "../state/campus";
import { Icon, SceneArt, themeFor } from "./CampusArt";
import { CafeBackdrop, IvyFigure } from "./CafeArt";
import { BACKDROPS, BackdropImage, PhoneOnDesk, canvasStyle } from "./SceneBackdrops";
import SaveLine, { type SaveState } from "./SaveLine";

/** Places shown in the scene frame. A place not listed here falls back to the classic view. */
export const SCENE_PLACES = new Set(["library", "corridor", "cafe", "office"]);
/** The place whose art is a background plus a standing figure. */
const FIGURE_PLACES = new Set(["cafe"]);
/** Placeholder fallback only: where a marker is pinned in the SVG drawing's own 320×150 coordinates. */
const PINS: Record<string, [number, number, "up" | "left" | "right"]> = {
  laptop: [201, 80, "up"], phone: [249, 92, "up"], syllabus: [150, 93, "up"], noticeboard: [160, 50, "up"], office_door: [160, 80, "up"],
};
const KIND_ICON: Record<Hotspot["kind"], string> = { object: "look", person: "talk", moment: "listen" };
const KIND_VERB: Record<Hotspot["kind"], [string, string]> = { object: ["Look", "查看"], person: ["Talk", "交谈"], moment: ["Listen", "听"] };
const pinOf = (id: string, n: number) => PINS[id] ?? ([90 + n * 70, 70, "up"] as [number, number, "up" | "left" | "right"]);
const pinStyle = (id: string, n: number): CSSProperties => {
  const [x, y] = pinOf(id, n);
  return { "--dx": ((x - 160) / 150) * 100, "--y": (y / 150) * 100 } as CSSProperties;
};
const SHORT: Record<string, string> = { library: "Library", corridor: "Corridor", cafe: "Coffee corner", office: "Office" };

interface Props {
  content: Content;
  session: SessionState;
  area: Area;
  open: string | null;
  saves: Record<string, SaveState>;
  placeSaveFailed: boolean;
  onLook: (h: Hotspot) => void;
  onAsk: (h: Hotspot, t: Topic) => void;
  onRetry: (clueId: string, hotspotId: string, topicId?: string) => void;
  onGo: (areaId: string) => void;
  onRetryPlace: () => void;
  onWrite: () => void;
  onClose: () => void;
}

export default function SceneStage({ content, session, area, open, saves, placeSaveFailed, onLook, onAsk, onRetry, onGo, onRetryPlace, onWrite, onClose }: Props) {
  const theme = themeFor(area.id);
  const spots = content.hotspots.filter((h) => h.area === area.id);
  const hasFigure = FIGURE_PLACES.has(area.id);
  const backdrop = BACKDROPS[area.id] ?? null;
  const hasArt = hasFigure || !!backdrop;
  // In the coffee corner the person is the standing figure; everything else is a pinned marker.
  const person = hasFigure ? spots.find((h) => h.kind === "person") ?? null : null;
  const others = spots.filter((h) => h !== person);
  const current = spots.find((h) => h.id === open) ?? null;
  const step = nextStep(session);
  // Which line of the conversation is on screen. null = the newest one.
  const [cursor, setCursor] = useState<string | null>(null);

  const topics = current ? topicStates(current, session.notes) : [];
  const said = topics.filter(({ topic, state }) => state === "asked" || saves[`${current!.id}:${topic.id}`] !== undefined);
  const choices = topics.filter((t) => !said.includes(t));
  const lines = current
    ? [{ id: current.id, ask: null as string | null, text: current.text, topic: null as Topic | null },
      ...said.map(({ topic }) => ({ id: `${current.id}:${topic.id}`, ask: topic.ask, text: topic.reply, topic }))]
    : [];
  const found = cursor ? lines.findIndex((l) => l.id === cursor) : -1;
  const index = found >= 0 ? found : Math.max(0, lines.length - 1);
  const line = lines[index] ?? null;
  const lineState = (id: string): SaveState | undefined => (session.notes.includes(id) ? "saved" : saves[id]);
  const personSeen = !!person && session.notes.includes(person.id);
  const talking = !!current;
  // Keyboard focus follows the conversation: a chosen question disappears from the list, so focus goes
  // to the answer; ending the chat hands focus back to Ivy.
  const dialogueRef = useRef<HTMLDivElement | null>(null);
  const figureRef = useRef<HTMLButtonElement | null>(null);
  const stageRef = useRef<HTMLElement | null>(null);
  const ended = useRef<string | null>(null);
  useEffect(() => {
    if (!talking && ended.current) {
      if (person && ended.current === person.id) figureRef.current?.focus();
      else stageRef.current?.querySelector<HTMLButtonElement>(`[data-spot="${ended.current}"]`)?.focus();
    }
    ended.current = null;
  }, [talking, person]);
  const firstMoment = session.notes.length === 0 && session.places_visited.length <= 1 && !session.prep.draft.trim();

  return (
    <section ref={stageRef} onKeyDown={(e) => {
      // Escape closes what is open in the scene, unless a side panel is open (that one closes first).
      if (e.key === "Escape" && current && !document.querySelector(".drawer:not(.off)")) { setCursor(null); ended.current = current.id; onClose(); }
    }} className={`scene-now stage stage-${area.id} ${hasArt ? "" : "stage-placeholder"} ${backdrop ? `caption-${backdrop.captionSide}` : ""} ${talking ? "in-talk" : ""}`} aria-label={area.label}>
      {hasFigure && <CafeBackdrop className="stage-bg" />}
      {backdrop && (
        <div className="stage-canvas canvas-back" style={canvasStyle(backdrop)}>
          <BackdropImage backdrop={backdrop} />
          {backdrop.pins.phone && spots.some((h) => h.id === "phone") && <PhoneOnDesk pin={backdrop.pins.phone} />}
        </div>
      )}
      {!hasArt && <SceneArt areaId={area.id} className="stage-bg stage-bg-draft" />}
      <div className="stage-shade" aria-hidden="true" />
      <h3 className="sr-only">{area.label}</h3>

      {person && (
        <button ref={figureRef} className={`stage-figure ${open === person.id ? "talking" : ""}`} aria-expanded={open === person.id}
          aria-label={`${person.label}${personSeen ? " (you have talked)" : ""}: talk`}
          onClick={() => { setCursor(null); onLook(person); }}>
          <IvyFigure className="figure-art" />
          {open !== person.id && (
            <span className="figure-tag">
              <Icon name="talk" /> Talk to {person.label} <span className="tag-zh">交谈</span>
              {personSeen && <span className="tick" aria-hidden="true"> ✓</span>}
            </span>
          )}
        </button>
      )}

      <nav className="map tabs" aria-label="Places on campus">
        {content.areas.map((a) => {
          const progress = areaProgress(content, session.notes, a.id);
          const here = a.id === area.id;
          return (
            <button key={a.id} className={`tab ${here ? "here" : ""}`} aria-current={here ? "location" : undefined}
              title={`${a.label}: ${progress.seen} of ${progress.total} looked at`}
              aria-label={`${a.label}${here ? " (you are here)" : ""}, ${progress.seen} of ${progress.total} looked at`}
              onClick={() => onGo(a.id)}>
              {here && <Icon name="pin" />}{SHORT[a.id] ?? a.label}
            </button>
          );
        })}
      </nav>
      <button className="stage-write" onClick={onWrite}><Icon name="write" /> {step.action}</button>

      {placeSaveFailed && (
        <p className="stage-alert save-line small save-line-error" role="status">
          <strong>Your place on campus was not saved</strong> (the game could not be reached). You can keep reading, but a
          reload may put you back where you were. <button className="link" onClick={onRetryPlace}>Retry</button>
        </p>
      )}

      {others.length > 0 && (
        <div className={backdrop ? "stage-canvas canvas-front stage-spots" : "stage-spots"} style={backdrop ? canvasStyle(backdrop) : undefined} role="group" aria-label="Things here">
          {others.map((h, n) => {
            const seen = session.notes.includes(h.id);
            const [verb, zh] = KIND_VERB[h.kind];
            const pin = backdrop?.pins[h.id];
            const placed = pin ? "pinned side-up" + (pin.lift ? " lifted" : "") : `side-${pinOf(h.id, n)[2]}`;
            return (
              <button key={h.id} data-spot={h.id} className={`stage-spot ${placed} kind-${h.kind} ${seen ? "seen" : ""} ${open === h.id ? "open" : ""}`}
                style={pin ? { left: `${pin.x * 100}%`, top: `${pin.y * 100}%` } : pinStyle(h.id, n)} aria-expanded={open === h.id}
                aria-label={`${h.label}${seen ? " (in your notes)" : ""}: ${verb.toLowerCase()}`}
                onClick={() => { setCursor(null); onLook(h); }}>
                <span className="spot-dot" aria-hidden="true" />
                {pin?.lift && <span className="spot-stem" aria-hidden="true" />}
                <span className="spot-tag">
                  <Icon name={KIND_ICON[h.kind]} /> {h.label} <span className="tag-zh">{zh}</span>
                  {seen && <span className="tick" aria-hidden="true"> ✓</span>}
                  {saves[h.id] === "error" && !seen && <span className="unsaved"> · not saved</span>}
                </span>
              </button>
            );
          })}
        </div>
      )}

      {!talking && (
        <div className="caption" role="note">
          <div className="caption-place">{area.label} <span className="tag-zh">{theme.zh}</span> <span className="caption-clock">{content.story_clock_label}</span></div>
          {firstMoment && content.intro && <p className="caption-intro">{content.intro}</p>}
          <p>{area.description}</p>
          {!hasArt && <p className="draft-note small">Placeholder picture · 占位画面</p>}
        </div>
      )}

      {current && line && (
        <div className="stage-talk">
          {choices.length > 0 && (
            <div className="choices" role="group" aria-label={`Questions for ${current.label}`}>
              {choices.map(({ topic, state }) => (
                <div key={topic.id} className="choice-row">
                  <button className={`choice ${state}`} disabled={state === "locked" || saves[`${current.id}:${topic.id}`] === "saving"}
                    onClick={() => { setCursor(`${current.id}:${topic.id}`); onAsk(current, topic); dialogueRef.current?.focus(); }}>
                    <Icon name={state === "locked" ? "lock" : "chevron"} /> <span>{topic.ask}</span>
                  </button>
                  {state === "locked" && (
                    <span className="lock-note small">{session.notes.includes(current.id) ? "Ask the other question first · 先问另一个问题" : "Available once your first chat is saved · 保存后可问"}</span>
                  )}
                </div>
              ))}
            </div>
          )}
          <div className="dialogue" role="region" aria-label={current.kind === "person" ? `Talking with ${current.label}` : current.title} tabIndex={-1} ref={dialogueRef}>
            <div className="name-tag">{current.speaker || current.label}</div>
            <div className="dialogue-text">
              {line.ask && <p className="you-asked"><span className="you-label">You · 你</span> {line.ask}</p>}
              {current.kind !== "person" && <p className="line-kicker small">{current.kind === "moment" && <Icon name="listen" />}{current.title}{current.kind === "moment" && " · 你听到了"}</p>}
              <p className="line" aria-live="polite" tabIndex={0}>{line.text}</p>
              <div className="dialogue-foot">
                <SaveLine state={lineState(line.id)} what={line.topic ? "this answer" : "this"}
                  retry={() => onRetry(line.id, current.id, line.topic?.id)} />
                {current.topics.length > 0 && choices.length === 0 && <span className="dialogue-hint small">You have asked {current.label} everything for now. · 暂时没有别的问题了。</span>}
              </div>
            </div>
            <div className="dialogue-actions">
              {lines.length > 1 && (
                <span className="line-nav small">
                  <button className="mini" disabled={index === 0} aria-label="Earlier line" onClick={() => setCursor(lines[index - 1].id)}>‹ Earlier</button>
                  <span aria-live="polite">{index + 1} / {lines.length}</span>
                  <button className="mini" disabled={index === lines.length - 1} aria-label="Later line" onClick={() => setCursor(lines[index + 1].id)}>Later ›</button>
                </span>
              )}
              <button className="mini end-chat" onClick={() => { setCursor(null); ended.current = current.id; onClose(); }}>
                {current.kind === "person" ? "End chat · 结束对话" : "Close · 关闭"}
              </button>
            </div>
          </div>
        </div>
      )}
    </section>
  );
}
