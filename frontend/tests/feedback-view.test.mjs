import assert from "node:assert/strict";
import test from "node:test";
import { buildFeedbackView, visibleGroups, wordDiff } from "../src/state/feedbackView.ts";

const V1 = "I has been sick, so I can't finish it. Sorry.";
const V2 = "I have been sick, so I can't finish it. Sorry.";
const point = (id, extra = {}) => ({ id, type: "grammar", judgement: "clear_error", serious: true, quote: "I has been",
  suggestion: "I have been", reason: "r", flags: [], layer: 1, ...extra });
const items = [
  point("v1-1"),
  point("v1-2", { type: "logic", judgement: "expression_issue", layer: 2, quote: "so I can't finish it" }),
  point("v1-3", { type: "politeness", judgement: "optional", layer: 3, quote: "Sorry" }),
  point("v1-4", { type: "accuracy", judgement: "uncertain", layer: 2, quote: "sick", flags: ["reviewer_disagrees"] }),
  point("v1-5", { type: "positive", judgement: "optional", layer: 3, quote: "Sorry", suggestion: "" }),
];
const version = (n, text, extra = {}) => ({ version: n, text, created_at: null, origin: "check", feedback: null,
  recheck: null, decisions: {}, ...extra });
const checked = (extra = {}) => version(1, V1, { feedback: { status: "ok", review_status: "ok", items }, ...extra });
const statuses = (view) => Object.fromEntries(view.points.map((p) => [p.point.id, p.status]));

test("no checked version means no feedback and nothing open", () => {
  const view = buildFeedbackView([], V1);
  assert.equal(view.base, null);
  assert.equal(view.feedbackState, "none");
  assert.equal(view.open.length, 0);
});

test("feedback for the text in the editor is current and its points are open", () => {
  const view = buildFeedbackView([checked()], V1);
  assert.equal(view.stale, false);
  assert.equal(view.feedbackState, "checked_this_version");
  assert.deepEqual(statuses(view), { "v1-1": "open", "v1-2": "open", "v1-3": "open", "v1-4": "open", "v1-5": "praise" });
  assert.equal(view.open.length, 4);
  assert.equal(view.uncertainCount, 1);
});

test("once the learner edits, the old feedback stays but is marked as an earlier version", () => {
  const view = buildFeedbackView([checked()], V2);
  assert.equal(view.base.version, 1);
  assert.equal(view.stale, true);
  assert.equal(view.rechecked, false);
  assert.equal(view.feedbackState, "changed_since_check");
  assert.equal(view.points.length, 5);
  assert.ok(view.points.filter((p) => p.status !== "praise").every((p) => p.status === "not_rechecked"));
});

test("a result for an older version never becomes the feedback of a newer checked version", () => {
  const newer = version(2, V2, { feedback: { status: "ok", review_status: "skipped", items: [] } });
  const view = buildFeedbackView([checked(), newer], V2);
  assert.equal(view.base.version, 2);
  assert.equal(view.points.length, 0);
});

test("recheck results are shown per point and the learner's keep choice wins", () => {
  const recheck = { base_version: 1, results: [
    { id: "v1-1", status: "resolved", basis: "ai_recheck", current_quote: "I have been", note: "" },
    { id: "v1-2", status: "still_present", basis: "sentence_unchanged", current_quote: "", note: "" },
    { id: "v1-3", status: "still_present", basis: "sentence_unchanged", current_quote: "", note: "" },
    { id: "v1-4", status: "cannot_judge", basis: "ai_unavailable", current_quote: "", note: "" },
  ] };
  const view = buildFeedbackView([checked({ decisions: { "v1-3": "keep" } }), version(2, V2, { origin: "recheck", recheck })], V2);
  assert.equal(view.feedbackState, "rechecked_this_version");
  assert.deepEqual(statuses(view), { "v1-1": "resolved", "v1-2": "still_present", "v1-3": "kept_by_user",
    "v1-4": "cannot_judge", "v1-5": "praise" });
  assert.deepEqual(view.open.map((p) => p.point.id), ["v1-2", "v1-4"]);
  assert.equal(view.recheckIncomplete, true);
});

test("a recheck of some other text does not describe the editor's text", () => {
  const recheck = { base_version: 1, results: [{ id: "v1-1", status: "resolved", basis: "ai_recheck", current_quote: "", note: "" }] };
  const view = buildFeedbackView([checked(), version(2, V2, { recheck })], V2 + " More.");
  assert.equal(view.rechecked, false);
  assert.equal(statuses(view)["v1-1"], "not_rechecked");
});

test("a failed check of the current text is reported while older feedback stays available", () => {
  const failed = version(2, V2, { feedback: { status: "unavailable" } });
  const view = buildFeedbackView([checked(), failed], V2);
  assert.equal(view.checkFailed, true);
  assert.equal(view.base.version, 1);
  assert.equal(view.stale, true);
  assert.equal(buildFeedbackView([failed], V2).base, null);
});

test("layers are revealed one at a time and uncertain points are kept apart", () => {
  const view = buildFeedbackView([checked()], V1);
  assert.deepEqual(visibleGroups(view, 1), { shown: ["layer1"], next: "layer2" });
  assert.deepEqual(visibleGroups(view, 2), { shown: ["layer1", "layer2"], next: "layer3" });
  assert.deepEqual(visibleGroups(view, 9), { shown: ["layer1", "layer2", "layer3"], next: null });
  assert.equal(view.points.find((p) => p.point.id === "v1-4").group, "uncertain");
  const onlyStyle = buildFeedbackView([version(1, V1, { feedback: { status: "ok", items: [items[2]] } })], V1);
  assert.deepEqual(visibleGroups(onlyStyle, 1), { shown: ["layer3"], next: null });
});

