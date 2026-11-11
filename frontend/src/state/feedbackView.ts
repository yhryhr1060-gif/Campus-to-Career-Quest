// Pure view logic for the writing-feedback panel. No React and no network, so it can be tested
// directly. The server keeps the authoritative record; this mirrors its rules for display.
import type { DraftVersion, RecheckResult, WritingPoint } from "../types";

export type PointStatus = "open" | "resolved" | "still_present" | "kept_by_user" | "cannot_judge" | "not_rechecked" | "praise";

export interface PointView {
  point: WritingPoint;
  status: PointStatus;
  recheck: RecheckResult | null;
  group: "layer1" | "layer2" | "layer3" | "uncertain" | "praise";
}

export interface FeedbackView {
  base: DraftVersion | null;          // the version the shown feedback was written for
  stale: boolean;                     // the editor no longer holds that version's text
  rechecked: boolean;                 // the earlier points were rechecked against the editor's text
  recheckIncomplete: boolean;         // the automated recheck could not run for some points
  checkFailed: boolean;               // the last check of the editor's text did not complete
  checkPending: boolean;
  reviewUnavailable: boolean;         // the second reading did not run
  reviewRetryable: boolean;           // technical failure or missing second-reader answers
  limited: boolean;
  itemLimit: number;
  omittedCount: number;
  invalidCount: number;
  points: PointView[];
  open: PointView[];                  // still open for the text in the editor
  uncertainCount: number;
  feedbackState: "none" | "checked_this_version" | "rechecked_this_version" | "changed_since_check";
}

const OPEN: PointStatus[] = ["open", "still_present", "cannot_judge", "not_rechecked"];
const RETRY_BASES = new Set(["ai_unavailable", "ai_no_answer", "evidence_not_in_draft", "evidence_missing", "context_recheck_needed"]);

export function isRetryableRecheck(result: RecheckResult): boolean {
  return result.retryable === true || RETRY_BASES.has(result.basis);
}

export function buildFeedbackView(versions: DraftVersion[], draft: string): FeedbackView {
  const latest = versions.length ? versions[versions.length - 1] : null;
  const checkFailed = !!latest && latest.text === draft && latest.feedback?.status === "unavailable";
  const checkPending = !!latest && latest.text === draft && latest.feedback?.status === "pending";
  let base: DraftVersion | null = null;
  for (let i = versions.length - 1; i >= 0; i--) {
    if (versions[i].feedback?.status === "ok") { base = versions[i]; break; }
  }
  if (!base) {
    return { base: null, stale: false, rechecked: false, recheckIncomplete: false, checkFailed, checkPending,
      reviewUnavailable: false, reviewRetryable: false, limited: false, itemLimit: 12, omittedCount: 0,
      invalidCount: 0, points: [], open: [], uncertainCount: 0, feedbackState: "none" };
  }
  const stale = base.text !== draft;
  const recheck = stale && latest && latest.text === draft && latest.recheck?.base_version === base.version
    ? latest.recheck : null;
  const results = new Map((recheck?.results ?? []).map((r) => [r.id, r]));
  const feedback = base.feedback!;
  const issues = (feedback.items ?? []).filter((p) => p.type !== "positive");
  // Infer missing statuses for round-5 saves without treating a valid unsure answer as a failure.
  const reviewRetryable = feedback.review_retryable === true || feedback.review_status === "unavailable" ||
    feedback.review_status === "partial" || issues.some((p) => p.reviewed === false || p.flags.includes("not_reviewed"));
  const points: PointView[] = (base.feedback?.items ?? []).map((point) => {
    const result = results.get(point.id) ?? null;
    const keeps = base!.decisions[point.id] === "keep";
    let status: PointStatus;
    if (point.type === "positive") status = "praise";
    else if (!stale) status = keeps ? "kept_by_user" : "open";
    else if (!recheck || !result) status = "not_rechecked";
    else if (result.status === "still_present" && keeps) status = "kept_by_user";
    else if (result.status === "kept_by_user" && !keeps) status = "still_present";
    else status = result.status;
    const group = point.type === "positive" ? "praise" : point.judgement === "uncertain" ? "uncertain"
      : point.layer === 1 ? "layer1" : point.layer === 2 ? "layer2" : "layer3";
    return { point, status, recheck: result, group };
  });
  return {
    base, stale, rechecked: !!recheck, checkFailed, checkPending,
    recheckIncomplete: !!recheck && (recheck.retryable === true || recheck.results.some(isRetryableRecheck) ||
      issues.some((p) => !results.has(p.id))),
    reviewUnavailable: base.feedback?.review_status === "unavailable",
    reviewRetryable,
    limited: feedback.possibly_incomplete === true || (feedback.items?.length ?? 0) >= (feedback.item_limit ?? 12) ||
      (feedback.over_limit_count ?? 0) > 0,
    itemLimit: feedback.item_limit ?? 12,
    omittedCount: feedback.over_limit_count ?? 0,
    invalidCount: feedback.invalid_count ?? 0,
    points, open: points.filter((p) => OPEN.includes(p.status)),
    uncertainCount: points.filter((p) => p.group === "uncertain").length,
    feedbackState: !stale ? "checked_this_version" : recheck ? "rechecked_this_version" : "changed_since_check",
  };
}

/** Which layers to show. Layers are revealed one at a time; an empty first layer is skipped. */
export function visibleGroups(view: FeedbackView, revealed: number): { shown: PointView["group"][]; next: PointView["group"] | null } {
  const order: PointView["group"][] = ["layer1", "layer2", "layer3"];
  const nonEmpty = order.filter((g) => view.points.some((p) => p.group === g || (g === "layer3" && p.group === "praise")));
  const count = Math.max(1, Math.min(revealed, nonEmpty.length));
  return { shown: nonEmpty.slice(0, count), next: nonEmpty[count] ?? null };
}

export interface DiffPart { kind: "same" | "removed" | "added"; text: string }

/** Word-level before/after comparison (longest common subsequence over words and spaces). */
export function wordDiff(before: string, after: string): DiffPart[] {
  const a = before.split(/(\s+)/).filter((t) => t !== "");
  const b = after.split(/(\s+)/).filter((t) => t !== "");
  const rows = a.length + 1, cols = b.length + 1;
  const table = new Uint16Array(rows * cols);
  for (let i = a.length - 1; i >= 0; i--) {
    for (let j = b.length - 1; j >= 0; j--) {
      table[i * cols + j] = a[i] === b[j] ? table[(i + 1) * cols + j + 1] + 1
        : Math.max(table[(i + 1) * cols + j], table[i * cols + j + 1]);
    }
  }
  const parts: DiffPart[] = [];
  const push = (kind: DiffPart["kind"], text: string) => {
    const last = parts[parts.length - 1];
    if (last && last.kind === kind) last.text += text; else parts.push({ kind, text });
  };
  let i = 0, j = 0;
  while (i < a.length && j < b.length) {
    if (a[i] === b[j]) { push("same", a[i]); i++; j++; }
    else if (table[(i + 1) * cols + j] >= table[i * cols + j + 1]) { push("removed", a[i]); i++; }
    else { push("added", b[j]); j++; }
  }
  while (i < a.length) push("removed", a[i++]);
  while (j < b.length) push("added", b[j++]);
  return parts;
}
