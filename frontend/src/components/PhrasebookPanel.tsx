import type { Content, SessionState } from "../types";
import { expressionSnippet, FUNCTION_ZH, phrasebook } from "../state/campus";

/** Expressions the learner has met in the story. The pattern comes first, then the sentence it was met
 *  in with the pattern's words marked; the full line can be opened. "Met" records that the text was
 *  opened; it is not a score and nothing depends on finding all of them. */
export default function PhrasebookPanel({ content, session }: { content: Content; session: SessionState }) {
  const entries = phrasebook(content, session);
  const met = entries.filter((e) => e.met);
  const missing = entries.length - met.length;
  return (
    <section className="panel phrasebook">
      <details className="fold" open>
        <summary><h3>Expressions met · 表达本 <span className="count">{met.length}/{entries.length}</span></h3></summary>
        {met.length === 0 && <p className="muted small">Useful expressions you come across on campus are kept here to look back at.</p>}
        <ul className="phrases">
          {met.map((entry) => (
            <li key={entry.id} className="phrase">
              <div className="phrase-pattern">{entry.text}</div>
              <div className="phrase-function small">
                {entry.function && FUNCTION_ZH[entry.function] && <span className="zh">{FUNCTION_ZH[entry.function]}</span>}
                <span className="muted"> {entry.function}</span>
              </div>
              {entry.contexts.map((context) => {
                const segments = entry.text ? expressionSnippet(entry.text, context.quote) : null;
                return (
                  <div key={context.where} className="phrase-example">
                    {segments && (
                      <p className="phrase-snippet">
                        {segments.map((s, i) => (s.mark ? <mark key={i}>{s.text}</mark> : <span key={i}>{s.text}</span>))}
                      </p>
                    )}
                    <details className="fold-inline">
                      <summary>{segments ? "Full context · 上下文" : "Where you met it · 出处"}</summary>
                      <p className="phrase-context small">
                        <span className="muted">{context.where}{context.speaker ? ` · ${context.speaker}` : ""}:</span> {context.quote}
                      </p>
                    </details>
                  </div>
                );
              })}
            </li>
          ))}
        </ul>
      </details>
      <p className="muted small">
        {met.length} of {entries.length} met.{missing > 0 && ` ${missing} more can be found around campus.`} Optional:
        you can write and negotiate without them, and meeting one does not mean you have learned it.
        <span className="zh-note"> 遇到过 ≠ 已掌握。</span>
      </p>
    </section>
  );
}
