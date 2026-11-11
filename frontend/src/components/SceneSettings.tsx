// Settings panel of the scene layout: who is playing, the save's session ID, and New game.
// New game asks again inside the panel; the safe choice gets the keyboard, so Enter never starts over.
import { useEffect, useRef, useState } from "react";
import type { SessionState } from "../types";

interface Props {
  session: SessionState;
  creating: boolean;
  onNewGame: () => Promise<void>;
}

export default function SceneSettings({ session, creating, onNewGame }: Props) {
  const [asking, setAsking] = useState(false);
  const keepRef = useRef<HTMLButtonElement | null>(null);
  const startRef = useRef<HTMLButtonElement | null>(null);
  const wasAsking = useRef(false);
  useEffect(() => {
    if (asking) keepRef.current?.focus();
    else if (wasAsking.current) startRef.current?.focus();
    wasAsking.current = asking;
  }, [asking]);

  return (
    <section className="panel settings">
      <dl className="settings-list">
        <dt>Playing as · 昵称</dt>
        <dd>{session.player_name}</dd>
        <dt>Experience · 经验</dt>
        <dd>★ {session.xp} XP</dd>
        <dt>Session ID · 存档编号</dt>
        <dd className="session-id">{session.session_id}</dd>
      </dl>
      <p className="small settings-note">Your progress is saved under this ID as you play. · 游戏进度会自动保存在这个编号下。</p>
      {!asking ? (
        <button ref={startRef} className="settings-new" disabled={creating} onClick={() => setAsking(true)}>
          {creating ? "Starting…" : "New game · 新游戏"}
        </button>
      ) : (
        <div className="confirm-new" role="alertdialog" aria-label="Start a new game?" aria-describedby="confirm-new-text"
          onKeyDown={(e) => { if (e.key === "Escape") { e.stopPropagation(); setAsking(false); } }}>
          <p id="confirm-new-text">
            <strong>Start a new play-through?</strong> This browser will switch to a new game. Your current progress
            stays in the log. · 将开始新的一局，当前进度保留在记录中。
          </p>
          <div className="confirm-actions">
            <button ref={keepRef} className="confirm-keep" disabled={creating} onClick={() => setAsking(false)}>Keep playing · 继续当前游戏</button>
            <button className="confirm-go" disabled={creating} onClick={() => { void onNewGame(); }}>
              {creating ? "Starting…" : "Start new game · 开始新游戏"}
            </button>
          </div>
        </div>
      )}
    </section>
  );
}
