import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError } from "./api";
import type { Content, Phase, SessionState } from "./types";
import TaskSummary from "./components/TaskSummary";
import NotesPanel from "./components/NotesPanel";
import PhrasebookPanel from "./components/PhrasebookPanel";
import StartScreen from "./components/StartScreen";
import { Icon } from "./components/CampusArt";
import { foundNotes } from "./state/campus";
import SceneSettings from "./components/SceneSettings";
import Explore from "./pages/Explore";
import Prepare from "./pages/Prepare";
import Negotiate from "./pages/Negotiate";
import OutcomePage from "./pages/Outcome";

const SESSION_KEY = "ccq_session_id";
type DrawerId = "task" | "notes" | "phrases" | "settings";

function readSaved(): string | null {
  try {
    return localStorage.getItem(SESSION_KEY);
  } catch {
    return null;
  }
}
function writeSaved(sid: string | null) {
  try {
    if (sid) localStorage.setItem(SESSION_KEY, sid);
    else localStorage.removeItem(SESSION_KEY);
  } catch {
    /* storage unavailable: progress still lives on the server for this tab */
  }
}

const STEPS: { id: Phase; label: string; sub: string }[] = [
  { id: "explore", label: "Explore", sub: "探索" },
  { id: "prepare", label: "Prepare", sub: "准备" },
  { id: "negotiate", label: "Negotiate", sub: "协商" },
  { id: "outcome", label: "Consequences", sub: "后果" },
];

