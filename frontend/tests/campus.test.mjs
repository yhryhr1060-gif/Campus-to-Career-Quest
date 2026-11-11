import assert from "node:assert/strict";
import test from "node:test";
import { allClues, areaProgress, foundNotes, nextStep, nicknameProblem, phrasebook, topicStates, unexplored } from "../src/state/campus.ts";

const spot = (id, area, extra = {}) => ({ id, area, label: id, title: id, text: `${id} text`, note: `${id} note`,
  kind: "object", speaker: "", expressions: [], topics: [], ...extra });
const content = {
  areas: [{ id: "desk", label: "Desk" }, { id: "cafe", label: "Cafe" }, { id: "door", label: "Door" }],
  expressions: [{ id: "E1", text: "Would it be possible to …?", function: "request" },
    { id: "E2", text: "I could … by …", function: "offer" }],
  hotspots: [
    spot("phone", "desk", { expressions: ["E2"], speaker: "Ivy", text: "I could send you my notes by tonight." }),
    spot("lamp", "desk", { note: "" }),
    spot("ivy", "cafe", { kind: "person", speaker: "Ivy", topics: [
      { id: "a", ask: "A?", reply: "Would it be possible to hand it in on Monday?", note: "a note", expressions: ["E1"], requires: null },
      { id: "b", ask: "B?", reply: "b reply", note: "", expressions: [], requires: "a" },
    ] }),
    spot("sign", "door"),
  ],
};
const prep = (extra = {}) => ({ draft: "", email_final: null, summary_confirmed: false, ...extra });

test("clues cover objects and each question put to a person", () => {
  assert.deepEqual(allClues(content).map((c) => c.id), ["phone", "lamp", "ivy", "ivy:a", "ivy:b", "sign"]);
});

test("notes list only what was found and has something to note", () => {
  assert.deepEqual(foundNotes(content, ["lamp", "ivy:a", "sign", "unknown"]).map((c) => c.note), ["a note", "sign note"]);
  assert.deepEqual(foundNotes(content, []), []);
});

test("a person's questions open in order and only after talking to them", () => {
  const states = (notes) => topicStates(content.hotspots[2], notes).map((t) => t.state);
  assert.deepEqual(states([]), ["locked", "locked"]);
  assert.deepEqual(states(["ivy"]), ["open", "locked"]);
  assert.deepEqual(states(["ivy", "ivy:a"]), ["asked", "open"]);
  assert.deepEqual(states(["ivy", "ivy:a", "ivy:b"]), ["asked", "asked"]);
});

test("place progress and the unexplored summary give counts and place names, never content", () => {
  assert.deepEqual(areaProgress(content, ["ivy", "ivy:a"], "cafe"), { seen: 2, total: 3 });
  const left = unexplored(content, { notes: ["phone"], places_visited: ["desk"] });
  assert.deepEqual(left, { placesNotVisited: ["Cafe", "Door"], thingsLeft: 5 });
  assert.ok(!JSON.stringify(left).includes("note") && !JSON.stringify(left).includes("Monday"));
});

test("phrasebook hides an expression until it was met, then shows the line it was met in", () => {
  const before = phrasebook(content, { notes: [], expressions_seen: [] });
  assert.ok(before.every((e) => !e.met && e.text === null && e.contexts.length === 0));
  assert.ok(!JSON.stringify(before).includes("possible"));
  const after = phrasebook(content, { notes: ["phone", "ivy", "ivy:a"], expressions_seen: ["E2", "E1"] });
  assert.deepEqual(after.map((e) => [e.id, e.met, e.contexts.length]), [["E1", true, 1], ["E2", true, 1]]);
  assert.deepEqual(after[1].contexts[0], { where: "phone", speaker: "Ivy", quote: "I could send you my notes by tonight." });
});

