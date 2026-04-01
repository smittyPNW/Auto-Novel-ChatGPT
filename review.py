"""
review.py — Deep manuscript review using two expert AI personas via OpenAI.

Two-Agent Architecture
======================

Agent 1 — The Literary Critic (Margaret Holloway)
--------------------------------------------------
A seasoned newspaper book critic with 25 years of experience reviewing literary
and genre fiction for publications like The Atlantic and The New York Review of
Books.  She reads for narrative pleasure, emotional resonance, and cultural
relevance.  Her output: a structured book review with a star rating (1-5,
half-star increments) that a reader could encounter in a Sunday supplement.

Agent 2 — Professor of Fiction (Dr. James Whitfield)
-----------------------------------------------------
A working novelist and MFA program director who has taught craft seminars for
30 years.  He reads manuscripts the way a developmental editor does — looking
for structural problems, pacing failures, POV drift, under-realised characters,
and prose-level habits that can be fixed.  His output: a numbered list of
specific, actionable craft notes with severity and fix-type classifications.

Why Two Agents vs One
---------------------
The original autonovel used Claude Opus in a single prompt to play both roles.
GPT-4o and o1 respond better to *committed* persona framing — where each agent
receives its own fully-formed identity and is never aware it shares a body with
a second persona.  Splitting the calls also allows independent temperature
tuning: the Critic gets mild creative latitude (temp 0.5) for vivid prose; the
Professor runs at near-zero temperature (temp 0.1) for reproducible, specific
analysis.

Usage
-----
    python review.py                     # Generate review, save JSON
    python review.py --output report.md  # Also save markdown
    python review.py --parse             # Parse latest saved review for items
    python review.py --chapter 3         # Review a single chapter only
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
import textwrap
from datetime import datetime
from pathlib import Path

from llm_client import (
    call_gpt,
    call_o1,
    DEEP_MODEL,
    JUDGE_MODEL,
    O1_CTX_WORDS,
    word_count,
    summarise_manuscript,
)


# ---------------------------------------------------------------------------
# Manuscript loading
# ---------------------------------------------------------------------------

def load_manuscript(chapters_dir: str = "chapters") -> tuple[str, int, int]:
    """
    Load all chapter markdown files in sorted order.
    Returns (full_text, word_count, chapter_count).
    """
    files = sorted(glob.glob(os.path.join(chapters_dir, "*.md")))
    if not files:
        raise FileNotFoundError(
            f"No .md chapter files found in '{chapters_dir}/'."
        )
    parts = []
    for f in files:
        parts.append(Path(f).read_text(encoding="utf-8"))
    text = "\n\n".join(parts)
    return text, word_count(text), len(files)


def load_support_docs() -> dict[str, str]:
    """Load optional planning docs to give reviewers context."""
    docs: dict[str, str] = {}
    for name in ("world.md", "characters.md", "outline.md", "voice.md"):
        p = Path(name)
        if p.exists():
            docs[name] = p.read_text(encoding="utf-8")
    return docs


# ---------------------------------------------------------------------------
# Agent 1 — The Literary Critic
# ---------------------------------------------------------------------------

CRITIC_SYSTEM = textwrap.dedent("""\
    You are Margaret Holloway, a senior literary critic with 25 years of
    experience reviewing fiction for The Atlantic, The New York Review of
    Books, and The Guardian.  You have reviewed over 400 novels across
    literary fiction, fantasy, science fiction, and historical fiction.

    Your voice is authoritative but accessible — you write for an educated
    general reader, not an academic.  You are neither sycophantic nor
    reflexively dismissive.  You give credit where it is earned and name
    specific problems when they exist.

    REVIEW FORMAT
    -------------
    Write a 600-900 word book review structured exactly as follows:

    ## [Title of the Novel] — A Review
    *[One-line tagline]*

    **★ RATING: X.X / 5.0**

    [Opening paragraph: hook the reader, establish what kind of book this is
     and what it is trying to do.  Do not summarise the plot directly.]

    [Body paragraphs (2-4): engage with specific scenes, characters, and
     prose moments.  Quote sparingly but precisely.  Discuss what works and
     what does not, grounding praise and criticism in concrete textual
     evidence.  Evaluate: narrative momentum, character complexity, emotional
     truth, world-building integration, prose style.]

    [Closing paragraph: overall verdict.  Who should read this?  How does it
     sit within its genre or tradition?  Would you recommend it?]

    RATING CALIBRATION
    ------------------
    1.0 — Fundamentally broken; unreadable.
    2.0 — Ambitious but fails on most counts.
    3.0 — Competent; does some things well but underwhelms.
    3.5 — Solid; enjoyable for the target audience despite real flaws.
    4.0 — Good; succeeds at what it sets out to do with only minor issues.
    4.5 — Very good; stands out in its genre; small but noticeable weaknesses.
    5.0 — Exceptional; rare.  Reserve for work that genuinely surprises you.

    AI-generated fiction tends to land between 2.5 and 4.0.  Score honestly.
    Do not inflate ratings.  If the prose has AI slop tells (repetitive
    sentence rhythms, explaining emotions instead of dramatising them, hedging
    phrases, clichéd imagery) note them explicitly and let them lower the score.

    IMPORTANT: End the review with this exact block (fill in the values):

    ---
    STRUCTURED_DATA:
    rating: X.X
    opening_strength: [strong|adequate|weak]
    prose_quality: [excellent|good|adequate|weak|poor]
    pacing: [excellent|good|adequate|weak|poor]
    world_building: [excellent|good|adequate|weak|poor]
    character_depth: [excellent|good|adequate|weak|poor]
    emotional_resonance: [excellent|good|adequate|weak|poor]
    ---
