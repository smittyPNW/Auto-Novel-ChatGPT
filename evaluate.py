"""
evaluate.py — Mechanical slop detection + LLM evaluation harness.

Three evaluation modes
----------------------
foundation  — Score the planning docs (world, characters, outline, voice, canon)
chapter     — Score a single chapter against the planning docs
full        — Holistic novel-level evaluation using chapter summaries

GPT-4o prompt engineering notes
---------------------------------
The evaluator is the harshest persona in the pipeline.  GPT-4o tends toward
inflated scores when not given explicit calibration guidance.  The system
prompts below:

  1. Anchor every score to a named calibration (e.g. "6/10 = competent AI
     output, not good fiction") so the model has concrete reference points.
  2. Require explicit gap identification for every dimension — GPT-4o cannot
     just say "8/10" without naming what would be needed to reach 10/10.
  3. Run mechanical slop detection *before* the LLM call so the penalty is
     quantified independently and cannot be talked away in the LLM reasoning.

Usage
-----
    python evaluate.py --mode foundation
    python evaluate.py --mode chapter --chapter 3
    python evaluate.py --mode full
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

from llm_client import call_gpt, JUDGE_MODEL


# ---------------------------------------------------------------------------
# Mechanical slop detection
# ---------------------------------------------------------------------------

SLOP_TIER1 = {
    "delve", "leverage", "synergy", "tapestry", "vibrant", "testament",
    "whispering", "searing pain", "heart pounded", "eyes widened",
    "breath caught", "knot in", "navigate", "foster", "underscore",
    "elucidate", "multifaceted", "nuanced", "paradigm", "meticulous",
}

SLOP_TIER2 = {
    "robust", "innovative", "seamlessly", "groundbreaking", "holistic",
    "transformative", "empower", "unlock potential", "game-changer",
    "cutting-edge", "utilize", "commence", "endeavour",
}

SLOP_PHRASES = [
    r"\bit was\b.*?\bwas\b",               # "it was X that was Y"
    r"\bthere was\b",
    r"not [a-z]+,? but [a-z]+",            # "Not fear, but dread"
    r"he/she/they (felt|knew|realised|understood) (that|how|why)",
    r"(suddenly|immediately|instantly) [a-z]+ed",
    r"\b(heart|pulse) (race|pound|hammer|skip)ed?\b",
    r"she/he looked? (down|up|away|back)",
    r"a (small|slight|faint|soft) smile",
    r"\bclearly\b",
    r"\bobviously\b",
    r"the (weight|burden|gravity) of [a-z ]+",
]


def slop_score(text: str) -> dict:
    """
    Scan text for AI slop patterns.  Returns a penalty dict:
      {penalty: float 0-3, hits: [{tier, term, count}]}
    Higher penalty = more slop.
    """
    low = text.lower()
    words = set(re.findall(r"\b\w+\b", low))
    hits = []

    for term in SLOP_TIER1:
        count = low.count(term)
        if count:
            hits.append({"tier": 1, "term": term, "count": count})

    for term in SLOP_TIER2:
        count = low.count(term)
        if count:
            hits.append({"tier": 2, "term": term, "count": count})

    for pat in SLOP_PHRASES:
        matches = re.findall(pat, low)
        if matches:
            hits.append({"tier": 3, "pattern": pat, "count": len(matches)})

    # Penalty: 0.5 per tier-1 hit, 0.25 per tier-2, 0.15 per tier-3 (capped at 3.0)
    penalty = 0.0
    for h in hits:
        if h["tier"] == 1:
            penalty += 0.5 * h["count"]
        elif h["tier"] == 2:
            penalty += 0.25 * h["count"]
        else:
            penalty += 0.15 * h["count"]

    return {"penalty": min(penalty, 3.0), "hits": hits}


# ---------------------------------------------------------------------------
# Foundation evaluation
# ---------------------------------------------------------------------------

FOUNDATION_JUDGE_SYSTEM = """\
You are a harsh, experienced fiction development editor reviewing planning
documents for a novel before any drafting begins.

Your job is to score these documents on 13 dimensions that predict whether
the resulting novel will be publishable.  You give a score from 0.0 to 10.0
for each dimension.

