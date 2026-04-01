"""
draft_chapter.py — Write a single novel chapter using gpt-4o.

GPT-4o prompt engineering notes
--------------------------------
GPT-4o benefits from:
  1. A rich, specific system prompt that commits to a persona and style
     BEFORE the user turn begins.  Unlike Claude, GPT-4o treats the system
     prompt as a contract it will honour throughout the completion.
  2. Explicit anti-pattern lists in the system prompt (GPT-4o will avoid them
     reliably if named up-front, rather than after the fact in the user turn).
  3. Temperature 0.85 for first drafts — high enough for creative variance,
     low enough to avoid narrative drift.
  4. A structured user turn that separates context, instructions, and the
     actual chapter spec.  GPT-4o performs best when it knows *why* it is
     seeing each block of information.

Usage
-----
    python draft_chapter.py --chapter 3
    python draft_chapter.py --chapter 3 --retry      # force regeneration
    python draft_chapter.py --all                    # draft all missing chapters
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from datetime import datetime
from pathlib import Path

from llm_client import call_gpt, WRITER_MODEL


# ---------------------------------------------------------------------------
# System prompt — the writer persona
# ---------------------------------------------------------------------------

WRITER_SYSTEM = """\
You are a literary fiction writer in the middle of drafting a fantasy novel.
You have already absorbed the world-building documents, character registries,
voice guidelines, and chapter outline.  Your job is to write the next chapter
exactly as it appears in the outline — no more, no less.

NARRATIVE STANCE
----------------
- Third-person limited past tense.  Stay locked to ONE point-of-view character
  per chapter.  Never slip into omniscient narration.
- Show, do not tell.  Dramatise emotions through action, dialogue, and
  sensory detail.  Do not write "she felt afraid" when you can write the
  racing heart, the dry mouth, the involuntary backward step.
- Dialogue must reveal character, advance plot, or both.  Cut any exchange that
  does neither.
- Vary sentence length deliberately.  Short sentences accelerate tension.
  Longer sentences slow pace and create texture.  Use both.

VOICE DISCIPLINE
----------------
- Honour the voice guidelines exactly.  If the voice is spare and understated,
  do not drift into lush description.  If it is lyrical, do not flatten it.
- Never start more than two consecutive sentences with the same word or
  grammatical construction.
- Avoid adverbs modifying dialogue tags ("she said quietly" → "she whispered").

ABSOLUTE BANS — these will fail the mechanical slop detector:
  × "delve", "leverage", "synergy", "tapestry", "vibrant", "testament",
    "whispering winds", "searing pain", "heart pounded", "eyes widened",
    "breath caught", "knot in her/his stomach"
  × Opening a chapter with weather unless thematically essential and original.
  × Starting a sentence with "It was" or "There was" more than once per chapter.
  × Explaining the meaning of your own metaphors.
  × Ending chapters with a character falling asleep, looking in a mirror,
    or summarising what they have learned.
  × Rhetorical repetition formula: "Not X.  But Y." — use it zero times.

LENGTH
------
Target 2 500–3 500 words per chapter.  Aim for the higher end if the outline
beat list is substantial, lower end for transitional chapters.

OUTPUT
------
Write the chapter prose only.  Begin with the chapter heading:
  # Chapter N: [Title]
