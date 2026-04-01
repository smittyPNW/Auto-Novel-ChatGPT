"""
gen_characters.py — Generate characters.md from seed + world.md using gpt-4o.

Usage
-----
    python gen_characters.py > characters.md
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from llm_client import call_gpt, WRITER_MODEL


CHARACTER_SYSTEM = """\
You are a novelist and character development expert.  You create characters
who feel like people — contradictory, motivated by history they cannot
escape, capable of surprising themselves.

You do not create archetypes.  You create individuals.

WHAT YOU WILL WRITE
--------------------
A characters.md document covering 5-8 characters with:

For EACH character:

NAME & ROLE
  Full name, age, role in the story (protagonist / antagonist / foil / etc.).
  One-line physical description that is specific and non-generic.

CORE WOUND
  The formative event or absence that shapes this person's worldview.
  Be specific: not "they lost someone" but who, how, and what the character
  concluded (rightly or wrongly) about the world as a result.

WANT vs. NEED
  What they consciously pursue (want) vs. what they actually need to grow.
  These must be in genuine tension — not obvious complements.

VOICE
  3-4 sentences of sample internal monologue in their voice.
  Plus: 2-3 dialogue quirks (word choices, sentence structures, things
  they never say).

RELATIONSHIP MAP
  How they relate to each other named character.  Not "they distrust X"
  but the specific reason, rooted in a shared history.

ARC
  Where they start, the moment of maximum pressure, where they end.
  The ending must be earned — it must follow from who they are, not from
  plot convenience.

SECRETS
  One thing they are hiding from other characters.
  One thing they are hiding from themselves.

BANNED ELEMENTS
---------------
× Tragic backstory that is purely decorative (it must change their behaviour).
× Characters who exist only to help or hinder the protagonist.
× Mentors without their own agendas.
× Love interests with no goals of their own.
× Evil because evil.

Respond in markdown.  Use ## for each character name.
Write 400-600 words per character.
"""


def generate_characters(seed: str, world: str) -> str:
    user = f"Seed: {seed}\n\nWorld document:\n{world}\n\nGenerate the characters.md document now."
    return call_gpt(
        system=CHARACTER_SYSTEM,
        user=user,
        model=WRITER_MODEL,
        temperature=0.80,
        max_tokens=8_000,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate characters.md")
    args = parser.parse_args()

    seed = ""
    p = Path("seed.txt")
    if p.exists():
        seed = p.read_text(encoding="utf-8").strip()

    world = ""
    w = Path("world.md")
    if w.exists():
        world = w.read_text(encoding="utf-8")
    else:
        print("world.md not found — run gen_world.py first.", file=sys.stderr)
        sys.exit(1)

    print("Generating characters.md…", file=sys.stderr)
    chars = generate_characters(seed, world)
    print(chars)


if __name__ == "__main__":
    main()