test("word diff marks only what changed and rebuilds both texts", () => {
  const parts = wordDiff(V1, V2);
  assert.deepEqual(parts.filter((p) => p.kind !== "same"), [{ kind: "removed", text: "has" }, { kind: "added", text: "have" }]);
  assert.equal(parts.filter((p) => p.kind !== "added").map((p) => p.text).join(""), V1);
  assert.equal(parts.filter((p) => p.kind !== "removed").map((p) => p.text).join(""), V2);
  assert.deepEqual(wordDiff("", "new"), [{ kind: "added", text: "new" }]);
  assert.deepEqual(wordDiff("same text", "same text"), [{ kind: "same", text: "same text" }]);
});

test("failed or partial second reading allows retry while retaining the first-pass points", () => {
  for (const review_status of ["unavailable", "partial"]) {
    const feedback = { status: "ok", review_status, review_retryable: true, items };
    const view = buildFeedbackView([checked({ feedback })], V1);
    assert.equal(view.reviewRetryable, true);
    assert.equal(view.checkFailed, false);
    assert.equal(view.stale, false);
    assert.deepEqual(view.points.map((p) => p.point.id), items.map((p) => p.id));
  }
});

test("legacy second-reading omissions remain retryable even if saved status said ok", () => {
  const feedback = { status: "ok", review_status: "ok", items: [point("v1-1", { reviewed: false, flags: ["not_reviewed"] })] };
  assert.equal(buildFeedbackView([checked({ feedback })], V1).reviewRetryable, true);
  feedback.items[0] = point("v1-1", { reviewed: true, judgement: "uncertain", flags: ["reviewer_unsure"] });
  assert.equal(buildFeedbackView([checked({ feedback })], V1).reviewRetryable, false);
});

test("all technical recheck failures stay retryable, including round-5 saved results", () => {
  for (const basis of ["ai_unavailable", "ai_no_answer", "evidence_not_in_draft", "evidence_missing", "context_recheck_needed"]) {
    const base = checked({ feedback: { status: "ok", items: [items[0]] } });
    const recheck = { base_version: 1, results: [{ id: "v1-1", status: "cannot_judge", basis, current_quote: "", note: "" }] };
    const view = buildFeedbackView([base, version(2, V2, { recheck })], V2);
    assert.equal(view.recheckIncomplete, true, basis);
    assert.equal(view.open.length, 1, basis);
    assert.equal(view.points[0].status, "cannot_judge", basis);
  }
});

test("a valid unsure answer remains cannot_judge but is not a failed retryable check", () => {
  const base = checked({ feedback: { status: "ok", items: [items[0]] } });
  const recheck = { base_version: 1, status: "ok", retryable: false, results: [
    { id: "v1-1", status: "cannot_judge", basis: "ai_unsure", current_quote: "I have been sick", note: "The writer's intention is unclear.", retryable: false },
  ] };
  const view = buildFeedbackView([base, version(2, V2, { recheck })], V2);
  assert.equal(view.recheckIncomplete, false);
  assert.equal(view.points[0].status, "cannot_judge");
  assert.equal(view.open.length, 1);
});

test("missing rows in a legacy recheck are incomplete, not resolved", () => {
  const view = buildFeedbackView([checked(), version(2, V2, { recheck: { base_version: 1, results: [] } })], V2);
  assert.equal(view.recheckIncomplete, true);
  assert.equal(view.points[0].status, "not_rechecked");
});

test("pending checks survive refresh as pending and do not hide old feedback", () => {
  const pending = version(2, V2, { feedback: { status: "pending" } });
  const view = buildFeedbackView([checked(), pending], V2);
  assert.equal(view.checkPending, true);
  assert.equal(view.checkFailed, false);
  assert.equal(view.base.version, 1);
  assert.equal(view.stale, true);
});

test("cap metadata and legacy cap-sized feedback expose incomplete coverage", () => {
  const capped = Array.from({ length: 12 }, (_, i) => point(`v1-${i + 1}`));
  let view = buildFeedbackView([checked({ feedback: { status: "ok", items: capped, item_limit: 12,
    over_limit_count: 3, possibly_incomplete: true, invalid_count: 1 } })], V1);
  assert.equal(view.limited, true);
  assert.equal(view.itemLimit, 12);
  assert.equal(view.omittedCount, 3);
  assert.equal(view.invalidCount, 1);
  view = buildFeedbackView([checked({ feedback: { status: "ok", items: capped } })], V1);
  assert.equal(view.limited, true);
  assert.equal(buildFeedbackView([checked()], V1).limited, false);
});

test("spelling, punctuation and capitalization keep distinct types alongside grammar and optional style", () => {
  const mechanics = ["spelling", "punctuation", "capitalization"].map((type, i) => point(`v1-${i + 1}`, { type, layer: 2 }));
  const view = buildFeedbackView([checked({ feedback: { status: "ok", items: [...mechanics, items[2]] } })], V1);
  assert.deepEqual(view.points.map((p) => p.point.type), ["spelling", "punctuation", "capitalization", "politeness"]);
  assert.deepEqual(visibleGroups(view, 2), { shown: ["layer2", "layer3"], next: null });
  assert.equal(view.points[3].point.judgement, "optional");
});