SCORE CALIBRATION — commit to these anchors:
  0-3  = Inadequate; will produce broken fiction.
  4-5  = Functional but generic; produces forgettable fiction.
  6    = Competent baseline — what average AI output produces.
  7    = Solid; a writer using this could produce a good first draft.
  8    = Strong; most professional outlines score here.
  9    = Exceptional; rare even in commercial publishing.
  10   = Masterwork; do not assign this unless you cannot name any gap.

For EVERY dimension you must:
  a) State the score.
  b) Name the strongest element.
  c) Identify the most important gap or risk.
  d) Specify what change would raise the score by 1 point.

Respond in valid JSON:
{
  "dimensions": {
    "magic_system_rigor": {"score": X, "strength": "...", "gap": "...", "improvement": "..."},
    "character_depth":    {"score": X, "strength": "...", "gap": "...", "improvement": "..."},
    "world_internal_consistency": {"score": X, ...},
    "plot_structure":     {"score": X, ...},
    "thematic_coherence": {"score": X, ...},
    "stakes_clarity":     {"score": X, ...},
    "voice_distinctiveness": {"score": X, ...},
    "pacing_blueprint":   {"score": X, ...},
    "foreshadowing_ledger": {"score": X, ...},
    "canon_completeness": {"score": X, ...},
    "conflict_escalation": {"score": X, ...},
    "sensory_grounding":  {"score": X, ...},
    "originality":        {"score": X, ...}
  },
  "foundation_score": X.X,
  "lore_score":       X.X,
  "summary":          "..."
}

foundation_score = weighted average (structure 30%, world 25%, character 20%, other 25%).
lore_score       = average of magic_system_rigor + world_internal_consistency + canon_completeness.
"""


def evaluate_foundation() -> dict:
    docs = {}
    for name in ("world.md", "characters.md", "outline.md", "voice.md", "canon.md"):
        p = Path(name)
        if p.exists():
            docs[name] = p.read_text(encoding="utf-8")

    if not docs:
        print("No planning documents found (world.md, characters.md, etc.)")
        sys.exit(1)

    user = "Evaluate the following planning documents:\n\n"
    for name, content in docs.items():
        user += f"### {name}\n{content}\n\n"

    print(f"[evaluate] Foundation evaluation with {JUDGE_MODEL}…")
    raw = call_gpt(
        system=FOUNDATION_JUDGE_SYSTEM,
        user=user,
        model=JUDGE_MODEL,
        temperature=0.2,
        max_tokens=4_000,
    )
    return _parse_json(raw)


# ---------------------------------------------------------------------------
# Chapter evaluation
# ---------------------------------------------------------------------------

CHAPTER_JUDGE_SYSTEM = """\
You are a harsh developmental editor evaluating a single draft chapter against
the project's planning documents.

Score on 10 dimensions (0.0–10.0 each).  Use the same calibration:
  6 = competent AI output (the floor, not a good score).
  8 = publishable quality in genre fiction.
  9+ = exceptional; justify it explicitly.

For each dimension provide: score, strength, gap, improvement.

Respond in valid JSON:
{
  "dimensions": {
    "voice_adherence":    {"score": X, "strength": "...", "gap": "...", "improvement": "..."},
    "beat_coverage":      {"score": X, ...},
    "prose_quality":      {"score": X, ...},
    "show_dont_tell":     {"score": X, ...},
    "pacing":             {"score": X, ...},
    "dialogue_quality":   {"score": X, ...},
    "canon_compliance":   {"score": X, ...},
    "character_consistency": {"score": X, ...},
    "tension_management": {"score": X, ...},
    "chapter_hook":       {"score": X, ...}
  },
  "raw_score":      X.X,
  "slop_penalty":   X.X,
  "final_score":    X.X,
  "word_count":     N,
  "summary":        "..."
}
raw_score = simple average of dimension scores.
slop_penalty is provided to you as input — subtract it from raw_score to get final_score.
"""


def evaluate_chapter(chapter_num: int, chapters_dir: str = "chapters") -> dict:
    files = sorted(Path(chapters_dir).glob("*.md"))
    chapter_files = [
        f for f in files
        if re.search(rf"chapter[_-]?0*{chapter_num}\b", f.name, re.IGNORECASE)
    ]
    if not chapter_files:
        # Fall back: pick the Nth file
        if chapter_num <= len(files):
            chapter_files = [files[chapter_num - 1]]
        else:
            print(f"Chapter {chapter_num} not found in {chapters_dir}/")
            sys.exit(1)

    chapter_text = chapter_files[-1].read_text(encoding="utf-8")
    wc = len(chapter_text.split())

    slop = slop_score(chapter_text)
    penalty = slop["penalty"]

    docs = {}
    for name in ("world.md", "characters.md", "outline.md", "voice.md"):
        p = Path(name)
        if p.exists():
            docs[name] = p.read_text(encoding="utf-8")[:3000]

    user = f"slop_penalty: {penalty:.2f}\nword_count: {wc}\n\n"
    user += f"CHAPTER {chapter_num}:\n{chapter_text}\n\n"
    for name, content in docs.items():
        user += f"### {name}\n{content}\n\n"

    print(f"[evaluate] Chapter {chapter_num} evaluation ({wc:,} words, slop penalty {penalty:.2f})…")
    raw = call_gpt(
        system=CHAPTER_JUDGE_SYSTEM,
        user=user,
        model=JUDGE_MODEL,
        temperature=0.2,
        max_tokens=3_000,
    )
    result = _parse_json(raw)
    result["slop_hits"] = slop["hits"]
    return result


# ---------------------------------------------------------------------------
# Full novel evaluation
# ---------------------------------------------------------------------------

NOVEL_JUDGE_SYSTEM = """\
You are a developmental editor performing a holistic evaluation of a complete
novel draft.  You have been given chapter-by-chapter summaries.

