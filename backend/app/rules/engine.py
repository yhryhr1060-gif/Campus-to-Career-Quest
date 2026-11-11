"""The rule engine — pure code. It alone decides whether an agreement exists.

The model reads the learner's words and returns *candidate facts* (what was proposed, refused or
confirmed, with a quote and its polarity/mode). The engine then:

1. drops any fact whose quote is not really in its source (learner turn, Prof. Chen's previous turn,
   or the email), and ignores quoted/reported speech ("my friend said Sunday");
2. records refusals ("I can't do Friday") as excluded options, and withdraws a standing proposal
   the learner turns down;
3. builds a plan from affirmed facts — assertions, conditional offers ("If I send the outline on
   Friday, could I finish on Sunday?") and questions all count as *proposals*, never as agreements;
4. checks the plan against the chapter limits (upper bounds: an earlier time is fine);
   the first proposal past the final limit fires the one-off constraint change;
5. asks for one follow-up before accepting the first legal plan (the NPC "pushes back once");
6. moves a legal plan to `proposed` and has Prof. Chen restate it with concrete days and clock times
   (a day without a time gets the chapter's default due time, which the learner must then confirm);
7. moves to `confirmed` only when the learner clearly says yes to the restated plan (or presses
   Confirm), and the server re-checks it. Any change to the plan replaces the old proposal.

State lives in a plain dict (stored as JSON in the session).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..content import Chapter
from ..llm.schemas import CandidateFact
from ..story_clock import StoryTime, parse_code, parse_stored_time
from .quotes import quote_in
from .utterance import confirm_blockers, is_explicit_confirmation

DUE_FIELDS = ("outline_due", "final_due")


def _empty_draft() -> dict:
    return {"outline_due": None, "final_due": None, "submission_plan": None}


def _withdraw(state: dict) -> None:
    """A superseded/refused plan must not survive in another state slot."""
    state.update(status="none", proposal=None, pending=None, draft=_empty_draft())


def new_agreement_state() -> dict:
    return {
        "status": "none",            # none | proposed | confirmed
        "proposal": None,            # plan dict waiting for the learner's confirmation
        "confirmed": None,           # plan dict once confirmed and rule-checked
        "confirm_quote": None,       # the learner's words when confirming
        "draft": _empty_draft(),
        "pending": None,             # legal plan held back until Prof. Chen has pushed back once
        "constraint_fired": False,
        "pushbacks": 0,
        "excluded": [],              # refusals, e.g. "outline_due:FRI"
    }


# ---------------------------------------------------------------------------- plans


@dataclass(frozen=True)
class Plan:
    submission_plan: str             # "full" | "staged"
    outline_due: StoryTime | None
    final_due: StoryTime

    def to_dict(self, *, for_storage: bool = False) -> dict:
        data = {
            "submission_plan": self.submission_plan,
            "outline_due": self.outline_due.code() if self.outline_due else None,
            "final_due": self.final_due.code(),
        }
        if for_storage:
            data["_times"] = {
                "outline_due": self.outline_due.storage() if self.outline_due else None,
                "final_due": self.final_due.storage(),
            }
        return data

    def describe(self) -> str:
        if self.submission_plan == "staged" and self.outline_due:
            return f"the outline by {self.outline_due.spoken()}, and the full report by {self.final_due.spoken()}"
        return f"the full report by {self.final_due.spoken()}"

    def notes(self) -> list[str]:
        out = []
        if self.submission_plan == "staged" and self.outline_due:
            out.append(f"{self.outline_due.short()} · outline")
        out.append(f"{self.final_due.short()} · full report")
        return out


def plan_from_dict(d: dict | None, chapter: Chapter) -> Plan | None:
    if not d:
        return None
    final = _stored_field(d, "final_due", chapter)
    if final is None:
        return None
    outline = _stored_field(d, "outline_due", chapter)
    if d.get("outline_due") and outline is None:
        return None
    return Plan(d["submission_plan"], outline, final)


def _stored_field(data: dict, name: str, chapter: Chapter) -> StoryTime | None:
    metadata = data.get("_times")
    if metadata is not None:
        if not isinstance(metadata, dict) or name not in metadata:
            return None
        if data.get(name) and metadata[name] is None:
            return None
        return parse_stored_time(data.get(name), chapter.now, metadata[name])
    return parse_stored_time(data.get(name), chapter.now)


def check_plan(chapter: Chapter, plan: Plan) -> str | None:
    """Return None if the plan is one of the chapter's legal endings, else a reason code."""
    now = chapter.now.total()
    if not plan.final_due.concrete or (plan.outline_due and not plan.outline_due.concrete):
        return "vague"
    if plan.final_due.total() > chapter.final_max.total():
        return "after_limit"
    if plan.final_due.total() <= now:
        return "past"
    if plan.submission_plan == "staged":
        if plan.outline_due is None:
            return "incomplete"
        if plan.outline_due.total() <= now:
            return "past"
        if plan.outline_due.total() > chapter.outline_max.total():
            return "outline_late"
        if plan.outline_due.total() >= plan.final_due.total():
            return "order"
    elif plan.submission_plan != "full":
        return "unknown_plan"
    return None