Then the text.  No preamble, no notes, no self-commentary after.
"""


# ---------------------------------------------------------------------------
# Context loading
# ---------------------------------------------------------------------------

def load_file(path: str, default: str = "") -> str:
    p = Path(path)
    if p.exists():
        return p.read_text(encoding="utf-8")
    return default


def get_chapter_outline(chapter_num: int, outline_text: str) -> str:
    """Extract the section for chapter N from outline.md."""
    pattern = re.compile(
        rf"(?:^|\n)(#+\s*Chapter\s+{chapter_num}\b.+?)(?=\n#+\s*Chapter\s+|\Z)",
        re.DOTALL | re.IGNORECASE,
    )
    m = pattern.search(outline_text)
    if m:
        return m.group(1).strip()
    return f"(No outline entry found for Chapter {chapter_num}.  Infer from context.)"


def get_previous_chapter_tail(chapter_num: int, chapters_dir: str = "chapters", chars: int = 2500) -> str:
    """Return the last `chars` characters of the previous chapter."""
    if chapter_num <= 1:
        return "(This is the opening chapter — no prior chapter.)"
    prev = chapter_num - 1
    files = sorted(Path(chapters_dir).glob("*.md"))
    # Find the file that looks like chapter N-1
    for f in files:
        if re.search(rf"(?:ch|chapter)[_-]?0*{prev}\b", f.name, re.IGNORECASE):
            text = f.read_text(encoding="utf-8")
            return text[-chars:] if len(text) > chars else text
    # fallback: use the last file
    if files:
        text = files[-1].read_text(encoding="utf-8")
        return text[-chars:] if len(text) > chars else text
    return "(No previous chapter found.)"


# ---------------------------------------------------------------------------
# Draft
# ---------------------------------------------------------------------------

def draft_chapter(chapter_num: int, chapters_dir: str = "chapters") -> str:
    outline     = load_file("outline.md")
    world       = load_file("world.md")
    characters  = load_file("characters.md")
    voice       = load_file("voice.md")
    canon       = load_file("canon.md")

    chapter_spec = get_chapter_outline(chapter_num, outline)
    prev_tail    = get_previous_chapter_tail(chapter_num, chapters_dir)

    user_prompt = f"""\
VOICE GUIDELINES
================
{voice or "(No voice.md found — use a clear, controlled literary voice.)"}

CHAPTER OUTLINE — Chapter {chapter_num}
=========================================
{chapter_spec}

CONTINUITY — end of previous chapter
======================================
{prev_tail}

WORLD REFERENCE (excerpt)
==========================
{world[:4000] if world else "(No world.md found.)"}

CHARACTER REGISTRY (excerpt)
=============================
{characters[:3000] if characters else "(No characters.md found.)"}

CANON NOTES (excerpt)
=====================
{canon[:2000] if canon else "(No canon.md found.)"}

WRITING INSTRUCTIONS
====================
1. Write Chapter {chapter_num} exactly as specified in the outline above.
2. Stay in the POV character's head.  No head-hopping.
3. Open in medias res — no weather, no character waking up, no mirror.
4. Every scene needs conflict, tension, or revelation.  No neutral filler.
5. Use the world details — specific place names, institutions, magic rules —
   not generic fantasy furniture.
6. Integrate at least one detail from the canon notes that has not appeared
   in previous chapters.
7. End the chapter on a forward hook: a question, a threat, a revelation, or
   a decision — something that makes the reader turn the page.
8. Write {2500}–{3500} words.

Now write Chapter {chapter_num}:
"""

    print(f"[draft_chapter] Drafting Chapter {chapter_num} with {WRITER_MODEL}…")
    text = call_gpt(
        system=WRITER_SYSTEM,
        user=user_prompt,
        model=WRITER_MODEL,
        temperature=0.85,
        max_tokens=6_000,
    )
    return text


def save_chapter(chapter_num: int, text: str, chapters_dir: str = "chapters") -> str:
    os.makedirs(chapters_dir, exist_ok=True)
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    filename = os.path.join(chapters_dir, f"chapter_{chapter_num:02d}_{ts}.md")
    Path(filename).write_text(text, encoding="utf-8")
    words = len(text.split())
    print(f"  Saved → {filename}  ({words:,} words)")
    return filename


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Draft a novel chapter with gpt-4o")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--chapter", type=int, metavar="N",
                       help="Draft chapter number N")
    group.add_argument("--all", action="store_true",
                       help="Draft all chapters listed in outline.md")
    parser.add_argument("--chapters-dir", default="chapters")
    args = parser.parse_args()

    if args.chapter:
        text = draft_chapter(args.chapter, args.chapters_dir)
        save_chapter(args.chapter, text, args.chapters_dir)
    else:
        # Find all chapter numbers mentioned in outline.md
        outline = load_file("outline.md")
        nums = sorted(set(
            int(m) for m in re.findall(r"(?:^|\n)#+\s*Chapter\s+(\d+)", outline, re.IGNORECASE)
        ))
        if not nums:
            print("No chapters found in outline.md.  Exiting.")
            sys.exit(1)
        print(f"Drafting chapters: {nums}")
        for n in nums:
            text = draft_chapter(n, args.chapters_dir)
            save_chapter(n, text, args.chapters_dir)


if __name__ == "__main__":
    main()
