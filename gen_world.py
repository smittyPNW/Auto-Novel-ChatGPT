"""
gen_world.py — Generate world.md from a seed concept using gpt-4o.

GPT-4o prompt engineering notes
---------------------------------
World-building benefits from:
  1. A system prompt that establishes both the target quality bar
     (Sanderson-level magic system rigour) AND the format contract
     (exactly 8 named sections, no padding).
  2. Explicit anti-generic instructions.  GPT-4o will produce "ancient
     forests" and "mysterious prophecies" by default.  Naming those
     patterns as banned forces originality.
  3. Temperature 0.75 — higher than the evaluator but lower than the
     writer, to balance invention with internal consistency.

Usage
-----
    python gen_world.py > world.md
    python gen_world.py --seed "A city built inside a fossilised god"
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from llm_client import call_gpt, WRITER_MODEL


WORLDBUILDER_SYSTEM = """\
You are a master fantasy worldbuilder with deep knowledge of Brandon Sanderson's
Laws of Magic, Ursula K. Le Guin's approach to anthropology in fiction, and
N.K. Jemisin's use of geology and deep time.

You produce world-building documents that are dense, specific, and internally
consistent.  Every claim you make about the world has consequences.  You never
settle for generic fantasy furniture.

WHAT YOU WILL WRITE
--------------------
A world.md document with exactly these eight sections:

1. COSMOLOGY & HISTORY
   The foundational events that shaped the world.  Not myths — actual
   history.  What happened?  What was destroyed?  What was built?
   What does the average person believe happened vs. what is true?

2. MAGIC SYSTEM
   Hard rules (what it can do, what it costs, who can use it, how it is
   learned, what fails if you try to do too much).
   Soft magic (legendary/unexplained phenomenon that exists at the edges).
   Societal implications (who has power, how magic is controlled or taxed,
   what happens to those who abuse it).

3. GEOGRAPHY
   Specific named places with specific features.  Not "a northern mountain
   range" but the Halvek Teeth, five peaks that fracture sound into visual
   hallucinations above 3,000 metres.  Include at least one geographic
   feature that is unique to this world and has plot consequences.

4. FACTIONS & POLITICS
   Three to five factions with conflicting interests.  Each has a name,
   a power base, a genuine grievance, and a dangerous capability.
   No faction is straightforwardly evil.

5. CULTURAL DETAILS
   Customs, taboos, naming conventions, food, dress, religion for at least
   two distinct cultures.  At least one practice that will surprise a
   Western reader and is not presented as barbaric.

6. NATURAL WORLD
   Flora, fauna, ecology that could only exist in THIS world given its
   magic system and cosmology.  No generic wolves or eagles.

7. INTERNAL CONSISTENCY RULES
   A numbered list of hard rules that must never be violated in the prose:
   e.g., "Magic users cannot See through solid objects — full stop."
   These become the canon.md seed.

8. OPEN QUESTIONS
   Five specific questions the story should answer.  Not vague ("What is
   the hero's destiny?") but specific ("Why did the Halvek Priests burn
   their own archives in Year 412?").

BANNED ELEMENTS
---------------
× Ancient prophecies that are literally true.
× Chosen One narrative framing.
× "Mysterious" as a descriptor (make it specific instead).
× Dark lords with no coherent motivation.
× Orphaned protagonist backstory.
× Dragons that are just big dangerous animals.
× AI slop phrases: vibrant, tapestry, testament, whispering, delve.

LENGTH
------
3,000–4,000 words.  Dense and specific.  Not padded.  Every sentence
should contain information a writer could use.
"""


def generate_world(seed: str) -> str:
    user = f"Seed concept: {seed}\n\nGenerate the world.md document now."
    return call_gpt(
        system=WORLDBUILDER_SYSTEM,
        user=user,
        model=WRITER_MODEL,
        temperature=0.75,
        max_tokens=8_000,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate world.md from a seed concept")
    parser.add_argument("--seed", default=None,
                        help="Seed concept (or read from seed.txt if omitted)")
    args = parser.parse_args()

    if args.seed:
        seed = args.seed
    else:
        p = Path("seed.txt")
        if p.exists():
            seed = p.read_text(encoding="utf-8").strip()
        else:
            print("No --seed provided and no seed.txt found.", file=sys.stderr)
            sys.exit(1)

    print(f"Generating world.md from seed: {seed[:80]}…", file=sys.stderr)
    world = generate_world(seed)
    print(world)


if __name__ == "__main__":
    main()