# ---------------------------------------------------------------------------- decisions


@dataclass
class Decision:
    kind: str                         # see the evaluate_turn docstring
    reply_override: str | None = None
    plan: dict | None = None
    dropped: list[dict] = field(default_factory=list)
    constraint_fired_now: bool = False
    became_confirmed: bool = False

    def to_log(self) -> dict:
        return {
            "kind": self.kind,
            "plan": self.plan,
            "dropped": self.dropped,
            "constraint_fired_now": self.constraint_fired_now,
            "became_confirmed": self.became_confirmed,
        }


def _drop(dropped: list[dict], f: CandidateFact, reason: str) -> None:
    dropped.append({"reason": reason, "fact": f.model_dump()})


def _fill(t: StoryTime | None, chapter: Chapter) -> tuple[StoryTime | None, bool]:
    if t is None or t.concrete:
        return t, False
    return t.with_time(chapter.default_due_time), True


def _reject_line(chapter: Chapter, reason: str) -> str:
    fm, om = chapter.final_max.spoken(), chapter.outline_max.spoken()
    return {
        "after_limit": chapter.line("reject_after_limit", final_max=fm),
        "outline_late": chapter.line("reject_outline_late", outline_max=om, final_max=fm),
        "past": chapter.line("reject_past"),
        "order": chapter.line("reject_order"),
    }.get(reason, chapter.line("neutral_fallback"))


def _restate(chapter: Chapter, plan: Plan) -> str:
    if plan.submission_plan == "staged" and plan.outline_due:
        return chapter.line("restate_staged", outline=plan.outline_due.spoken(), final=plan.final_due.spoken())
    return chapter.line("restate_full", final=plan.final_due.spoken())


def _confirm(chapter: Chapter, state: dict, quote: str, dec: Decision) -> Decision:
    plan = plan_from_dict(state["proposal"], chapter)
    if plan is None or check_plan(chapter, plan) is not None:  # server re-check
        _withdraw(state)
        dec.kind = "confirm_rejected_by_rules"
        dec.reply_override = chapter.line("neutral_fallback")
        return dec
    state["status"] = "confirmed"
    state["confirmed"] = plan.to_dict(for_storage=True)
    state["confirm_quote"] = quote
    state["pending"] = None
    dec.kind = "confirmed"
    dec.plan = plan.to_dict()
    dec.became_confirmed = True
    dec.reply_override = chapter.line("agreed_close")
    return dec


