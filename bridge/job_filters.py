#!/usr/bin/env python3
"""
Discovery filters and free role extraction.

Two jobs:
1. Drop roles the candidate cannot or will not take (UAE-national-only postings
   and roles that demand a language other than English/Arabic).
2. Turn a raw job description into a short "what is this role" brief without
   spending an AI call — the paid tailoring only happens after approval.
"""

import re
from typing import Any, Dict, List, Optional

# ── Exclusion rules ──

UAE_NATIONAL_PATTERNS = [
    r"\buae nationals?\b(?!\s+(?:holidays?|days?)\b)",
    r"\bemirati nationals?\b(?!\s+days?\b)",
    r"\bemirati only\b",
    r"\bemiratisation\b",
    r"\bemiratization\b",
    r"\bnationals? only\b",
    r"\bnationals of the uae\b",
    r"مواطن",
]

# "UAE nationals welcome but not required" must not disqualify a posting.
_NON_RESTRICTIVE = re.compile(
    r"\b(not required|not mandatory|not essential|no requirement|welcome|"
    r"preferred|advantage|a plus|nice to have|desirable|asset|bonus|"
    r"open to all|regardless)\b",
    re.IGNORECASE,
)

OTHER_LANGUAGES = [
    "russian", "french", "german", "spanish", "italian", "mandarin", "cantonese",
    "hindi", "urdu", "tagalog", "malayalam", "tamil", "turkish", "dutch",
    "portuguese", "japanese", "korean", "polish", "romanian", "swahili", "farsi",
    "persian", "bengali", "sinhala", "nepali", "ukrainian", "hebrew", "greek",
    "czech", "hungarian", "thai", "vietnamese", "indonesian", "malay", "filipino",
    "punjabi", "gujarati", "amharic", "somali", "kurdish", "danish", "swedish",
    "norwegian", "finnish", "bulgarian", "serbian", "croatian", "slovak",
    "lithuanian", "latvian", "estonian", "azerbaijani", "kazakh", "uzbek",
    "burmese", "khmer", "mongolian", "armenian", "georgian",
]

_LANG_ALTERNATION = "|".join(sorted(OTHER_LANGUAGES, key=len, reverse=True))

# A language only disqualifies when the sentence actually demands it.
REQUIREMENT_CUES = re.compile(
    r"\b(required|requires?|requiring|fluent|fluency|proficien\w*|speaking|speaker|"
    r"native|bilingual|command of|knowledge of|written and spoken|mandatory|must)\b",
    re.IGNORECASE,
)

_LANG_IN_SENTENCE = re.compile(rf"\b({_LANG_ALTERNATION})\b", re.IGNORECASE)
_LANG_IN_TITLE = re.compile(
    rf"\b({_LANG_ALTERNATION})\b.*\b(speaking|speaker|bilingual|language)\b|"
    rf"\b(speaking|speaker|bilingual|language)\b.*\b({_LANG_ALTERNATION})\b",
    re.IGNORECASE,
)


def normalize(text: Any) -> str:
    return " ".join(str(text or "").split())


def _clauses(text: str) -> List[str]:
    return [c.strip() for c in re.split(r"[,;()]| - | – | — ", text) if c.strip()]


def exclusion_reason(job: Dict[str, Any]) -> Optional[str]:
    """Return a human-readable reason if the role should be skipped, else None."""
    title = normalize(job.get("job_title") or job.get("title"))
    body = normalize(job.get("description") or job.get("raw_description") or "")
    text = normalize(f"{title} {body}")

    sentence_has_restriction = False
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        lowered = sentence.lower()
        if not any(re.search(p, lowered) for p in UAE_NATIONAL_PATTERNS):
            continue
        if _NON_RESTRICTIVE.search(sentence):
            continue
        sentence_has_restriction = True
        break
    if sentence_has_restriction:
        return "UAE national / Emirati requirement"

    if title and _LANG_IN_TITLE.search(title) and not _NON_RESTRICTIVE.search(title):
        match = _LANG_IN_SENTENCE.search(title)
        return f"requires {match.group(1).title()}" if match else "requires another language"

    for sentence in re.split(r"(?<=[.!?])\s+", body):
        for clause in _clauses(sentence):
            if not REQUIREMENT_CUES.search(clause):
                continue
            if _NON_RESTRICTIVE.search(clause):
                continue
            match = _LANG_IN_SENTENCE.search(clause)
            if match:
                return f"requires {match.group(1).title()}"

    return None


# ── Role extraction (no AI) ──

_ACTION_CUES = re.compile(
    r"\b(manage|managed|managing|lead|leading|support|supporting|coordinate|coordination|"
    r"handle|handling|build|building|ensure|ensuring|develop|developing|drive|driving|"
    r"own|owning|deliver|delivering|maintain|maintaining|resolve|resolving|monitor|"
    r"monitoring|prepare|preparing|provide|providing|create|creating|design|designing|"
    r"implement|implementing|oversee|overseeing|review|reviewing|track|tracking|"
    r"process|processing|respond|responding|liaise|collaborate|analy[sz]e|report)\w*\b",
    re.IGNORECASE,
)

_REQ_CUES = re.compile(
    r"\b(experience|degree|qualified|qualification|knowledge|proficien\w*|fluent|"
    r"skills?|background|certification|years? of|familiar\w*|understanding|ability)\b",
    re.IGNORECASE,
)


def _sentences(text: str) -> List[str]:
    parts = re.split(r"(?<=[.!?])\s+|\s*[•·]\s*|\n+", text)
    out = []
    for part in parts:
        s = normalize(part).strip(" -–—")
        if 25 <= len(s) <= 240:
            out.append(s)
    return out


def _dedupe(items: List[str]) -> List[str]:
    seen, out = set(), []
    for item in items:
        key = item.lower()[:60]
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def explain_role(job: Dict[str, Any], max_items: int = 3) -> Dict[str, Any]:
    """Extractive brief: what the role does and what it asks for."""
    body = normalize(job.get("description") or job.get("raw_description") or "")
    sentences = _sentences(body)

    summary = ""
    for s in sentences[:4]:
        if len(s) >= 45:
            summary = s
            break
    if not summary and sentences:
        summary = sentences[0]

    used = {summary}
    responsibilities = _dedupe([
        s for s in sentences
        if s not in used and _ACTION_CUES.search(s) and not _REQ_CUES.search(s)
    ])[:max_items]

    requirements = _dedupe([
        s for s in sentences
        if s not in used and _REQ_CUES.search(s)
    ])[:max_items]

    return {
        "summary": summary,
        "responsibilities": responsibilities,
        "requirements": requirements,
    }