""")


def run_literary_critic(manuscript: str, support_docs: dict[str, str]) -> str:
    """
    Call Agent 1 (Margaret Holloway) and return her full review text.
    """
    doc_block = ""
    if support_docs:
        doc_block = "\n\nFor additional context, here are the planning documents:\n"
        for name, content in support_docs.items():
            doc_block += f"\n### {name}\n{content[:3000]}\n"  # cap each to 3k words

    user_prompt = textwrap.dedent(f"""\
        Please review the following novel manuscript.

        {doc_block}

        MANUSCRIPT:
        -----------
        {manuscript}
    """)

    # Use o1 for full manuscripts (better long-range coherence tracking),
    # fall back to gpt-4o for short/single-chapter reviews.
    use_o1 = word_count(manuscript) > 20_000 and DEEP_MODEL.startswith("o1")
    if use_o1:
        return call_o1(
            developer_prompt=CRITIC_SYSTEM,
            user=user_prompt,
            max_completion_tokens=3_000,
        )
    else:
        return call_gpt(
            system=CRITIC_SYSTEM,
            user=user_prompt,
            model=JUDGE_MODEL,
            temperature=0.5,
            max_tokens=3_000,
        )


# ---------------------------------------------------------------------------
# Agent 2 — The Professor of Fiction
# ---------------------------------------------------------------------------

PROFESSOR_SYSTEM = textwrap.dedent("""\
    You are Dr. James Whitfield, a novelist and the director of an MFA
    creative writing program at a major research university.  You have
    taught advanced fiction craft for 30 years and have served as a
    developmental editor on more than 60 published novels.

    You read manuscripts the way a skilled developmental editor does —
    looking past surface-level prose to find structural, character, pacing,
    and narrative architecture problems.  You are direct, specific, and
    unsentimental.  Vague praise and vague criticism are both useless to
    a writer trying to improve.

    CRAFT NOTE FORMAT
    -----------------
    After reading the manuscript, produce a numbered list of craft
    observations.  Each item must follow this exact format:

    N. [SEVERITY] [FIX-TYPE] — [One-sentence problem statement]
       Location: [Chapter N / Throughout / First half / etc.]
       Detail: [2-4 sentences of specific, concrete explanation.  Name
                characters, quote phrases, cite scenes.  Never be vague.]
       Fix: [1-3 sentences describing the specific revision needed.]

    SEVERITY levels:
      MAJOR    — Undermines the novel's core success; must be addressed.
      MODERATE — Noticeable weakness that diminishes the reading experience.
      MINOR    — Small issue; worth fixing in a polish pass.

    FIX-TYPE categories:
      STRUCTURAL  — Plot architecture, act structure, scene order.
      CHARACTER   — Motivation, arc, voice differentiation, consistency.
      PACING      — Scene length, tension management, info-dump mitigation.
      PROSE       — Sentence rhythm, word choice, show-don't-tell failures.
      COMPRESSION — Passages that can be cut without loss.
      ADDITION    — Missing beats, underdeveloped sections.
      CONTINUITY  — Canon errors, timeline inconsistencies, factual drift.

    CALIBRATION NOTES
    -----------------
    - AI-generated prose frequently suffers from: over-explanation of
      internal states, repetitive sentence structures (subject-verb-object
      chains), hedging adverbs, clichéd emotional beats, and a tendency
      to resolve tension too cleanly.  Flag these patterns explicitly.
    - Name the specific chapter and passage.  "The pacing drags" is not
      a note.  "Chapter 7 devotes 1,200 words to Mira's breakfast before
      the confrontation; cut to 300 words maximum" is a note.
    - Differentiate between definitive problems ("This must change") and
      qualified observations ("Consider whether…").  Use hedging language
      deliberately to signal confidence level.
    - Limit your list to the 8-15 most important items.  Prioritise by
      severity, then by how fixable the problem is.

    IMPORTANT: End your response with this exact block:

    ---
    STRUCTURED_DATA:
    total_items: N
    major_count: N
    moderate_count: N
    minor_count: N
    structural_count: N
    character_count: N
    pacing_count: N
    prose_count: N
    compression_count: N
    addition_count: N
    continuity_count: N
    ---