def evaluate_turn(
    chapter: Chapter,
    state: dict,
    facts: list[CandidateFact],
    *,
    player_text: str,
    npc_previous: str = "",
    email: str = "",
) -> Decision:
    """Update `state` in place from one learner turn and return what happened.

    Decision.kind is one of:
      none, withdrawn, incomplete, constraint_change, rejected, follow_up, restate,
      confirm_unclear, confirmed, confirm_rejected_by_rules, already_confirmed
    """
    dec = Decision(kind="none")
    if state["status"] == "confirmed":
        dec.kind = "already_confirmed"
        return dec

    sources = {"player": player_text, "npc_previous": npc_previous, "email": email}
    valid: list[CandidateFact] = []
    for f in facts:
        if not quote_in(f.quote, sources.get(f.source, "")):
            _drop(dec.dropped, f, "quote_not_found")
        elif f.mode == "quoted":
            _drop(dec.dropped, f, "quoted_speech")
        elif f.field == "confirm" and (f.source != "player" or f.mode != "assert"):
            _drop(dec.dropped, f, "confirmation_requires_current_player_assertion")
        elif f.field == "confirm" and f.polarity == "affirm" and not is_explicit_confirmation(player_text):
            _drop(dec.dropped, f, "confirmation_not_explicit")
        elif f.field in DUE_FIELDS and f.value is not None and parse_code(f.value, chapter.now) is None:
            _drop(dec.dropped, f, "unparsable_time")
        elif f.field in DUE_FIELDS and f.value is None:
            _drop(dec.dropped, f, "missing_value")
        else:
            valid.append(f)

    confirms = [f for f in valid if f.field == "confirm"]
    aff_dates = [f for f in valid if f.field in DUE_FIELDS and f.polarity == "affirm"]
    neg_dates = [f for f in valid if f.field in DUE_FIELDS and f.polarity == "negate"]
    plan_facts = [f for f in valid if f.field == "submission_plan" and f.polarity == "affirm"
                  and f.value in ("full", "staged")]
    yes = [f for f in confirms if f.polarity == "affirm"]
    no = [f for f in confirms if f.polarity == "negate"]

    for f in neg_dates:
        t = parse_code(f.value, chapter.now)
        state["excluded"].append(f"{f.field}:{t.code() if t else f.value}")

    proposal = plan_from_dict(state["proposal"], chapter) if state["status"] == "proposed" else None

    # --- 2. refusals invalidate pending proposals and the draft as well --------------------
    saved_plans = [d for d in (state["proposal"], state["pending"], state["draft"]) if d]
    has_plan = any(d.get("final_due") or d.get("outline_due") for d in saved_plans)
    refused = bool(no) and has_plan
    refused = refused or any(
        (saved := _stored_field(d, f.field, chapter)) is not None and _same_day(f, saved, chapter)
        for f in neg_dates for d in saved_plans
    )
    if refused:
        _withdraw(state)
        proposal = None
        # A replacement in this same turn must be built from its explicit facts alone.
        if not aff_dates and not plan_facts:
            dec.kind = "withdrawn"
            dec.reply_override = chapter.line("proposal_withdrawn")
            return dec

    # --- 7. a clear yes to the restated plan ------------------------------------------------
    if proposal is not None and yes and not no:
        merged = _merge(chapter, proposal.to_dict(for_storage=True), aff_dates, plan_facts)
        if merged == proposal:
            if confirm_blockers(player_text):
                dec.kind = "confirm_unclear"
                dec.plan = proposal.to_dict()
                dec.reply_override = chapter.line("confirm_unclear", plan=proposal.describe())
                return dec
            return _confirm(chapter, state, player_text, dec)
        # a "yes" with different times is a new proposal — fall through

    # --- 3–6. a (new or changed) proposal ----------------------------------------------------
    if aff_dates or plan_facts:
        base = proposal.to_dict(for_storage=True) if proposal is not None else state["draft"]
        plan = _merge(chapter, base, aff_dates, plan_facts)
        draft = _plan_to_draft(chapter, base, aff_dates, plan_facts)
        # Even an incomplete or invalid replacement supersedes the old confirmable plan.
        _withdraw(state)
        state["draft"] = draft
        if plan is None:
            dec.kind = "incomplete"         # e.g. only an outline date so far; the NPC asks for the rest
            return dec

        reason = check_plan(chapter, plan)
        if reason == "after_limit":
            _withdraw(state)
            state["pushbacks"] += 1
            if not state["constraint_fired"]:
                state["constraint_fired"] = True
                dec.kind = "constraint_change"
                dec.constraint_fired_now = True
                dec.reply_override = chapter.constraint_change.npc_line
            else:
                dec.kind = "rejected"
                dec.reply_override = _reject_line(chapter, reason)
            dec.plan = plan.to_dict()
            return dec
        if reason is not None:
            _withdraw(state)
            state["pushbacks"] += 1
            dec.kind = "rejected"
            dec.plan = plan.to_dict()
            dec.reply_override = _reject_line(chapter, reason)
            return dec

        # legal
        if state["pushbacks"] == 0:
            state["pending"] = plan.to_dict(for_storage=True)
            state["pushbacks"] += 1
            dec.kind = "follow_up"
            dec.plan = plan.to_dict()
            dec.reply_override = chapter.line("follow_up_before_agree")
            return dec
        state["status"], state["proposal"], state["pending"] = "proposed", plan.to_dict(for_storage=True), None
        dec.kind = "restate"
        dec.plan = plan.to_dict()
        dec.reply_override = _restate(chapter, plan)
        return dec

    # --- a plan held back for the follow-up question is now put forward ---------------------
    if state["pending"] and state["status"] == "none" and not no and not neg_dates:
        plan = plan_from_dict(state["pending"], chapter)
        if plan is not None and check_plan(chapter, plan) is None:
            state["status"], state["proposal"], state["pending"] = "proposed", plan.to_dict(for_storage=True), None
            dec.kind = "restate"
            dec.plan = plan.to_dict()
            dec.reply_override = _restate(chapter, plan)
            return dec

    return dec


