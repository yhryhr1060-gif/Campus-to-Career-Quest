import type { Content, SessionState } from "../types";
import { areaProgress, nextStep, unexplored } from "../state/campus";

/** The task, said once: the goal and situation stay visible; who and when can be opened.
 *  In the scene layout the session is passed too, and exploration progress is listed here: counts and
 *  place names only, never what is still to be found. */
export default function TaskSummary({ content, session }: { content: Content; session?: SessionState }) {
  const left = session ? unexplored(content, session) : null;
  return (
    <section className="panel task">
      <h3>Task · 任务</h3>
      <p className="task-goal">{content.task.goal}</p>
      <p className="task-situation">{content.task.situation}</p>
      <details className="fold">
        <summary>Who &amp; when · 对象与时间</summary>
        <dl>
          <dt>Who</dt>
          <dd>{content.task.target}</dd>
          <dt>Clock</dt>
          <dd>{content.story_clock_label} · {content.location}</dd>
        </dl>
      </details>
      {session && (session.phase === "explore") && (
        <p className="next-step"><span className="next-label">Next · 下一步</span> {nextStep(session).message}</p>
      )}
      {session && left && (
        <div className="explore-progress">
          <h4>Exploring campus · 探索进度</h4>
          <ul className="place-progress">
            {content.areas.map((a) => {
              const p = areaProgress(content, session.notes, a.id);
              const visited = session.places_visited.includes(a.id);
              const done = p.total > 0 && p.seen === p.total;
              return (
                <li key={a.id} className={done ? "done" : visited ? "part" : "new"}>
                  <span className="place-name">{a.label}</span>
                  <span className="place-state">{done ? "✓ all looked at · 已看完" : visited ? `${p.seen} of ${p.total} looked at` : "not visited yet · 未去过"}</span>
                </li>
              );
            })}
          </ul>
          <p className="small unexplored-note">
            {left.thingsLeft > 0
              ? <>Not explored yet · 尚未探索: {left.thingsLeft} thing{left.thingsLeft > 1 ? "s" : ""}. Optional — your email is checked only against your notes and the task.</>
              : <>You have looked at everything on campus.</>}
          </p>
        </div>
      )}
    </section>
  );
}