""")


def run_professor(manuscript: str, support_docs: dict[str, str]) -> str:
    """
    Call Agent 2 (Dr. James Whitfield) and return his craft notes.
    """
    doc_block = ""
    if support_docs:
        doc_block = "\n\nPlanning documents for reference:\n"
        for name, content in support_docs.items():
            doc_block += f"\n### {name}\n{content[:3000]}\n"

    user_prompt = textwrap.dedent(f"""\
        Please provide developmental editing notes on the following novel
        manuscript.  Be specific, actionable, and unsparing.

        {doc_block}

        MANUSCRIPT:
        -----------
        {manuscript}
    """)

    use_o1 = word_count(manuscript) > 20_000 and DEEP_MODEL.startswith("o1")
    if use_o1:
        return call_o1(
            developer_prompt=PROFESSOR_SYSTEM,
            user=user_prompt,
            max_completion_tokens=5_000,
        )
    else:
        return call_gpt(
            system=PROFESSOR_SYSTEM,
            user=user_prompt,
            model=JUDGE_MODEL,
            temperature=0.1,
            max_tokens=5_000,
        )


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

def extract_rating(critic_text: str) -> float | None:
    m = re.search(r"rating:\s*([\d.]+)", critic_text, re.IGNORECASE)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            pass
    m = re.search(r"★\s*RATING:\s*([\d.]+)", critic_text, re.IGNORECASE)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            pass
    return None


def extract_structured_data(text: str) -> dict:
    """Extract the STRUCTURED_DATA block from an agent's response."""
    m = re.search(r"STRUCTURED_DATA:\s*\n(.*?)---", text, re.DOTALL)
    if not m:
        return {}
    data: dict = {}
    for line in m.group(1).strip().splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            data[k.strip()] = v.strip()
    return data


def parse_professor_items(professor_text: str) -> list[dict]:
    """
    Extract individual craft notes from the Professor's response.
    Returns a list of dicts with keys: number, severity, fix_type, statement,
    location, detail, fix, qualified.
    """
    items: list[dict] = []
    # Match numbered items: "N. [SEVERITY] [FIX-TYPE] — ..."
    pattern = re.compile(
        r"(\d+)\.\s+(MAJOR|MODERATE|MINOR)\s+(STRUCTURAL|CHARACTER|PACING|PROSE|"
        r"COMPRESSION|ADDITION|CONTINUITY)\s+[—-]\s+(.+?)(?=\n\d+\.|STRUCTURED_DATA|$)",
        re.DOTALL | re.IGNORECASE,
    )
    for m in pattern.finditer(professor_text):
        block = m.group(0)
        location = ""
        loc_m = re.search(r"Location:\s*(.+)", block)
        if loc_m:
            location = loc_m.group(1).strip()
        detail = ""
        det_m = re.search(r"Detail:\s*(.+?)(?=Fix:|$)", block, re.DOTALL)
        if det_m:
            detail = det_m.group(1).strip()
        fix = ""
        fix_m = re.search(r"Fix:\s*(.+?)$", block, re.DOTALL)
        if fix_m:
            fix = fix_m.group(1).strip()

        statement = m.group(4).split("\n")[0].strip()
        qualified = any(
            hedge in statement.lower()
            for hedge in ("consider", "might", "perhaps", "could", "may", "whether")
        )
        items.append({
            "number":    int(m.group(1)),
            "severity":  m.group(2).upper(),
            "fix_type":  m.group(3).upper(),
            "statement": statement,
            "location":  location,
            "detail":    detail,
            "fix":       fix,
            "qualified": qualified,
        })
    return items


# ---------------------------------------------------------------------------
# Stopping condition
# ---------------------------------------------------------------------------

def should_stop_revising(rating: float | None, items: list[dict]) -> tuple[bool, str]:
    """
    Determine whether the revision loop should terminate.

    Stopping conditions (mirror the original autonovel logic):
      A. Rating >= 4.5 AND no MAJOR items remaining.
      B. Rating >= 4.0 AND >50% of items are qualified (hedged).
      C. Total items <= 2 (noise floor reached).
    """
    if rating is None:
        return False, "No rating extracted; continue revising."

    major = [i for i in items if i["severity"] == "MAJOR"]
    qualified = [i for i in items if i["qualified"]]
    total = len(items)

    if rating >= 4.5 and not major:
        return True, f"Rating {rating}/5 with no MAJOR items — revision complete."
    if rating >= 4.0 and total > 0 and len(qualified) / total > 0.5:
        return True, f"Rating {rating}/5 with >{len(qualified)}/{total} qualified items — diminishing returns."
    if total <= 2:
        return True, f"Only {total} item(s) remaining — noise floor reached."
    return False, f"Rating {rating}/5, {len(major)} major items, {total} total — continue revising."