export default function App() {
  const [content, setContent] = useState<Content | null>(null);
  const [session, replaceSession] = useState<SessionState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [bootError, setBootError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [navigating, setNavigating] = useState(false);
  const mounted = useRef(false);
  const activeSessionId = useRef<string | null>(null);
  const sessionRequest = useRef(0);
  const bootRequest = useRef<Promise<void> | null>(null);
  // The page on screen may need to finish saving (a draft, a note) before the learner moves on.
  const leaveGuard = useRef<(() => Promise<void>) | null>(null);
  const setLeaveGuard = useCallback((guard: (() => Promise<void>) | null) => { leaveGuard.current = guard; }, []);
  const newGameRequest = useRef<Promise<void> | null>(null);
  // A place with scene art asks for the stage layout: the sidebar becomes a rail with drawers.
  const [sceneMode, setSceneMode] = useState(false);
  const [drawer, setDrawer] = useState<DrawerId | null>(null);
  const railRef = useRef<HTMLDivElement | null>(null);
  // Closing a panel hands the keyboard back to the rail button that opened it.
  const closeDrawer = useCallback((id: DrawerId | null) => {
    setDrawer(null);
    if (id) railRef.current?.querySelector<HTMLButtonElement>(`[data-rail="${id}"]`)?.focus();
  }, []);
  useEffect(() => {
    if (!sceneMode || !drawer) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") closeDrawer(drawer); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [sceneMode, drawer, closeDrawer]);

  const acceptSession = useCallback((s: SessionState) => {
    activeSessionId.current = s.session_id;
    writeSaved(s.session_id);
    replaceSession(s);
    setError(null);
  }, []);

  const loadGame = useCallback((): Promise<void> => {
    // StrictMode's repeated effect shares this request. Once it fails, Retry can start a fresh one.
    if (bootRequest.current) return bootRequest.current;
    const request = ++sessionRequest.current;
    setBootError(null);
    let failureMessage = "We couldn't load the game. Please try again.";
    const pending = (async () => {
      try {
        const loadedContent = await api.content();
        const saved = readSaved();
        let loadedSession: SessionState | null = null;
        if (saved) {
          failureMessage = "We couldn't load your saved game. Try again to reconnect.";
          try {
            loadedSession = await api.getSession(saved);
          } catch (e) {
            // A temporary connection error must never discard an existing play-through.
            if (!(e instanceof ApiError && e.status === 404)) throw e;
          }
        }
        if (!loadedSession) {
          failureMessage = "We couldn't start the game. Please try again.";
          loadedSession = await api.createSession();
        }
        if (mounted.current && request === sessionRequest.current) {
          setContent(loadedContent);
          acceptSession(loadedSession);
        }
      } catch {
        if (mounted.current && request === sessionRequest.current) setBootError(failureMessage);
      } finally {
        bootRequest.current = null;
      }
    })();
    bootRequest.current = pending;
    return pending;
  }, [acceptSession]);

  useEffect(() => {
    mounted.current = true;
    void loadGame();
    return () => { mounted.current = false; };
  }, [loadGame]);

  const startNew = useCallback((): Promise<void> => {
    if (newGameRequest.current) return newGameRequest.current;
    const request = ++sessionRequest.current;
    setCreating(true);
    const pending = api.createSession().then((s) => {
      if (mounted.current && request === sessionRequest.current) acceptSession(s);
    }).finally(() => {
      newGameRequest.current = null;
      if (mounted.current) setCreating(false);
    });
    newGameRequest.current = pending;
    return pending;
  }, [acceptSession]);

  // An old page may still have a save or model request in flight after New game.
  const setSession = useCallback((s: SessionState) => {
    if (mounted.current && s.session_id === activeSessionId.current) replaceSession(s);
  }, []);
  const renderedSessionId = session?.session_id;
  const report = useCallback((e: unknown) => {
    if (!mounted.current || renderedSessionId !== activeSessionId.current) return;
    setError(e instanceof Error ? e.message : String(e));
  }, [renderedSessionId]);

  if (bootError && !session) {
    return (
      <div className="fatal" role="alert">
        <h1>Let's reconnect</h1>
        <p>{bootError}</p>
        <button className="primary" onClick={() => { void loadGame(); }}>Retry</button>
      </div>
    );
  }
  if (!content || !session) return <div className="loading" role="status">Loading your game…</div>;
  if (session.needs_name) return <StartScreen key={session.session_id} content={content} session={session} setSession={setSession} />;

  const phase = session.phase;
  const reachable = (p: Phase) => {
    if (p === "explore" || p === "prepare") return phase === "explore" || phase === "prepare";
    return p === phase;
  };
  const goTo = async (p: Phase) => {
    if (navigating || creating || p === phase || !reachable(p) || (p !== "explore" && p !== "prepare")) return;
    setNavigating(true);
    try {
      // Save first, then move: unsaved writing never depends on the browser cache alone.
      try {
        await leaveGuard.current?.();
      } catch (e) {
        throw new Error(`You are still on this page because saving did not finish. ${e instanceof Error ? e.message : String(e)}`);
      }
      setSession(await api.setPhase(session.session_id, p));
    } catch (e) {
      report(e);
    } finally {
      if (mounted.current) setNavigating(false);
    }
  };

  const props = { content, session, setSession, report, goTo, setLeaveGuard, setSceneMode };
  const scene = sceneMode && phase === "explore";
  const pane = (id: DrawerId) => (!scene ? "pane-flat" : drawer === id ? "drawer-pane" : "drawer-pane off");
  const RAIL = [
    { id: "task" as const, icon: "task", label: "Task", zh: "任务", count: "" },
    { id: "notes" as const, icon: "notes", label: "Notes", zh: "笔记", count: String(foundNotes(content, session.notes).length) },
    { id: "phrases" as const, icon: "phrases", label: "Expressions", zh: "表达本", count: `${session.expressions_seen.length}/${content.expressions.length}` },
    { id: "settings" as const, icon: "settings", label: "Settings", zh: "设置", count: "" },
  ];

  return (
    <div className={`app ${scene ? "scene-app" : ""}`}>
      <header className="topbar">
        <div className="brand">
          <strong>Campus to Career Quest</strong>
          <span className="muted"> · {content.title}</span>
        </div>
        <nav className="steps" aria-label="Chapter steps">
          {STEPS.map((s, i) => (
            <button
              key={s.id}
              className={`step ${s.id === phase ? "current" : ""}`}
              disabled={navigating || creating || !reachable(s.id)}
              onClick={() => goTo(s.id)}
            >
              <span className="step-no">{i + 1}</span> {s.label} <span className="muted">{s.sub}</span>
            </button>
          ))}
        </nav>
        <div className="xp" title="XP">
          <span className="player muted">Playing as {session.player_name} · </span>★ {session.xp} XP
        </div>
      </header>

      {error && (
        <div className="toast" role="alert" onClick={() => setError(null)}>
          {error} <span className="muted">(click to dismiss)</span>
        </div>
      )}

      <div className={`layout ${scene ? "scene" : ""}`}>
        <aside className={`sidebar ${scene ? "as-rail" : ""}`}>
          {scene && (
            <div className="rail" role="group" aria-label="Task, notes, expressions and settings" ref={railRef}>
              {RAIL.map((item) => (
                <button key={item.id} data-rail={item.id} className={`rail-btn ${item.id === "settings" ? "rail-low" : ""} ${drawer === item.id ? "on" : ""}`} aria-expanded={drawer === item.id}
                  aria-controls="side-drawer" onClick={() => setDrawer(drawer === item.id ? null : item.id)}>
                  <Icon name={item.icon} />
                  <span>{item.label}</span>
                  <span className="rail-zh">{item.zh}</span>
                  {item.count && <span className="rail-count" aria-label={`${item.count} so far`}>{item.count}</span>}
                </button>
              ))}
            </div>
          )}
          <div id="side-drawer" className={!scene ? "pane-flat" : drawer ? "drawer" : "drawer off"}>
            {scene && (
              <div className="drawer-head">
                <strong>{RAIL.find((r) => r.id === drawer)?.label} · {RAIL.find((r) => r.id === drawer)?.zh}</strong>
                <button className="drawer-close" onClick={() => closeDrawer(drawer)}><Icon name="close" /> Close · 收起</button>
              </div>
            )}
            <div className={pane("task")}><TaskSummary content={content} session={scene ? session : undefined} /></div>
            <div className={pane("notes")}><NotesPanel content={content} session={session} cluesOnly={scene} /></div>
            {(phase === "explore" || phase === "prepare") && (
              <div className={pane("phrases")}><PhrasebookPanel content={content} session={session} /></div>
            )}
            {scene ? (
              <div className={pane("settings")}>
                <SceneSettings key={`${session.session_id}:${drawer === "settings"}`} session={session} creating={creating}
                  onNewGame={async () => {
                    try {
                      await startNew();
                      setDrawer(null);
                    } catch (e) {
                      report(e);
                    }
                  }} />
              </div>
            ) : (
              <div className="session-meta muted">
                Session {session.session_id}
                <button
                  className="link"
                  disabled={creating}
                  onClick={async () => {
                    if (confirm("Start a new play-through? Your current progress stays in the log.")) {
                      try {
                        await startNew();
                      } catch (e) {
                        report(e);
                      }
                    }
                  }}
                >
                  {creating ? "Starting…" : "New game"}
                </button>
              </div>
            )}
          </div>
        </aside>
        <main className="main">
          {phase === "explore" && <Explore key={session.session_id} {...props} />}
          {phase === "prepare" && <Prepare key={session.session_id} {...props} />}
          {phase === "negotiate" && <Negotiate key={session.session_id} {...props} />}
          {phase === "outcome" && <OutcomePage key={session.session_id} {...props} onNewGame={startNew} />}
        </main>
      </div>
    </div>
  );
}

export interface PageProps {
  content: Content;
  session: SessionState;
  setSession: (s: SessionState) => void;
  report: (e: unknown) => void;
  /** Move between campus and the editor; finishes pending saves first. */
  goTo: (p: Phase) => Promise<void>;
  setLeaveGuard: (guard: (() => Promise<void>) | null) => void;
  /** Ask for the scene layout (true while a place with scene art is on screen). */
  setSceneMode: (on: boolean) => void;
}
