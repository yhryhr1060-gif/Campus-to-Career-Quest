"""Mock model (LLM_MOCK=1). Deterministic, keyword-based, good enough to play the chapter by typing and
to run the tests without an API key. It is a stand-in for the model, not part of the rules: everything
it returns still goes through the same validation, quote checks and rule engine as real output.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# ---------------------------------------------------------------- date mentions

_DAY_WORDS = {
    "monday": "MON", "tuesday": "TUE", "wednesday": "WED", "thursday": "THU", "friday": "FRI",
    "saturday": "SAT", "sunday": "SUN", "today": "TODAY", "tonight": "TODAY", "tomorrow": "TOMORROW",
}
_DAY_RE = re.compile(
    r"\b(?:(next|this)\s+)?(monday|tuesday|wednesday|thursday|friday|saturday|sunday|today|tonight|tomorrow)\b"
    r"|\b(next week)\b",
    re.IGNORECASE,
)
_TIME = r"(?:(\d{1,2})(?::(\d{2}))?\s*(am|pm|a\.m\.|p\.m\.)|(\d{1,2}):(\d{2})|(noon|midday|midnight))"
_TIME_BEFORE = re.compile(_TIME + r"\s*(?:on\s+|this\s+)?$", re.IGNORECASE)
_TIME_AFTER = re.compile(r"^\s*(?:,\s*)?(?:at|by|before|around)?\s*" + _TIME, re.IGNORECASE)

_OUTLINE_WORDS = re.compile(r"\b(outline|plan|first draft|draft|first part|first half)\b", re.IGNORECASE)
_FINAL_WORDS = re.compile(r"\b(full report|final report|whole report|complete report|report|rest|everything|"
                          r"whole thing|final version|project|it)\b", re.IGNORECASE)
_FULL_PLAN = re.compile(r"\b(whole|full|complete|entire) (report|thing|project)\b|\beverything\b", re.IGNORECASE)

_SAFE_NEG = re.compile(r"\b(no later than|not later than|no problem|not a problem)\b", re.IGNORECASE)
_NEG = re.compile(r"\b(not|never|cannot|unable|no way)\b|n't\b|\bcan not\b", re.IGNORECASE)
_QUOTED = re.compile(r"\b(said|says|told me|suggested)\b", re.IGNORECASE)
_COND = re.compile(r"\b(if|unless)\b", re.IGNORECASE)

_YES = re.compile(
    r"^\s*(yes|yeah|yep|yup|sure|ok|okay|alright|all right|that works|sounds good|deal|agreed|correct|"
    r"that's right|that is right|exactly|perfect|great|fine|i agree|i confirm)\b",
    re.IGNORECASE,
)
_NO = re.compile(r"^\s*(no|nope|not really)\b|\bthat doesn't work\b", re.IGNORECASE)


@dataclass
class Mention:
    code: str        # "FRI 18:00", "SUN", "NEXT WED", "NEXT MON"
    quote: str       # exact words from the text
    start: int
    end: int


def _time_code(m: re.Match) -> str:
    g = m.groups()
    if g[5]:
        word = g[5].lower()
        return "00:00" if word == "midnight" else "12:00"
    if g[3] is not None:
        return f"{int(g[3]):02d}:{g[4]}"
    h = int(g[0])
    mi = int(g[1] or 0)
    ampm = g[2].lower().replace(".", "")
    if ampm == "pm" and h != 12:
        h += 12
    if ampm == "am" and h == 12:
        h = 0
    return f"{h:02d}:{mi:02d}"


def find_dates(text: str) -> list[Mention]:
    out = []
    for m in _DAY_RE.finditer(text):
        start, end = m.start(), m.end()
        if re.search(r"\b(since|last|from)\s+$", text[max(0, start - 8):start], re.IGNORECASE):
            continue  # "sick since Monday" is background, not a proposed time
        if m.group(3):  # "next week"
            out.append(Mention("NEXT MON", text[start:end], start, end))
            continue
        nxt = (m.group(1) or "").lower() == "next"
        code = ("NEXT " if nxt else "") + _DAY_WORDS[m.group(2).lower()]
        before = _TIME_BEFORE.search(text[max(0, start - 25):start])
        after = _TIME_AFTER.match(text[end:end + 25])
        if before:
            code += " " + _time_code(before)
            start = max(0, start - 25) + before.start()
        elif after:
            code += " " + _time_code(after)
            end = end + after.end()
        out.append(Mention(code, text[start:end], start, end))
    return out


def _sentence_bounds(text: str, pos: int) -> tuple[int, int]:
    s = max(text.rfind(c, 0, pos) for c in ".!?\n") + 1
    ends = [i for i in (text.find(c, pos) for c in ".!?\n") if i != -1]
    e = min(ends) + 1 if ends else len(text)
    return s, e


def _clause(text: str, pos: int) -> str:
    s, e = _sentence_bounds(text, pos)
    sent = text[s:e]
    rel = pos - s
    cuts = [m.end() for m in re.finditer(r",|;|\bbut\b|\band\b", sent) if m.end() <= rel]
    left = max(cuts) if cuts else 0
    rights = [m.start() for m in re.finditer(r",|;|\bbut\b|\band\b", sent) if m.start() > rel]
    right = min(rights) if rights else len(sent)
    return sent[left:right]


def _field_for(text: str, m: Mention) -> str:
    """Outline or full report? Look at the words before the date in the same clause."""
    clause = _clause(text, m.start)
    idx = clause.lower().find(m.quote.lower().split()[-1])
    window = clause[:idx] if idx >= 0 else clause
    last_outline = max((x.end() for x in _OUTLINE_WORDS.finditer(window)), default=-1)
    last_final = max((x.end() for x in _FINAL_WORDS.finditer(window)), default=-1)
    return "outline_due" if last_outline > last_final else "final_due"


def _mode(text: str, m: Mention) -> str:
    s, e = _sentence_bounds(text, m.start)
    sent = text[s:e]
    before = text[:m.start]
    if before.count('"') % 2 == 1 or _QUOTED.search(sent):
        return "quoted"
    if _COND.search(sent):
        return "conditional"
    if sent.strip().endswith("?"):
        return "question"
    return "assert"


def _polarity(text: str, m: Mention) -> str:
    clause = _SAFE_NEG.sub(" ", _clause(text, m.start))
    return "negate" if _NEG.search(clause) else "affirm"


def date_facts(text: str, source: str = "player") -> list[dict]:
    facts = []
    for m in find_dates(text):
        facts.append({
            "field": _field_for(text, m),
            "value": m.code,
            "quote": m.quote,
            "source": source,
            "polarity": _polarity(text, m),
            "mode": _mode(text, m),
        })
    return facts


# ---------------------------------------------------------------- feedback


def mock_feedback(draft: str) -> dict:
    """Keyword stand-in for the whole-draft check. It is NOT a language judge: it only recognises a
    few fixed patterns so the flow can be played and tested without an API key."""
    items = []
    # Deterministic acceptance examples only; this mock is not a general English checker.
    for kind, bad, good, reason in [
        ("spelling", "recieve", "receive", "The spelling is 'receive'."),
        ("punctuation", "report,,", "report,", "Use one comma here."),
        ("capitalization", "i am", "I am", "Capitalize the pronoun 'I'."),
    ]:
        if bad in draft:
            items.append({"type": kind, "judgement": "clear_error", "serious": False,
                          "quote": bad, "suggestion": good, "reason": reason})
    m = re.search(r"\bI (has|is)\b(?: \w+)?", draft)
    if m:
        fixed = m.group(0).replace(" has", " have", 1).replace(" is", " am", 1)
        items.append({"type": "grammar", "judgement": "clear_error", "serious": True, "quote": m.group(0),
                      "suggestion": fixed, "reason": "Subject–verb agreement: after “I”, use “have” / “am”."})
    m = re.search(r"\b(?:can't|cannot|can not) finish(?!(?: it| the report)? by\b)(?: it| the report)?\b", draft, re.IGNORECASE)
    if m:
        items.append({"type": "task_info", "judgement": "expression_issue", "serious": False, "quote": m.group(0),
                      "suggestion": m.group(0) + " by Friday",
                      "reason": "Say which deadline you mean, so Prof. Chen knows exactly what is at risk."})
    m = re.search(r"\bI want to ask for more time\b|\bI want more time\b|\bCan I [^.?!\n]*\?", draft, re.IGNORECASE)
    if m:
        items.append({"type": "politeness", "judgement": "optional", "serious": False, "quote": m.group(0),
                      "suggestion": "Would it be possible to …?",
                      "reason": "A more polite way to ask a lecturer for a favour."})
    if not any(i["type"] == "grammar" for i in items):
        m = re.search(r"\bwould it be possible to\b", draft, re.IGNORECASE)
        if m:
            items.append({"type": "positive", "judgement": "optional", "serious": False, "quote": m.group(0),
                          "suggestion": "", "reason": "A polite, clear request — exactly right for a lecturer."})
    return {"items": items}


def mock_feedback_review(items: list[dict]) -> dict:
    """The mock second reader agrees with every point. Tests replace it to exercise disagreement."""
    return {"reviews": [{"id": i["id"], "verdict": "agree", "note": ""} for i in items]}


def mock_recheck(old_text: str, new_text: str, items: list[dict]) -> dict:
    """Re-apply the same fixed patterns to the revised draft; no judgement beyond those patterns."""
    import difflib
    again = mock_feedback(new_text)["items"]
    sentences = [p.strip() for p in re.split(r"(?<=[.!?])\s+|\n+", new_text) if p.strip()]
    results = []
    for item in items:
        same = next((x for x in again if x["type"] == item["type"] and x["reason"] == item["reason"]), None)
        if same:
            results.append({"id": item["id"], "status": "still_present", "current_quote": same["quote"],
                            "note": "The same pattern is still in the revised draft."})
            continue
        old_sentence = next((p.strip() for p in re.split(r"(?<=[.!?])\s+|\n+", old_text)
                             if item["quote"].lower() in p.lower()), item["quote"])
        near = difflib.get_close_matches(old_sentence, sentences, n=1, cutoff=0.3)
        results.append({"id": item["id"], "status": "resolved", "current_quote": near[0] if near else "",
                        "note": "This pattern is no longer in the revised draft."})
    return {"results": results}


# ---------------------------------------------------------------- summary & opening

_GREETING = re.compile(r"^\s*(dear|hi|hello|thanks|thank you|best|regards|kind regards|sincerely|yours|alex)\b[^.?!]*$",
                       re.IGNORECASE)


def _sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+|\n+", text)
    return [p.strip() for p in parts if p.strip() and not _GREETING.match(p.strip())]


def mock_summary(email: str) -> dict:
    sents = _sentences(email)
    fields = []
    used: set[str] = set()

    def pick(name: str, pattern: str, extra=None) -> None:
        for s in sents:
            if s in used:
                continue
            if re.search(pattern, s, re.IGNORECASE) and (extra is None or extra(s)):
                fields.append({"field": name, "value": s, "quote": s})
                used.add(s)
                return

    pick("situation", r"\b(sick|ill|unwell|flu|fever|haven't been able|can't finish|cannot finish|behind)\b")
    pick("proposal", r".", lambda s: any(f["polarity"] == "affirm" and f["mode"] != "quoted" for f in date_facts(s)))
    pick("request", r"\b(would it be possible|could i|can i|may i|ask for|extension|more time|extend|later)\b")
    return {"fields": fields}


def mock_opening(summary_fields: list[dict], name: str = "Alex") -> dict:
    by = {f["field"]: f["quote"] for f in summary_fields}
    if "proposal" in by:
        return {"reply": f'Ah, {name}, come in. In your email, you wrote: "{by["proposal"]}" Is that right?'}
    if "request" in by:
        return {"reply": f'Ah, {name}, come in. In your email, you wrote: "{by["request"]}" What exactly do you have in mind?'}
    return {"reply": f"Ah, {name}, come in. In your email, you mentioned the project report. What exactly are you asking for?"}


# ---------------------------------------------------------------- NPC turn

_TASK_WORDS = re.compile(
    r"\b(report|project|deadline|outline|submit|hand|send|finish|sick|ill|time|extension|late|draft|"
    r"monday|tuesday|wednesday|thursday|friday|saturday|sunday|week|email|plan|yes|no|ok|okay|sorry)\b",
    re.IGNORECASE,
)
_WARM = re.compile(r"\b(please|thank|thanks|sorry|would it be possible|could i|unfortunately|appreciate|"
                   r"i'll make sure|i will make sure)\b", re.IGNORECASE)
_REASON = re.compile(r"\b(sick|ill|unwell|flu|fever|hospital|doctor)\b", re.IGNORECASE)


def mock_npc_turn(player_text: str, npc_previous: str) -> dict:
    facts = date_facts(player_text, "player")
    yes = _YES.search(player_text)
    no = _NO.search(player_text)
    if yes:
        facts.append({"field": "confirm", "value": None, "quote": yes.group(0).strip(), "source": "player",
                      "polarity": "affirm", "mode": "assert"})
        # "Yes, that works." in answer to a question that named a time: the time comes from that question
        if not any(f["field"] != "confirm" for f in facts) and npc_previous.strip().endswith("?"):
            facts.extend(f for f in date_facts(npc_previous, "npc_previous") if f["polarity"] == "affirm")
            for f in facts:
                if f["source"] == "npc_previous":
                    f["mode"] = "assert"
    elif no:
        facts.append({"field": "confirm", "value": None, "quote": no.group(0).strip(), "source": "player",
                      "polarity": "negate", "mode": "assert"})
    if _FULL_PLAN.search(player_text) and not _OUTLINE_WORDS.search(player_text) and facts:
        m = _FULL_PLAN.search(player_text)
        facts.append({"field": "submission_plan", "value": "full", "quote": m.group(0), "source": "player",
                      "polarity": "affirm", "mode": "assert"})

    mood = "warm" if _WARM.search(player_text) else "neutral"
    dates = [f for f in facts if f["field"] in ("outline_due", "final_due")]
    off_topic = False
    needs_clar = False
    if any(f["field"] == "outline_due" and f["polarity"] == "affirm" for f in dates) and not any(
            f["field"] == "final_due" and f["polarity"] == "affirm" for f in dates):
        reply = "An outline by then would help. And when would I get the full report?"
        needs_clar = True
    elif dates and all(f["polarity"] == "negate" for f in dates):
        reply = "I see. So what could you manage instead?"
        needs_clar = True
    elif dates:
        reply = "Let me think about that."
    elif yes or no:
        reply = "Alright. So what exactly are you proposing for the report?"
        needs_clar = True
    elif len(re.findall(r"[A-Za-z]{3,}", player_text)) < 3:
        # hesitation ("Um... I... maybe...") is not off-topic: give the learner room
        reply = "Take your time. What could you realistically manage?"
        needs_clar = True
    elif not _TASK_WORDS.search(player_text):
        reply = "Ha — maybe another time. Let's sort out your report first."
        off_topic = True
    elif _REASON.search(player_text):
        reply = "I'm sorry to hear that. How far along are you, and what are you proposing?"
        mood = "concerned"
        needs_clar = True
    else:
        reply = "I see. What exactly would you like to propose?"
        needs_clar = True
    return {"reply": reply, "mood": mood, "candidate_facts": facts,
            "needs_clarification": needs_clar, "off_topic": off_topic}


# ---------------------------------------------------------------- end-of-chapter language notes


def mock_turn_feedback(lines: list[str]) -> dict:
    items = []
    for line in lines:
        m = re.search(r"\bI (has|is)\b(?: \w+)?", line, re.IGNORECASE)
        if m:
            fixed = re.sub(r"\b(has|is)\b", lambda x: "have" if x.group(1).lower() == "has" else "am", m.group(0), count=1)
            items.append({"category": "error", "quote": m.group(0), "suggestion": fixed,
                          "reason": "After “I”, use “have” / “am”. (主谓一致)"})
            break
    for line in lines:
        m = re.search(r"\bi want to (?:next|this) \w+(?: to)?(?: \w+){0,3}", line, re.IGNORECASE)
        if m:
            items.append({"category": "error", "quote": m.group(0), "suggestion": "I'd like to … by next Monday",
                          "reason": "Put the verb straight after “want to”, and the time at the end. (语序)"})
            break
    if len(items) < 2:
        for line in lines:
            m = re.search(r"\bI want\b", line)
            if m:
                items.append({"category": "upgrade", "quote": m.group(0), "suggestion": "I'd like",
                              "reason": "“I'd like” sounds more polite to a lecturer."})
                break
    if not any(i["category"] == "error" for i in items):
        for line in lines:
            m = re.search(r"\bI could [^.?!]*\bby\b[^.?!]*", line, re.IGNORECASE)
            if m:
                items.append({"category": "positive", "quote": m.group(0), "suggestion": "",
                              "reason": "A clear, concrete offer — exactly what Prof. Chen wanted."})
                break
    return {"items": items[:2]}
