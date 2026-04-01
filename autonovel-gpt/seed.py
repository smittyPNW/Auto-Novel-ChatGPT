"""
seed.py — Generate novel seed concepts using gpt-4o at maximum temperature.

The seed is the starting point for the entire pipeline.  It must be:
  - Specific enough to constrain the world and characters meaningfully
  - Open enough to allow 70-90k words of story
  - Distinctive enough that the resulting novel won't feel generic
  - Grounded in a concrete premise, not a vague mood

GPT-4o prompt engineering notes
---------------------------------
Temperature 1.0 is used here deliberately — this is the one stage where
maximum creative variance is desirable.  We generate 10 candidates and let
the user (or an automated picker) choose the strongest one.

The system prompt bans the most common AI-generated fantasy premises to
force genuine originality.

Usage
-----
    python seed.py              # generate 10 seeds, print to stdout
    python seed.py --pick 3     # write seed 3 to seed.txt
    python seed.py --custom "A city built inside a fossilised god"
                                # write custom seed to seed.txt
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from llm_client import call_gpt, WRITER_MODEL


SEED_SYSTEM = """\
You are a novelist's creative partner specialising in high-concept premises.
Your job is to generate novel seed concepts that are:

  SPECIFIC   — Not "a world where magic is dying" but "the last three
               licensed Distillers of the Amber Veil compete for the
               final imperial contract before the emperor bans the practice."
  ORIGINAL   — No chosen ones.  No dark lords.  No orphan protagonists
               discovering secret powers.  No prophecies.  No ancient evils
               returning.  No medieval European defaults.
  GENERATIVE — The premise must contain enough conflict, world texture, and
               character potential to sustain 75,000 words.
  GROUNDED   — Root the fantastical in something human and recognisable:
               economics, family, professional life, class, grief, ambition.

BANNED PREMISES (do not generate anything resembling these):
  × Hero discovers they are the chosen one
  × Orphan with secret magical heritage
  × Ancient evil awakens / dark lord returns
  × Quest to find/destroy a magical object
  × Portal fantasy (character from our world enters another)
  × Academy/school for magic users as primary setting
  × Three-way love triangle as central conflict
  × Rebellion against an evil empire (without a more specific hook)

OUTPUT FORMAT
-------------
Generate exactly 10 seed concepts, numbered 1-10.
For each seed write:

  N. [ONE-LINE HOOK]
     PREMISE: 2-3 sentences establishing the core conflict and stakes.
     WORLD TEXTURE: One sentence on what makes this world distinctive.
     CHARACTER SEED: One sentence on the protagonist's specific situation and want.
     TENSION: One sentence on the central irresolvable conflict.

Make each of the 10 radically different from the others — different genres
within fantasy, different emotional registers, different structural shapes.
"""


def generate_seeds() -> str:
    user = "Generate 10 novel seed concepts now."
    return call_gpt(
        system=SEED_SYSTEM,
        user=user,
        model=WRITER_MODEL,
        temperature=1.0,
        max_tokens=4_000,
    )


def parse_seeds(text: str) -> list[dict]:
    """Parse the numbered seed list into structured dicts."""
    seeds = []
    blocks = re.split(r"\n(?=\d+\.)", text.strip())
    for block in blocks:
        m = re.match(r"(\d+)\.\s+(.+)", block, re.DOTALL)
        if not m:
            continue
        num = int(m.group(1))
        body = m.group(2)
        hook_m = re.match(r"(.+?)\n", body)
        hook = hook_m.group(1).strip() if hook_m else body.split("\n")[0].strip()
        seeds.append({"number": num, "hook": hook, "full": block.strip()})
    return seeds


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate novel seed concepts")
    parser.add_argument("--pick", type=int, metavar="N",
                        help="Write seed N to seed.txt")
    parser.add_argument("--custom", metavar="TEXT",
                        help="Write a custom seed directly to seed.txt")
    args = parser.parse_args()

    if args.custom:
        Path("seed.txt").write_text(args.custom.strip(), encoding="utf-8")
        print(f"Custom seed written to seed.txt:\n  {args.custom.strip()}")
        return

    print("Generating 10 seed concepts…\n", file=sys.stderr)
    raw = generate_seeds()
    seeds = parse_seeds(raw)

    # Always print all seeds
    print(raw)

    if args.pick:
        match = next((s for s in seeds if s["number"] == args.pick), None)
        if not match:
            print(f"\nSeed {args.pick} not found in output.", file=sys.stderr)
            sys.exit(1)
        Path("seed.txt").write_text(match["hook"], encoding="utf-8")
        print(f"\n✓ Seed {args.pick} written to seed.txt:\n  {match['hook']}", file=sys.stderr)


if __name__ == "__main__":
    main()
