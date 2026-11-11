"""JSON shapes the model must return. Every model output is validated against one of these."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# ---------------------------------------------------------------- feedback (prepare)

FeedbackCategory = Literal["error", "info_missing", "upgrade", "positive"]


class FeedbackItem(BaseModel):
    category: FeedbackCategory
    quote: str = Field(min_length=1, description="exact words copied from the draft")
    suggestion: str = ""
    reason: str = ""


class FeedbackOut(BaseModel):
    items: list[FeedbackItem] = Field(default_factory=list)


# ---------------------------------------------------------------- whole-draft writing feedback (prepare)

FeedbackType = Literal["grammar", "spelling", "punctuation", "capitalization", "accuracy", "logic", "task_info", "politeness", "naturalness", "positive"]
FeedbackJudgement = Literal["clear_error", "expression_issue", "optional", "uncertain"]


class DraftFeedbackItem(BaseModel):
    """One point about one draft version. Empty reason/suggestion are rejected by code, per item,
    so a single careless item does not discard the model's whole answer."""

    type: FeedbackType
    judgement: FeedbackJudgement
    serious: bool = False
    quote: str = Field(min_length=1, description="exact words copied from the draft")
    suggestion: str = ""
    reason: str = ""


class DraftFeedbackOut(BaseModel):
    items: list[DraftFeedbackItem] = Field(default_factory=list, max_length=40)


class FeedbackReviewItem(BaseModel):
    id: str = Field(min_length=1, max_length=40)
    verdict: Literal["agree", "disagree", "unsure"]
    note: str = Field(default="", max_length=400)


class FeedbackReviewOut(BaseModel):
    reviews: list[FeedbackReviewItem] = Field(max_length=60)


class RecheckItem(BaseModel):
    id: str = Field(min_length=1, max_length=40)
    status: Literal["resolved", "still_present", "unclear"]
    current_quote: str = Field(default="", description="exact words from the NEW draft, or empty if removed")
    note: str = Field(default="", max_length=400)


class RecheckOut(BaseModel):
    results: list[RecheckItem] = Field(max_length=60)


# ---------------------------------------------------------------- summary (prepare handoff)

SummaryField = Literal["situation", "request", "proposal"]


class SummaryItem(BaseModel):
    field: SummaryField
    value: str = Field(min_length=1)
    quote: str = Field(min_length=1, description="exact words copied from the email")


class SummaryOut(BaseModel):
    fields: list[SummaryItem] = Field(default_factory=list)


class OpeningOut(BaseModel):
    reply: str = Field(min_length=1, max_length=400)


# ---------------------------------------------------------------- NPC turn (negotiate)


class CandidateFact(BaseModel):
    """Something the learner may have proposed, refused or confirmed in this turn.

    field:    submission_plan | outline_due | final_due | confirm
    value:    for *_due: a story-clock code ("FRI 18:00", "SUN", "NEXT WED"); for submission_plan:
              "full" | "staged"; for confirm: null
    quote:    exact words from `source` that support the fact
    source:   where the quote comes from — the learner's turn, Prof. Chen's previous turn
              (e.g. learner says "yes, that works" to "Would Saturday work?"), or the email
    polarity: affirm | negate     ("I can't do Friday" is negate)
    mode:     assert | conditional | question | quoted   ("my friend said Sunday" is quoted)
    """

    field: Literal["submission_plan", "outline_due", "final_due", "confirm"]
    value: str | None = None
    quote: str = Field(min_length=1)
    source: Literal["player", "npc_previous", "email"] = "player"
    polarity: Literal["affirm", "negate"]
    mode: Literal["assert", "conditional", "question", "quoted"]


class NpcTurn(BaseModel):
    reply: str = Field(min_length=1, max_length=500)
    mood: Literal["neutral", "warm", "concerned"] = "neutral"
    candidate_facts: list[CandidateFact] = Field(default_factory=list)
    needs_clarification: bool = False
    off_topic: bool = False
