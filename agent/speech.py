"""
agent/speech.py
---------------
Builds a plain-text, spoken-style attendance message from an AttendanceSummary.
The output is a single clean string suitable for TTS (ElevenLabs or any other).
No Rich markup, no symbols, no percentages written as "85%", no bullet points.
"""
from __future__ import annotations

import random
from typing import List

from agent.models import AttendanceSummary, SubjectAttendance


# ─────────────────────────────────────────────────────────────────────────────
# Number → words helpers
# ─────────────────────────────────────────────────────────────────────────────

_ONES = [
    "", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine",
    "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen",
    "seventeen", "eighteen", "nineteen",
]
_TENS = [
    "", "", "twenty", "thirty", "forty", "fifty",
    "sixty", "seventy", "eighty", "ninety",
]


def _int_to_words(n: int) -> str:
    """Convert an integer 0-100 to English words."""
    if n == 100:
        return "one hundred"
    if n < 0 or n > 100:
        return str(n)
    if n < 20:
        return _ONES[n]
    tens, ones = divmod(n, 10)
    if ones == 0:
        return _TENS[tens]
    return f"{_TENS[tens]} {_ONES[ones]}"


def pct_to_words(value: float) -> str:
    """
    Convert a percentage float to spoken English, e.g. 85.56 → "eighty six percent".
    Rounds to the nearest whole number.
    """
    rounded = round(value)
    return f"{_int_to_words(rounded)} percent"


# ─────────────────────────────────────────────────────────────────────────────
# Sentence fragments
# ─────────────────────────────────────────────────────────────────────────────

_GOOD_OPENERS = [
    "Hey, good news!",
    "Alright, you're doing well!",
    "Good news on the attendance front.",
    "Okay, things look pretty good.",
]

_WARN_OPENERS = [
    "Heads up.",
    "Okay, so there are a couple of things to watch.",
    "Alright, most things are fine, but listen up.",
    "Quick heads up about your attendance.",
]

_COURSE_GOOD = [
    "{name} is at {pct}, so you're fine there.",
    "In {name} you're at {pct}, which is solid.",
    "{name} is at {pct}, that's safe.",
    "You're at {pct} in {name}, no issues there.",
    "{name} sits at {pct}, looking good.",
]

_COURSE_BAD = [
    "{name} is at {pct}, so that one needs attention.",
    "In {name} you're only at {pct}, which is below the limit.",
    "{name} is at {pct} — you'll want to work on that one.",
    "Watch out for {name}, you're at {pct} there.",
]


def _course_sentence(name: str, pct_words: str, below: bool) -> str:
    template = random.choice(_COURSE_BAD if below else _COURSE_GOOD)
    return template.format(name=name, pct=pct_words)


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def build_attendance_message(summary: AttendanceSummary) -> str:
    """
    Returns one plain-text string written the way a person would speak.
    Safe to pass directly to a TTS engine — no markup, no symbols, no emojis.
    """
    # Filter out garbage subjects the scraper sometimes picks up
    _BAD_TOKENS = ("total:", "present:", "absent:", "total lectures", "activities")
    subjects: List[SubjectAttendance] = [
        s for s in summary.subjects
        if s.name
        and not any(tok in s.name.lower() for tok in _BAD_TOKENS)
        and s.numeric_percentage > 0
    ]

    overall = summary.numeric_overall_percentage
    overall_words = pct_to_words(overall)

    low  = [s for s in subjects if s.numeric_percentage < 75.0]
    good = [s for s in subjects if s.numeric_percentage >= 75.0]

    sentences: List[str] = []

    # ── Opener ────────────────────────────────────────────────────────────────
    opener = random.choice(_GOOD_OPENERS if not low else _WARN_OPENERS)
    sentences.append(opener)

    # ── Overall line ──────────────────────────────────────────────────────────
    sentences.append(f"Your overall attendance is {overall_words}.")

    # ── Per-course lines ──────────────────────────────────────────────────────
    if subjects:
        for s in subjects:
            sentences.append(
                _course_sentence(
                    name=s.name,
                    pct_words=pct_to_words(s.numeric_percentage),
                    below=s.numeric_percentage < 75.0,
                )
            )
    else:
        sentences.append("I couldn't read the individual courses this time.")

    # ── Closing line ──────────────────────────────────────────────────────────
    if not low:
        sentences.append("You're above seventy five percent in every course, so you're all good.")
    else:
        low_names = ", ".join(s.name for s in low)
        if len(low) == 1:
            sentences.append(
                f"{low[0].name} is the one you need to focus on to get back above seventy five percent."
            )
        else:
            sentences.append(
                f"The courses that need attention are {low_names}."
            )

    return " ".join(sentences)
