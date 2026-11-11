// Pure view logic for the compact campus: what has been found, what can be asked, what to show next.
// Nothing here decides progress. Writing is always open, and "met" never means "learned".
import type { Content, Hotspot, SessionState, Topic } from "../types";

export interface Clue {
  id: string;            // "syllabus" or "ivy:extension"
  hotspot: Hotspot;
  topic: Topic | null;
  note: string;
  text: string;          // the words the learner read there
  speaker: string;
  expressions: string[];
}

export function allClues(content: Content): Clue[] {
  return content.hotspots.flatMap((hotspot) => [
    { id: hotspot.id, hotspot, topic: null, note: hotspot.note, text: hotspot.text, speaker: hotspot.speaker,
      expressions: hotspot.expressions },
    ...hotspot.topics.map((topic) => ({ id: `${hotspot.id}:${topic.id}`, hotspot, topic, note: topic.note,
      text: topic.reply, speaker: hotspot.speaker, expressions: topic.expressions })),
  ]);
}

export function foundNotes(content: Content, notes: string[]): Clue[] {
  return allClues(content).filter((clue) => clue.note && notes.includes(clue.id));
}

export type TopicState = "asked" | "open" | "locked";

export function topicStates(hotspot: Hotspot, notes: string[]): { topic: Topic; state: TopicState }[] {
  const met = notes.includes(hotspot.id);
  return hotspot.topics.map((topic) => ({
    topic,
    state: notes.includes(`${hotspot.id}:${topic.id}`) ? "asked"
      : met && (!topic.requires || notes.includes(`${hotspot.id}:${topic.requires}`)) ? "open" : "locked",
  }));
}

/** How much of a place has been looked at. Counts only; it never reveals what is there. */
export function areaProgress(content: Content, notes: string[], areaId: string): { seen: number; total: number } {
  const here = allClues(content).filter((clue) => clue.hotspot.area === areaId);
  return { seen: here.filter((clue) => notes.includes(clue.id)).length, total: here.length };
}

export interface Unexplored { placesNotVisited: string[]; thingsLeft: number }

/** What the learner has not looked at yet: place names and a count, never the content. */
export function unexplored(content: Content, session: Pick<SessionState, "notes" | "places_visited">): Unexplored {
  const clues = allClues(content);
  return {
    placesNotVisited: content.areas.filter((a) => !session.places_visited.includes(a.id)).map((a) => a.label),
    thingsLeft: clues.filter((clue) => !session.notes.includes(clue.id)).length,
  };
}

export interface PhraseEntry {
  id: string;
  met: boolean;
  text: string | null;        // hidden until the learner meets it in the story
  function: string | null;
  contexts: { where: string; speaker: string; quote: string }[];
}

/** Expressions met so far, each with the lines where the learner actually read it. */
export function phrasebook(content: Content, session: Pick<SessionState, "notes" | "expressions_seen">): PhraseEntry[] {
  const found = allClues(content).filter((clue) => session.notes.includes(clue.id));
  return content.expressions.map((expression) => {
    const met = session.expressions_seen.includes(expression.id);
    return {
      id: expression.id, met,
      text: met ? expression.text : null,
      function: met ? expression.function : null,
      contexts: met ? found.filter((clue) => clue.expressions.includes(expression.id))
        .map((clue) => ({ where: clue.hotspot.label, speaker: clue.speaker, quote: clue.text })) : [],
    };
  });
}

export interface NextStep { message: string; action: string }

/** A suggestion, never a requirement: the write button is available in every state. */
export function nextStep(session: Pick<SessionState, "prep" | "notes">): NextStep {
  const prep = session.prep;
  if (prep.summary_confirmed) {
    return { message: "Your email is sent and its summary is confirmed. When you are ready, get set for the office.",
      action: "Get ready for the office" };
  }
  if (prep.email_final) {
    return { message: "Your email is sent. Check the summary Prof. Chen will read, or go back and edit.",
      action: "Check your email summary" };
  }
  if (prep.draft.trim()) {
    return { message: "Your draft is saved. Carry on with it whenever you like, or keep looking around.",
      action: "Continue your email" };
  }
  if (session.notes.length === 0) {
    return { message: "Look around, talk to people, or start your email straight away. Nothing here is required.",
      action: "Write to Prof. Chen" };
  }
  return { message: "You can write to Prof. Chen at any time, and come back here while you write.",
    action: "Write to Prof. Chen" };
}

const NAME = /^[\p{L}\p{N}]+(?:[ '’.\-][\p{L}\p{N}]+)*\.?$/u;

/** Same rule as the server, so most mistakes are caught before a request is made. */
export function nicknameProblem(raw: string): string | null {
  const name = raw.trim().replace(/\s+/g, " ");
  if (!name) return "Type a nickname, or keep the suggested one.";
  if (name.length > 20) return "Use 20 characters or fewer.";
  if (!NAME.test(name)) return "Use letters or numbers. Spaces, hyphens and apostrophes are allowed.";
  return null;
}

/** Short Chinese labels for each expression's function (an aid; the English function stays shown). */
export const FUNCTION_ZH: Record<string, string> = {
  "Making a request": "提出请求",
  "Explaining the reason and your progress": "说明原因和进度",
  "Offering an alternative": "提出替代方案",
  "Making a commitment": "作出承诺",
};

export interface Segment { text: string; mark: boolean }

const isLetter = (ch: string | undefined) => !!ch && /[\p{L}\p{N}]/u.test(ch);
const fold = (text: string) => text.replace(/[’‘]/g, "'").toLowerCase();

/**
 * Find a pattern such as "I could … by …" in the line where the learner met it and return just that
 * sentence, with the fixed words of the pattern marked. Returns null when the words are not found in
 * order, so nothing is ever highlighted that is not really there.
 */
export function expressionSnippet(pattern: string, quote: string): Segment[] | null {
  const fragments = pattern.replace(/\([^)]*\)/g, " ").split("…")
    .map((f) => f.trim().replace(/\s+/g, " ")).filter((f) => isLetter(Array.from(f).find((ch) => isLetter(ch))));
  if (fragments.length === 0) return null;
  const haystack = fold(quote);
  const marks: [number, number][] = [];
  let from = 0;
  for (const fragment of fragments) {
    const needle = fold(fragment);
    let at = haystack.indexOf(needle, from);
    // A fragment that starts or ends with a letter must match whole words ("by" is not inside "maybe").
    while (at >= 0 && ((isLetter(needle[0]) && isLetter(haystack[at - 1])) ||
      (isLetter(needle[needle.length - 1]) && isLetter(haystack[at + needle.length])))) {
      at = haystack.indexOf(needle, at + 1);
    }
    if (at < 0) return null;
    marks.push([at, at + needle.length]);
    from = at + needle.length;
  }
  let start = 0;
  for (let i = marks[0][0] - 1; i >= 0; i--) {
    if (".!?:“\"".includes(quote[i])) { start = i + 1; break; }
  }
  let end = quote.length;
  for (let i = from; i < quote.length; i++) {
    if (".!?".includes(quote[i])) { end = i + 1; break; }
  }
  const segments: Segment[] = [];
  let cursor = start;
  for (const [a, b] of marks) {
    if (a > cursor) segments.push({ text: quote.slice(cursor, a), mark: false });
    segments.push({ text: quote.slice(a, b), mark: true });
    cursor = b;
  }
  if (end > cursor) segments.push({ text: quote.slice(cursor, end), mark: false });
  if (segments.length && !segments[0].mark) segments[0].text = segments[0].text.trimStart();
  return segments;
}
