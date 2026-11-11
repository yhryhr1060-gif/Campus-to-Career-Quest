import { useState } from "react";
import { api } from "../api";
import type { Content, SessionState } from "../types";
import { nicknameProblem } from "../state/campus";
import { CampusSkyline } from "./CampusArt";

/** First screen of a new game: choose a game nickname. Nothing else is asked for. */
export default function StartScreen({ content, session, setSession }: {
  content: Content; session: SessionState; setSession: (s: SessionState) => void;
}) {
  const [name, setName] = useState(session.player_name || content.player.name);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const problem = nicknameProblem(name);

  const enter = async () => {
    if (problem || busy) return;
    setBusy(true);
    setError(null);
    try {
      setSession(await api.setPlayer(session.session_id, name.trim().replace(/\s+/g, " ")));
    } catch (e) {
      // The game does not start until the nickname is really saved.
      setError(e instanceof Error ? e.message : String(e));
      setBusy(false);
    }
  };

  return (
    <div className="start">
      <div className="start-card">
        <CampusSkyline />
        <div className="start-body">
        <div className="card-kicker">Campus to Career Quest · {content.title}</div>
        <h1>An extension, please</h1>
        <p className="start-story">{content.task.situation} {content.task.goal}</p>
        <label className="field">
          <span>Your game nickname · 游戏昵称</span>
          <input
            className="name-input" value={name} maxLength={40} autoFocus disabled={busy}
            aria-describedby="name-help" aria-invalid={!!problem}
            onChange={(e) => { setName(e.target.value); setError(null); }}
            onKeyDown={(e) => { if (e.key === "Enter") void enter(); }}
          />
        </label>
        {problem && <p className="fb-alert small" role="status">{problem}</p>}
        {error && <p className="fb-alert small" role="alert">Not saved: {error}</p>}
        <p id="name-help" className="muted small">
          Please use a nickname, not your real name. “{content.player.name}” is the default. Prof. Chen will call you by
          it. It is saved with this play-through in the game's save file on the computer that runs the game, and it is
          not included in exported records.
          <span className="zh-note"> 请用昵称，不要填真实姓名。昵称只存在本机存档里，不写入导出记录。</span>
        </p>
        <button className="primary big" disabled={busy || !!problem} onClick={() => void enter()}>
          {busy ? "Saving…" : "Enter the campus · 进入校园"}
        </button>
        </div>
      </div>
    </div>
  );
}