Score 6 dimensions (0.0–10.0).  Calibration: 6 = competent AI baseline.

For each dimension: score, strength, gap, improvement.

Respond in valid JSON:
{
  "dimensions": {
    "arc_completion":     {"score": X, "strength": "...", "gap": "...", "improvement": "..."},
    "pacing_overall":     {"score": X, ...},
    "theme_coherence":    {"score": X, ...},
    "character_arcs":     {"score": X, ...},
    "foreshadowing_payoff": {"score": X, ...},
    "emotional_impact":   {"score": X, ...}
  },
  "novel_score": X.X,
  "summary":     "..."
}
"""


def evaluate_full(chapters_dir: str = "chapters") -> dict:
    from llm_client import summarise_manuscript, chunk_manuscript

    files = sorted(Path(chapters_dir).glob("*.md"))
    if not files:
        print(f"No chapters found in {chapters_dir}/")
        sys.exit(1)

    manuscript = "\n\n".join(f.read_text(encoding="utf-8") for f in files)
    wc = len(manuscript.split())

    print(f"[evaluate] Full novel evaluation — {wc:,} words across {len(files)} chapters…")
    summary = summarise_manuscript(manuscript)
    print(f"  Summary: {len(summary.split()):,} words.")

    print(f"  Calling {JUDGE_MODEL} for holistic evaluation…")
    raw = call_gpt(
        system=NOVEL_JUDGE_SYSTEM,
        user=f"Chapter summaries:\n\n{summary}",
        model=JUDGE_MODEL,
        temperature=0.2,
        max_tokens=3_000,
    )
    return _parse_json(raw)


# ---------------------------------------------------------------------------
# JSON parsing helper
# ---------------------------------------------------------------------------

def _parse_json(raw: str) -> dict:
    """Extract JSON from a response that may be wrapped in markdown fences."""
    m = re.search(r"```(?:json)?\s*([\s\S]+?)```", raw)
    if m:
        raw = m.group(1)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # Last resort: try to find the first { ... } block
        m2 = re.search(r"\{[\s\S]+\}", raw)
        if m2:
            try:
                return json.loads(m2.group(0))
            except json.JSONDecodeError:
                pass
    return {"raw_response": raw, "parse_error": True}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate novel foundation, chapter, or full manuscript")
    parser.add_argument("--mode", choices=["foundation", "chapter", "full"], required=True)
    parser.add_argument("--chapter", type=int, metavar="N",
                        help="Chapter number (required for --mode chapter)")
    parser.add_argument("--chapters-dir", default="chapters")
    args = parser.parse_args()

    if args.mode == "foundation":
        result = evaluate_foundation()
    elif args.mode == "chapter":
        if not args.chapter:
            parser.error("--chapter N is required for --mode chapter")
        result = evaluate_chapter(args.chapter, args.chapters_dir)
    else:
        result = evaluate_full(args.chapters_dir)

    os.makedirs("evaluations", exist_ok=True)
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    out_path = f"evaluations/{args.mode}_{ts}.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    print(f"\nEvaluation saved → {out_path}")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