def confirm_by_button(chapter: Chapter, state: dict) -> Decision:
    dec = Decision(kind="none")
    if state["status"] == "confirmed":
        dec.kind = "already_confirmed"
        return dec
    if state["status"] != "proposed" or not state["proposal"]:
        dec.kind = "nothing_to_confirm"
        return dec
    return _confirm(chapter, state, "[pressed Confirm]", dec)


# ---------------------------------------------------------------------------- helpers


def _same_day(f: CandidateFact, t: StoryTime, chapter: Chapter) -> bool:
    v = parse_code(f.value, chapter.now)
    return v is not None and v.day == t.day


def _merged_fields(chapter: Chapter, base: dict, aff_dates: list[CandidateFact],
                   plan_facts: list[CandidateFact]) -> tuple[str | None, StoryTime | None, StoryTime | None]:
    outline = _stored_field(base, "outline_due", chapter)
    final = _stored_field(base, "final_due", chapter)
    for f in aff_dates:
        t = parse_code(f.value, chapter.now)
        # A confirmation may cite the NPC's restatement of today's saved weekday. That
        # weekday has already been resolved; it is not a fresh request for next week.
        existing = outline if f.field == "outline_due" else final
        if f.source == "npc_previous" and existing is not None:
            restated = parse_stored_time(f.value, chapter.now)
            if restated is not None and restated.day == existing.day:
                t = restated
        if f.field == "outline_due":
            outline = t
        else:
            final = t
    # An explicit "the whole report" / "split it" this turn wins; otherwise the shape follows the dates.
    kind = plan_facts[-1].value if plan_facts else (
        "staged" if outline is not None else base.get("submission_plan") or "full"
    )
    if kind == "full":
        outline = None
    return kind, outline, final


def _plan_to_draft(chapter: Chapter, base: dict, aff_dates: list[CandidateFact],
                   plan_facts: list[CandidateFact]) -> dict:
    kind, outline, final = _merged_fields(chapter, base, aff_dates, plan_facts)
    return {
        "submission_plan": kind,
        "outline_due": outline.code() if outline else None,
        "final_due": final.code() if final else None,
        "_times": {
            "outline_due": outline.storage() if outline else None,
            "final_due": final.storage() if final else None,
        },
    }


def _merge(chapter: Chapter, base: dict, aff_dates: list[CandidateFact],
           plan_facts: list[CandidateFact]) -> Plan | None:
    kind, outline, final = _merged_fields(chapter, base, aff_dates, plan_facts)
    if final is None:
        return None
    if kind is None:
        kind = "staged" if outline is not None else "full"
    if kind == "staged" and outline is None:
        return None
    outline, _ = _fill(outline, chapter)
    final, _ = _fill(final, chapter)
    assert final is not None
    return Plan(kind, outline, final)


def summarize_state(state: dict, chapter: Chapter) -> dict[str, Any]:
    """Public view of the agreement for the browser."""
    out: dict[str, Any] = {"status": state["status"], "constraint_fired": state["constraint_fired"]}
    for key in ("proposal", "confirmed"):
        p = plan_from_dict(state.get(key), chapter)
        out[key] = None if p is None else {**p.to_dict(), "describe": p.describe(), "notes": p.notes()}
    out["confirm_quote"] = state.get("confirm_quote")
    return out
