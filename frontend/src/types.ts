export type Phase = "explore" | "prepare" | "negotiate" | "outcome";

export interface Topic {
  id: string;
  ask: string;
  reply: string;
  note: string;
  expressions: string[];
  requires: string | null;
}

export interface Hotspot {
  id: string;
  area: string;
  label: string;
  title: string;
  text: string;
  note: string;
  kind: "object" | "person" | "moment";
  speaker: string;
  expressions: string[];
  topics: Topic[];
}

export interface Area { id: string; label: string; description: string; icon: string }

export interface Content {
  id: string;
  version: string;
  title: string;
  location: string;
  story_clock_label: string;
  player: { name: string };
  intro: string;
  start_area: string;
  task: { situation: string; target: string; goal: string; goal_short: string };
  characters: Record<string, { name: string; role: string }>;
  areas: Area[];
  hotspots: Hotspot[];
  expressions: { id: string; text: string; function: string }[];
  hint_count: number;
  xp: { prep_complete: number; first_agreement: number };
  input_limits: { email: number; turn: number; prediction: number; backup_plan: number };
}

export interface SummaryField {
  field: "situation" | "request" | "proposal";
  value: string;
  quote: string;
}

export interface Plan {
  submission_plan: "full" | "staged";
  outline_due: string | null;
  final_due: string;
  describe: string;
  notes: string[];
}

export interface Turn {
  speaker: "npc" | "player";
  text: string;
  mood?: "neutral" | "warm" | "concerned";
  kind?: string;
  input_mode?: string;
  turn_no?: number;
}

export interface SessionState {
  session_id: string;
  phase: Phase;
  player_name: string;
  needs_name: boolean;
  location: string;
  places_visited: string[];
  /** Target expressions the learner has met in the story. Exposure only; says nothing about mastery. */
  expressions_seen: string[];
  /** Clue ids: a hotspot ("syllabus") or one question put to a person ("ivy:extension"). */
  notes: string[];
  prep: {
    draft: string;
    email_final: string | null;
    summary: SummaryField[] | null;
    summary_confirmed: boolean;
    prediction: string | null;
    backup_plan: string | null;
    done: boolean;
    versions: DraftVersion[];
  };
  negotiation: {
    turns: Turn[];
    turn_count: number;
    max_turns: number;
    agreement: {
      status: "none" | "proposed" | "confirmed";
      constraint_fired: boolean;
      proposal: Plan | null;
      confirmed: Plan | null;
      confirm_quote: string | null;
    };
    hints_unlocked: boolean;
    hints: { level: number; kind: string; text: string }[];
    paused: boolean;
    attempt: number;
    key_points: { field: string; text: string }[];
  };
  xp: number;
}

export type FeedbackType = "grammar" | "spelling" | "punctuation" | "capitalization" | "accuracy" | "logic" | "task_info" | "politeness" | "naturalness" | "positive";
export type Judgement = "clear_error" | "expression_issue" | "optional" | "uncertain";

/** One point of the whole-draft check. It belongs to exactly one draft version. */
export interface WritingPoint {
  id: string;
  type: FeedbackType;
  judgement: Judgement;
  serious: boolean;
  quote: string;
  suggestion: string;
  reason: string;
  flags: string[];
  layer: 1 | 2 | 3;
  reviewed?: boolean;
  review_note?: string;
}

export type RecheckStatus = "resolved" | "still_present" | "kept_by_user" | "cannot_judge";

export interface RecheckResult {
  id: string;
  status: RecheckStatus;
  basis: string;
  current_quote: string;
  note: string;
  retryable?: boolean;
}

export interface DraftVersion {
  version: number;
  text: string;
  created_at: string | null;
  origin: string | null;
  feedback: null | {
    status: "ok" | "unavailable" | "pending";
    review_status?: "ok" | "unavailable" | "partial" | "skipped";
    review_retryable?: boolean;
    items?: WritingPoint[];
    dropped_count?: number;
    invalid_count?: number;
    item_limit?: number;
    over_limit_count?: number;
    possibly_incomplete?: boolean;
  };
  recheck: null | { base_version: number; results: RecheckResult[]; used_model?: boolean;
    status?: "ok" | "partial" | "unavailable"; retryable?: boolean };
  decisions: Record<string, "keep">;
}

export interface FeedbackItem {
  category: "error" | "info_missing" | "upgrade" | "positive";
  quote: string;
  suggestion: string;
  reason: string;
}

export interface Outcome {
  status: "agreed" | "not_agreed";
  notes: string[];
  ending: { id: string; title: string; plan: string; confirm_quote: string | null };
  other_ending: { id: string; face_down: boolean; text: string } | null;
  reflection?: { reason: string; known_constraints: string[]; next_steps: string[]; technical_failures: number };
  strategy: { move: string; kind: "did_well" | "try_next"; text: string; quote: string | null }[];
  language: (FeedbackItem & { line: string })[];
  language_available: boolean;
  xp: number;
  turns: number;
}
