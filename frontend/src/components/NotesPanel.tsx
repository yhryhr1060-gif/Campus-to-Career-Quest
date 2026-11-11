import type { Content, SessionState } from "../types";
import { foundNotes, unexplored } from "../state/campus";

/** Notes hold only what the learner has actually found and saved. What is still out there is shown as
 *  a count and place names, so "not explored yet" is never confused with "not written clearly".
 *  In the scene layout (cluesOnly) that line lives in the Task panel and this panel lists clues only. */
export default function NotesPanel({ content, session, cluesOnly = false }: { content: Content; session: SessionState; cluesOnly?: boolean }) {
  const items = foundNotes(content, session.notes);
  const left = unexplored(content, session);
  const onCampus = !cluesOnly && (session.phase === "explore" || session.phase === "prepare");
  return (
    <section className="panel notes">
      <details className="fold" open>
        <summary><h3>Notes · 笔记 <span className="count">{items.length}</span></h3></summary>
        {items.length === 0 ? (
          <p className="muted small">What you find on campus is noted here once it is saved.</p>
        ) : (
          <ul className="note-list">
            {items.map((clue) => (
              <li key={clue.id}>{clue.note}</li>
            ))}
          </ul>
        )}
      </details>
      {onCampus && left.thingsLeft > 0 && (
        <p className="muted small unexplored">
          Not explored yet · 尚未探索: {left.thingsLeft} thing{left.thingsLeft > 1 ? "s" : ""}
          {left.placesNotVisited.length > 0 && <> (not visited: {left.placesNotVisited.join(", ")})</>}. Optional — your
          email is checked only against these notes and the task.
        </p>
      )}
      {onCampus && left.thingsLeft === 0 && <p className="muted small unexplored">You have looked at everything on campus.</p>}
    </section>
  );
}
