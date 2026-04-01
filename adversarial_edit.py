"""
adversarial_edit.py — Ruthless editing agent using gpt-4o.

The adversarial editor's job is to find ~500 words to cut from each chapter.
What gets cut reveals what is weakest.  This is inspired by the "murder your
darlings" tradition in editing.

GPT-4o prompt engineering notes
---------------------------------
For adversarial tasks GPT-4o performs best when:
  1. The persona is framed with a specific professional identity and
     motivation (not just "act as an editor").
  2. The model is explicitly told it will be evaluated on the *specificity*
     and *correctness* of its cuts, not on completeness or positivity.
  3. Temperature is low (0.3) — creative latitude hurts precision here.
  4. JSON output is specified with exact field names and examples in the
     system prompt.  GPT-4o follows JSON schemas reliably when they are
     demonstrated rather than just described.

Usage
-----
    python adversarial_edit.py --chapter 3
    python adversarial_edit.py --all
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

from llm_client import call_gpt, EDITOR_MODEL


# ---------------------------------------------------------------------------
# Editor persona system prompt
# ---------------------------------------------------------------------------

EDITOR_SYSTEM = """\
You are Harriet Vane, a ruthless literary editor with 20 years of experience
cutting fat from manuscripts at major publishing houses.  You have edited
Booker Prize winners.  You do not soften criticism.  You do not invent
improvements — you identify exactly what is already on the page and should
not be.

YOUR MANDATE
------------
Identify approximately 500 words to cut from the chapter.  Find the weakest
prose.  Look for:

  FAT         — Sentences that repeat information already established.
  REDUNDANT   — Actions described twice (narration and then dialogue repeating the same beat).
  OVER_EXPLAIN — Authorial intrusion explaining what the reader can already feel.
  GENERIC     — Descriptions that could appear in any fantasy novel ("the ancient stones").
  TELL        — Emotional states stated rather than dramatised.
  STRUCTURAL  — Whole scenes or beats that serve no unique purpose.

OUTPUT FORMAT
-------------
Respond with ONLY valid JSON.  No preamble.  No notes after the JSON.
Use exactly this schema:

{
  "chapter": N,
  "total_cuttable_words": N,
  "cuts": [
    {
      "id": 1,
      "category": "FAT|REDUNDANT|OVER_EXPLAIN|GENERIC|TELL|STRUCTURAL",
      "exact_quote": "the exact text to cut, verbatim, 20-80 words",
      "reason": "one sentence explaining why this is weak",
      "suggested_replacement": "optional: a tighter version, or null if the passage should simply be deleted",
      "estimated_words_saved": N
    }
  ],
  "tightest_passage": "quote the single best-written passage in the chapter (20-50 words)",
  "loosest_passage": "quote the single worst-written passage in the chapter (20-50 words)",
  "prose_quality": "one sentence overall assessment"
}

RULES
-----
- exact_quote must be verbatim text from the chapter.  Never paraphrase.
- Do not quote the same passage twice.
- Aim for 8-15 cut items that together total ~500 words.
- Rank cuts from most to least important (most important = id 1).
- If the chapter is genuinely tight, say so in prose_quality and reduce
  total_cuttable_words — do not invent cuts.
"""


# ---------------------------------------------------------------------------
# Chapter loading
# ---------------------------------------------------------------------------

def find_chapter_file(chapter_num: int, chapters_dir: str = "chapters") -> Path | None:
    files = sorted(Path(chapters_dir).glob("*.md"))
    for f in files:
        if re.search(rf"chapter[_-]?0*{chapter_num}\b", f.name, re.IGNORECASE):
            return f
    if chapter_num <= len(files):
        return files[chapter_num - 1]
    return None


def load_all_chapter_files(chapters_dir: str = "chapters") -> list[Path]:
    return sorted(Path(chapters_dir).glob("*.md"))


# ---------------------------------------------------------------------------
# Edit
# ---------------------------------------------------------------------------

def edit_chapter(chapter_num: int, chapter_text: str) -> dict:
    wc = len(chapter_text.split())
    user = f"Chapter {chapter_num} ({wc:,} words):\n\n{chapter_text}"

    print(f"[adversarial_edit] Editing Chapter {chapter_num} ({wc:,} words)…")
    raw = call_gpt(
        system=EDITOR_SYSTEM,
        user=user,
        model=EDITOR_MODEL,
        temperature=0.3,
        max_tokens=8_000,
    )

    # Parse JSON
    result = _parse_json(raw, chapter_num)
    result["original_word_count"] = wc
    return result


def _parse_json(raw: str, chapter_num: int) -> dict:
    m = re.search(r"```(?:json)?\s*([\s\S]+?)```", raw)
    if m:
        raw = m.group(1)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        m2 = re.search(r"\{[\s\S]+\}", raw)
        if m2:
            try:
                return json.loads(m2.group(0))
            except json.JSONDecodeError:
                pass
    return {
        "chapter": chapter_num,
        "parse_error": True,
        "raw_response": raw,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Adversarial chapter editor")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--chapter", type=int, metavar="N")
    group.add_argument("--all", action="store_true")
    parser.add_argument("--chapters-dir", default="chapters")
    args = parser.parse_args()

    os.makedirs("edits", exist_ok=True)
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")

    if args.chapter:
        f = find_chapter_file(args.chapter, args.chapters_dir)
        if not f:
            print(f"Chapter {args.chapter} not found in {args.chapters_dir}/")
            sys.exit(1)
        text = f.read_text(encoding="utf-8")
        result = edit_chapter(args.chapter, text)
        out = f"edits/edit_ch{args.chapter:02d}_{ts}.json"
        with open(out, "w", encoding="utf-8") as fp:
            json.dump(result, fp, indent=2, ensure_ascii=False)
        cuttable = result.get("total_cuttable_words", "?")
        print(f"  {cuttable} cuttable words identified → {out}")

    else:
        files = load_all_chapter_files(args.chapters_dir)
        if not files:
            print(f"No chapters found in {args.chapters_dir}/")
            sys.exit(1)
        summary = []
        for i, f in enumerate(files, 1):
            text = f.read_text(encoding="utf-8")
            result = edit_chapter(i, text)
            out = f"edits/edit_ch{i:02d}_{ts}.json"
            with open(out, "w", encoding="utf-8") as fp:
                json.dump(result, fp, indent=2, ensure_ascii=False)
            cuttable = result.get("total_cuttable_words", 0)
            summary.append({"chapter": i, "cuttable_words": cuttable, "file": out})
            print(f"  Ch{i}: {cuttable} cuttable words → {out}")

        total = sum(s["cuttable_words"] for s in summary if isinstance(s["cuttable_words"], int))
        print(f"\nTotal across {len(files)} chapters: ~{total:,} words cuttable.")


if __name__ == "__main__":
    main()