test("an expression is not shown as met just because its clue is in the notes", () => {
  // The server records expressions only with a successful save; the notes alone are not evidence.
  const entry = phrasebook(content, { notes: ["phone"], expressions_seen: [] })[1];
  assert.equal(entry.met, false);
  assert.equal(entry.text, null);
});

test("the next step is a suggestion for every stage and always offers writing", () => {
  const actions = [
    nextStep({ notes: [], prep: prep() }),
    nextStep({ notes: ["phone"], prep: prep() }),
    nextStep({ notes: [], prep: prep({ draft: "Dear Prof. Chen" }) }),
    nextStep({ notes: [], prep: prep({ draft: "x", email_final: "x" }) }),
    nextStep({ notes: [], prep: prep({ draft: "x", email_final: "x", summary_confirmed: true }) }),
  ].map((s) => s.action);
  assert.deepEqual(actions, ["Write to Prof. Chen", "Write to Prof. Chen", "Continue your email",
    "Check your email summary", "Get ready for the office"]);
  assert.ok(nextStep({ notes: [], prep: prep() }).message.includes("Nothing here is required"));
});

test("nickname check matches the server's rule", () => {
  for (const ok of ["Alex", "Mika Chan", "小林", "Jean-Luc", "O'Neil", "Player 2", "Dr.Lee"]) assert.equal(nicknameProblem(ok), null, ok);
  for (const bad of ["", "   ", "x".repeat(21), "Mika{{7}}", "a@b.c", "Mika!!", "<b>", 'Mi"ka', "-Mika"]) assert.ok(nicknameProblem(bad), bad);
});

// ---- round 8: highlighting the pattern in the line where it was met (real chapter text)
import { expressionSnippet, FUNCTION_ZH } from "../src/state/campus.ts";
const show = (segments) => segments.map((s) => (s.mark ? `[${s.text}]` : s.text)).join("");

test("each target pattern is found in its real campus line and only that sentence is shown", () => {
  assert.equal(show(expressionSnippet("I could … by …",
    "Heard you've been ill — hope you're better! I could send you my lecture notes by tonight. I'm at the coffee corner till three if you want to talk about Prof. Chen.")),
    "[I could] send you my lecture notes [by] tonight.");
  assert.equal(show(expressionSnippet("Unfortunately, I've been …, so I haven't been able to …",
    "Last term, yes — I had flu the week of the lab report. I emailed him before I went to see him: “Unfortunately, I've been ill, so I haven't been able to finish the lab.” He answered within the hour.")),
    "[Unfortunately, I've been] ill[, so I haven't been able to] finish the lab.");
  assert.equal(show(expressionSnippet("Would it be possible to …?",
    "I kept it polite and specific: “Would it be possible to hand it in on Monday instead?” That was my lab, though — I've no idea what he can accept for your report.")),
    "[Would it be possible to] hand it in on Monday instead?");
  assert.equal(show(expressionSnippet("I'll make sure (that / to) …",
    "“Thank you, Professor. I'll make sure to send it by Friday.” Prof. Chen's voice follows her out: “Good. A clear plan — that's what I like to see.”")),
    "[I'll make sure] to send it by Friday.");
});

test("highlighting never marks words that are not there, in order, as whole words", () => {
  assert.equal(expressionSnippet("I could … by …", "Maybe I could not come."), null);           // no whole-word "by"
  assert.equal(expressionSnippet("I could … by …", "By Friday I could do it."), null);            // wrong order
  assert.equal(expressionSnippet("Would it be possible to …?", "Is it possible?"), null);
  assert.equal(show(expressionSnippet("I'll make sure …", "I’ll make sure it’s done.")), "[I’ll make sure] it’s done.");
  assert.equal(expressionSnippet("…", "anything"), null);
});

test("every expression function has a short Chinese label", () => {
  for (const f of ["Making a request", "Explaining the reason and your progress", "Offering an alternative", "Making a commitment"]) assert.ok(FUNCTION_ZH[f]);
});