# ---------------------------------------------------------------------------
# Main entrypoint
# ---------------------------------------------------------------------------

def generate_review(
    chapters_dir: str = "chapters",
    output_md: str | None = None,
) -> dict:
    """
    Run both agents and return the combined review dict.
    """
    print("Loading manuscript…")
    manuscript, wc, chapter_count = load_manuscript(chapters_dir)
    support_docs = load_support_docs()

    print(f"  {wc:,} words across {chapter_count} chapters loaded.")

    # If manuscript exceeds o1's context, summarise it first.
    if word_count(manuscript) > O1_CTX_WORDS:
        print(f"  Manuscript exceeds {O1_CTX_WORDS:,}-word context limit — "
              "generating summary for review…")
        manuscript = summarise_manuscript(manuscript)
        print(f"  Summary: {word_count(manuscript):,} words.")

    print("\n[Agent 1] Running Literary Critic (Margaret Holloway)…")
    critic_text = run_literary_critic(manuscript, support_docs)
    rating = extract_rating(critic_text)
    critic_data = extract_structured_data(critic_text)
    print(f"  Rating extracted: {rating}/5.0")

    print("\n[Agent 2] Running Professor of Fiction (Dr. James Whitfield)…")
    professor_text = run_professor(manuscript, support_docs)
    items = parse_professor_items(professor_text)
    professor_data = extract_structured_data(professor_text)
    print(f"  {len(items)} craft items extracted.")

    stop, reason = should_stop_revising(rating, items)
    print(f"\nRevision decision: {reason}")

    result = {
        "timestamp":       datetime.utcnow().isoformat(),
        "word_count":      wc,
        "chapter_count":   chapter_count,
        "critic_review":   critic_text,
        "critic_data":     critic_data,
        "rating":          rating,
        "professor_notes": professor_text,
        "professor_data":  professor_data,
        "craft_items":     items,
        "stop_revising":   stop,
        "stop_reason":     reason,
    }

    # Save JSON
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    json_path = f"reviews/review_{ts}.json"
    os.makedirs("reviews", exist_ok=True)
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    print(f"\nReview saved → {json_path}")

    # Optionally save markdown
    if output_md:
        md = _format_markdown(result)
        Path(output_md).write_text(md, encoding="utf-8")
        print(f"Markdown saved → {output_md}")

    return result


def _format_markdown(result: dict) -> str:
    lines = [
        "# Manuscript Review",
        f"*Generated {result['timestamp']}*",
        f"**{result['word_count']:,} words | {result['chapter_count']} chapters**",
        "",
        "---",
        "## Agent 1: Literary Critic (Margaret Holloway)",
        "",
        result["critic_review"],
        "",
        "---",
        "## Agent 2: Professor of Fiction (Dr. James Whitfield)",
        "",
        result["professor_notes"],
        "",
        "---",
        f"## Revision Decision",
        f"**Stop revising:** {result['stop_revising']}",
        f"**Reason:** {result['stop_reason']}",
    ]
    return "\n".join(lines)


def parse_latest_review() -> None:
    """Parse the most recent saved review and print actionable items."""
    files = sorted(glob.glob("reviews/review_*.json"))
    if not files:
        print("No saved reviews found in reviews/")
        return
    latest = files[-1]
    with open(latest, encoding="utf-8") as f:
        data = json.load(f)
    items = data.get("craft_items", [])
    rating = data.get("rating")
    stop = data.get("stop_revising")
    reason = data.get("stop_reason")

    print(f"\n=== Parsed from {latest} ===")
    print(f"Rating: {rating}/5.0")
    print(f"Stop revising: {stop}  ({reason})\n")
    print(f"{'#':<4} {'SEV':<10} {'TYPE':<14} Statement")
    print("-" * 80)
    for item in items:
        q = " [qualified]" if item["qualified"] else ""
        print(f"{item['number']:<4} {item['severity']:<10} {item['fix_type']:<14} "
              f"{item['statement'][:60]}{q}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Dual-agent manuscript review")
    parser.add_argument("--output", metavar="FILE",
                        help="Also write a human-readable markdown report")
    parser.add_argument("--parse", action="store_true",
                        help="Parse the latest saved review for actionable items")
    parser.add_argument("--chapters-dir", default="chapters",
                        help="Directory containing chapter .md files (default: chapters/)")
    args = parser.parse_args()

    if args.parse:
        parse_latest_review()
        return

    generate_review(
        chapters_dir=args.chapters_dir,
        output_md=args.output,
    )


if __name__ == "__main__":
    main()
