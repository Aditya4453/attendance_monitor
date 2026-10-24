"""
agent/scraper.py
----------------
Extracts attendance data directly from the live Playwright page DOM.

The TCS iON period-wise attendance page renders cards like:

  ┌──────────────────────────────────────────────────┐
  │  Computer Networks                    [CS1140]   │
  │   85.29% (donut)   Activities (2)               │
  │  Total Lectures  34  LECTURE   83%              │
  │  Present         29  PRACTICAL 100%             │
  │  Absent           5                              │
  └──────────────────────────────────────────────────┘

Header bar:
  Total Lectures 180 | Present 154 | Absent 26 | ██ 85.56%

Strategy: inject JS into every frame until we find one that has
subject cards. Parse the cards with explicit DOM traversal rather
than regex on innerText (which concatenates everything into garbage).
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

from loguru import logger
from playwright.sync_api import Page

from agent.models import AttendanceSummary, SubjectAttendance


# ─────────────────────────────────────────────────────────────────────────────
# JS — injected into each frame until it returns useful data
# ─────────────────────────────────────────────────────────────────────────────

_SCRAPE_JS = r"""
() => {
    // ── tiny helpers ─────────────────────────────────────────────────────────
    function num(el) {
        if (!el) return null;
        const t = el.textContent.replace(/,/g, '').trim();
        const m = t.match(/[-+]?\d*\.?\d+/);
        return m ? parseFloat(m[0]) : null;
    }
    function txt(el) {
        return el ? el.textContent.trim() : '';
    }

    // ── HEADER: Total Lectures / Present / Absent / Overall % ────────────────
    // The header bar has three coloured stat boxes and a progress bar label.
    // We read each box's number via the sibling/child text, not class names
    // (class names vary across TCS iON deployments).
    let totalClasses = 0, totalPresent = 0, totalAbsent = 0, overallPct = 0;

    // Walk every element; if its text is a plain integer preceded by a label
    // like "Total Lectures", "Present", "Absent" nearby, capture it.
    const allEls = Array.from(document.querySelectorAll('*'));

    // Find elements whose *direct* text content is a standalone integer
    // and whose nearby label contains the right keyword.
    allEls.forEach(el => {
        const children = Array.from(el.childNodes);
        const directText = children
            .filter(n => n.nodeType === 3)
            .map(n => n.textContent.trim())
            .join(' ').trim();

        // Look for a label element (sibling or parent text) + a number element
        const parentText = txt(el.parentElement).toLowerCase();
        const n = parseFloat(directText.replace(/,/g, ''));
        if (isNaN(n) || directText === '') return;

        if (!totalClasses && (parentText.includes('total') && parentText.includes('lecture'))) {
            totalClasses = n;
        }
        if (!totalPresent && parentText.includes('present') && !parentText.includes('absent')) {
            totalPresent = n;
        }
        if (!totalAbsent && parentText.includes('absent')) {
            totalAbsent = n;
        }
    });

    // Fallback: scan for stat boxes by looking for adjacent label+number pairs
    if (!totalClasses || !totalPresent) {
        // Many TCS iON builds put label and value as siblings inside a flex box
        const statContainers = document.querySelectorAll(
            'div, span, td, li, p'
        );
        statContainers.forEach(el => {
            const t = txt(el).toLowerCase();
            // Must be short (a stat box, not a paragraph)
            if (t.length > 60) return;
            const n = parseFloat(t.replace(/[^0-9.]/g, ''));
            if (isNaN(n)) return;
            if (!totalClasses && t.includes('total') && t.includes('lectur')) totalClasses = n;
            if (!totalPresent && t.includes('present') && !t.includes('absent')) totalPresent = n;
            if (!totalAbsent  && t.includes('absent')) totalAbsent = n;
        });
    }

    // Overall percentage: look for "85.56%" standalone text near a progress bar
    // or in a large font element in the header area
    const pctEls = document.querySelectorAll('*');
    pctEls.forEach(el => {
        if (overallPct) return;
        const t = txt(el);
        if (t.length > 12) return;   // skip containers with lots of text
        const m = t.match(/^(\d{1,3}(?:\.\d+)?)\s*%$/);
        if (m) {
            const v = parseFloat(m[1]);
            if (v > 0 && v <= 100) overallPct = v;
        }
    });

    // ── SUBJECT CARDS ─────────────────────────────────────────────────────────
    // Each card is an independent block with:
    //   • A heading containing the subject name
    //   • A circular % indicator  (we parse the text inside the circle)
    //   • A mini-table with rows: "Total Lectures N", "Present N", "Absent N"
    //
    // We identify cards by looking for elements that contain ALL of:
    //   "Total Lectures", "Present", "Absent" within the same ancestor.

    const subjects = [];

    // Find the deepest ancestor that contains all three row labels.
    // That ancestor is the card container.
    function findCards() {
        const candidates = Array.from(document.querySelectorAll('div, td, li, article, section'));
        return candidates.filter(el => {
            const t = el.innerText || '';
            return (
                t.includes('Total Lectures') &&
                t.includes('Present') &&
                t.includes('Absent') &&
                // Must have a percentage (the donut label)
                /\d{1,3}(?:\.\d+)?\s*%/.test(t) &&
                // Not the whole page — should be a card-sized element
                t.length < 800
            );
        });
    }

    const cards = findCards();

    cards.forEach(card => {
        const cardText = card.innerText || '';

        // ── Subject name ──────────────────────────────────────────────────────
        // The name is the first non-empty, non-numeric, non-label line.
        // Course code badges like "CS1140" appear as siblings — skip them.
        let name = '';
        const lines = cardText.split('\n').map(l => l.trim()).filter(Boolean);
        for (const line of lines) {
            if (/^\d/.test(line)) continue;                          // starts with digit
            if (/^(total|present|absent|activities|lecture|practical|tutorial|view|p$)/i.test(line)) continue;
            if (/^[A-Z]{2}\d{4}$/.test(line)) continue;             // course code like CS1140
            if (line.length < 3) continue;
            if (/^\d{1,3}(\.\d+)?%?\s*(present)?$/i.test(line)) continue;  // "85.29%" or "81.25% Present"
            name = line;
            break;
        }
        if (!name) return;

        // ── Percentage ────────────────────────────────────────────────────────
        // The donut shows e.g. "81.25%\nPresent". Grab the first % value in the card.
        let pct = 0;
        const pctMatch = cardText.match(/(\d{1,3}(?:\.\d+)?)\s*%/);
        if (pctMatch) pct = parseFloat(pctMatch[1]);

        // ── Total / Present / Absent (the three rows in the card) ─────────────
        // Each row looks like:  "Total Lectures\n16"  or  "Total Lectures    16"
        // Use regex on the card text — but only capture the FIRST number after each label.
        let subTotal = 0, subPresent = 0, subAbsent = 0;

        const tlMatch = cardText.match(/Total\s+Lectures[\s\S]*?(\d+)/i);
        if (tlMatch) subTotal = parseInt(tlMatch[1], 10);

        // "Present" appears in "81.25% Present" (donut label) AND "Present  13"
        // We want the standalone row, so look for Present followed by a small integer
        const prMatch = cardText.match(/\bPresent\b\s*[\n\r\t ]+(\d+)\b/i);
        if (prMatch) subPresent = parseInt(prMatch[1], 10);

        const abMatch = cardText.match(/\bAbsent\b\s*[\n\r\t ]+(\d+)\b/i);
        if (abMatch) subAbsent = parseInt(abMatch[1], 10);

        subjects.push({
            name:      name,
            percentage: pct,
            attended:  subPresent,
            total:     subTotal,
        });
    });

    return {
        overall_percentage: overallPct,
        total_classes:      totalClasses,
        attended:           totalPresent,
        absent:             totalAbsent,
        subjects:           subjects,
    };
}
"""


# ─────────────────────────────────────────────────────────────────────────────
# Python-side helpers
# ─────────────────────────────────────────────────────────────────────────────

def _clean_subjects(raw_subjects: List[Dict]) -> List[SubjectAttendance]:
    """Deduplicate and validate subject list from DOM scrape."""
    seen: set = set()
    result: List[SubjectAttendance] = []
    for s in raw_subjects:
        name = str(s.get("name", "")).strip()
        if not name or name.lower() in seen:
            continue
        seen.add(name.lower())
        try:
            result.append(SubjectAttendance(
                name=name,
                percentage=float(s.get("percentage", 0) or 0),
                attended=int(s.get("attended", 0) or 0),
                total=int(s.get("total", 0) or 0),
            ))
        except Exception as e:
            logger.debug(f"Skipping subject '{name}': {e}")
    return result


def _build_summary(raw: Dict, subjects: List[SubjectAttendance]) -> AttendanceSummary:
    """Build an AttendanceSummary from scraped raw dict + cleaned subjects."""
    overall  = float(raw.get("overall_percentage", 0) or 0)
    total    = int(raw.get("total_classes", 0) or 0)
    attended = int(raw.get("attended", 0) or 0)
    absent   = int(raw.get("absent", 0) or 0)

    if absent == 0 and total > 0 and attended > 0:
        absent = total - attended

    low = [s.name for s in subjects if 0 < s.numeric_percentage < 75.0]

    if overall >= 75:
        one_liner = f"Overall attendance {overall:.1f}% — looking good!"
    else:
        one_liner = f"Overall attendance {overall:.1f}% — below 75%, action needed."

    return AttendanceSummary(
        overall_percentage=overall,
        total_classes=total,
        attended=attended,
        absent=absent,
        subjects=subjects,
        low_attendance_subjects=low,
        one_line_summary=one_liner,
    )


def _is_useful(raw: Optional[Dict]) -> bool:
    """Return True if the scrape result has at least an overall % or ≥1 subject."""
    if not raw:
        return False
    overall  = float(raw.get("overall_percentage", 0) or 0)
    subjects = raw.get("subjects", [])
    # Reject if subject names look like garbage (contain digits / concatenated text)
    clean_subs = [
        s for s in subjects
        if isinstance(s.get("name"), str)
        and len(s["name"]) >= 4
        and not any(c.isdigit() for c in s["name"][:4])
    ]
    return overall > 0 or len(clean_subs) > 0


# ─────────────────────────────────────────────────────────────────────────────
# Public entry point
# ─────────────────────────────────────────────────────────────────────────────

def scrape_attendance_from_page(page: Page) -> Optional[AttendanceSummary]:
    """
    Scrape attendance data from the live Playwright page by injecting JS
    into every frame until useful data is found.

    Returns AttendanceSummary on success, None if nothing usable found.
    """
    # Try frames from deepest (most likely to have the cards) to shallowest
    frames = page.frames
    logger.info(f"Scraper: {len(frames)} frame(s) available.")

    # Sort: child frames before main frame (content is usually in the iframe)
    ordered = [f for f in frames if f != page.main_frame] + [page.main_frame]

    for frame in ordered:
        label = frame.url or "about:blank"
        if label == "about:blank":
            continue
        try:
            logger.info(f"Scraper injecting JS into: {label[:80]}")
            raw = frame.evaluate(_SCRAPE_JS)

            if not _is_useful(raw):
                logger.debug(f"  → No useful data in this frame.")
                continue

            subjects = _clean_subjects(raw.get("subjects", []))
            overall  = float(raw.get("overall_percentage", 0) or 0)

            logger.info(
                f"  → Scrape OK: overall={overall}%, "
                f"subjects={len(subjects)}, "
                f"total={raw.get('total_classes')}, "
                f"present={raw.get('attended')}, "
                f"absent={raw.get('absent')}"
            )

            summary = _build_summary(raw, subjects)
            return summary

        except Exception as e:
            logger.debug(f"  → Frame error: {e}")
            continue

    logger.warning("Scraper: no usable attendance data found in any frame.")
    return None
