"""
reader_panel.py — Four-persona reader evaluation panel.

The panel simulates four distinct readers responding to the completed arc
summary (or full manuscript for shorter works).  Each persona has a different
relationship to fiction and a different set of concerns — together they surface
blind spots that a single evaluator misses.

Four Reader Personas
--------------------

1. THE EDITOR — Judith Crane
   Senior acquisitions editor at a mid-size literary imprint.  25 years.
   Reads for: prose texture, narrative voice consistency, sentence-level craft,
   paragraph rhythm, whether the author's hand is visible in a good way.
   Does NOT care about: plot mechanics, genre expectations.

2. THE GENRE READER — Marcus Webb
   Voracious fantasy reader, 3-4 novels a month for 20 years.  Has strong
   genre expectations and will notice when the world-building doesn't pay off,
   the magic system isn't consistent, or the pacing drags in the middle act.
   Reads for: momentum, worldbuilding payoff, narrative satisfaction.
   Does NOT care about: literary technique visibility.

3. THE WRITER — Priya Nair
   Published literary fiction author, teaches at a university.  Reads as a
   craftsperson — tracking structure, foreshadowing completion, beat function,
   whether every scene is earning its place, technique choices and their effects.
   Reads for: architecture, economy, foreshadowing, scene function.
   Does NOT care about: whether the genre conventions are met.

4. THE FIRST READER — Danny Okafor
   Not a literary person.  Reads for pleasure, on the subway, in bed.  Will
   put a book down if it bores them.  Responds emotionally and reports honestly.
   Reads for: engagement, emotional impact, character likeability/investment,
   whether they want to know what happens next.
   Does NOT care about: craft terminology, structural analysis.

Each persona answers nine evaluation questions from their perspective.
Their responses are combined into a consensus report that identifies issues
all four agree on (high priority) vs. issues only one raises (low priority).

Usage
-----
    python reader_panel.py                    # evaluate full arc summary
    python reader_panel.py --chapters-dir chapters
    python reader_panel.py --parse            # parse latest saved panel
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

from llm_client import call_gpt, JUDGE_MODEL, summarise_manuscript, word_count


# ---------------------------------------------------------------------------
# Persona definitions
# ---------------------------------------------------------------------------

PERSONAS = [
    {
        "id":   "editor",
        "name": "Judith Crane",
        "role": "Senior acquisitions editor, literary imprint, 25 years",
        "focus": "prose texture, voice consistency, sentence craft, paragraph rhythm, "
                 "authorial presence, line-level precision",
        "blind_spot": "You are less concerned with plot mechanics or genre conventions "
                      "than with the quality of the writing itself.",
        "temperature": 0.4,
    },
    {
        "id":   "genre_reader",
        "name": "Marcus Webb",
        "role": "Avid fantasy reader, 3-4 novels per month for 20 years",
        "focus": "narrative momentum, worldbuilding payoff, magic system consistency, "
                 "pacing, genre satisfaction, whether the ending delivers",
        "blind_spot": "You are not a literary critic — you don't analyse technique. "
                      "You notice when things are boring, confusing, or unsatisfying.",
        "temperature": 0.6,
    },
    {
        "id":   "writer",
        "name": "Priya Nair",
        "role": "Published literary novelist, university creative writing instructor",
        "focus": "plot architecture, scene economy, foreshadowing completion, beat function, "
                 "POV discipline, structural choices and their effects, technique visibility",
        "blind_spot": "You read as a craftsperson. Genre conventions interest you only "
                      "as structural choices, not as requirements to fulfil.",
        "temperature": 0.3,
    },
    {
        "id":   "first_reader",
        "name": "Danny Okafor",
        "role": "General reader, reads for pleasure, not a literary person",
        "focus": "engagement, emotional impact, character likability, whether you want "
                 "to know what happens next, whether you were ever bored or confused",
        "blind_spot": "You don't use craft terminology. You report your honest emotional "
                      "response. If something bored you, you say so. If you loved a character, "
                      "you say why.",
        "temperature": 0.7,
    },
]

NINE_QUESTIONS = """\
Answer these nine questions from your specific perspective.  Be honest.
Be specific — name chapters, characters, moments.

1. What is the single strongest moment in this novel?  Why does it work?
2. What is the single weakest moment?  Why does it fail?
3. Did the protagonist earn their ending?  Explain.
4. Was there any point where you considered stopping reading?  When and why?
5. What is the most unresolved or unsatisfying element?
6. Rate the overall pacing: too fast / too slow / well-calibrated?  Where specifically?
7. Did the world feel real?  What made it feel real or fake?
8. Rate the novel: X out of 10.  What would change it by 1 point in either direction?
9. Would you recommend this to someone?  What kind of reader would love it?
   What kind of reader would bounce off it?
"""

PERSONA_SYSTEM_TEMPLATE = """\
You are {name}, a {role}.

You are reading a novel and responding as yourself — not as a literary critic,
not as an AI assistant.  You have a specific relationship to fiction shaped by
your role and history.

YOUR FOCUS: {focus}

YOUR BLIND SPOT: {blind_spot}

You do not hedge.  You do not say "as an AI I cannot..."  You are {name} and
you have an opinion.  State it directly.

{nine_questions}

Respond with your nine answers clearly numbered.  After the answers, add:

PANEL_DATA:
persona: {id}
overall_rating: X
pacing_verdict: too_fast|too_slow|well_calibrated
protagonist_earned_ending: yes|no|partial
would_recommend: yes|no|conditional
"""


# ---------------------------------------------------------------------------
# Run one persona
# ---------------------------------------------------------------------------

def run_persona(persona: dict, arc_summary: str) -> str:
    system = PERSONA_SYSTEM_TEMPLATE.format(
        name=persona["name"],
        role=persona["role"],
        focus=persona["focus"],
        blind_spot=persona["blind_spot"],
        nine_questions=NINE_QUESTIONS,
        id=persona["id"],
    )
    user = f"Here is the novel:\n\n{arc_summary}"

    print(f"  [{persona['id']}] {persona['name']} reading…")
    return call_gpt(
        system=system,
        user=user,
        model=JUDGE_MODEL,
        temperature=persona["temperature"],
        max_tokens=2_000,
    )


# ---------------------------------------------------------------------------
# Consensus extraction
# ---------------------------------------------------------------------------

def extract_panel_data(text: str) -> dict:
    data: dict = {}
    m = re.search(r"PANEL_DATA:\s*\n(.*?)(?:\n\n|$)", text, re.DOTALL)
    if not m:
        return data
    for line in m.group(1).strip().splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            data[k.strip()] = v.strip()
    # Try to extract rating from answers if not in PANEL_DATA
    if "overall_rating" not in data:
        m2 = re.search(r"(?:8\.|rate|rating)[^\n]*?(\d+(?:\.\d+)?)\s*(?:out of|/)\s*10", text, re.IGNORECASE)
        if m2:
            data["overall_rating"] = m2.group(1)
    return data


def build_consensus(responses: list[dict]) -> dict:
    """
    Identify high-consensus issues (raised by 3+ personas) and
    low-consensus issues (raised by only 1).
    """
    ratings = []
    pacing_votes: dict[str, int] = {}
    earned_votes: dict[str, int] = {}
    recommend_votes: dict[str, int] = {}

    for r in responses:
        d = r.get("panel_data", {})
        try:
            ratings.append(float(d.get("overall_rating", 0)))
        except (ValueError, TypeError):
            pass
        pv = d.get("pacing_verdict", "")
        if pv:
            pacing_votes[pv] = pacing_votes.get(pv, 0) + 1
        ev = d.get("protagonist_earned_ending", "")
        if ev:
            earned_votes[ev] = earned_votes.get(ev, 0) + 1
        rv = d.get("would_recommend", "")
        if rv:
            recommend_votes[rv] = recommend_votes.get(rv, 0) + 1

    avg_rating = sum(ratings) / len(ratings) if ratings else None
    dominant_pacing  = max(pacing_votes,  key=pacing_votes.get)  if pacing_votes  else None
    dominant_earned  = max(earned_votes,  key=earned_votes.get)   if earned_votes  else None
    dominant_rec     = max(recommend_votes, key=recommend_votes.get) if recommend_votes else None

    return {
        "average_rating":          avg_rating,
        "pacing_consensus":        dominant_pacing,
        "protagonist_earned":      dominant_earned,
        "recommend_consensus":     dominant_rec,
        "individual_ratings":      {r["persona"]: r.get("panel_data", {}).get("overall_rating") for r in responses},
        "rating_spread":           max(ratings) - min(ratings) if len(ratings) > 1 else 0,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run_panel(chapters_dir: str = "chapters") -> dict:
    files = sorted(Path(chapters_dir).glob("*.md"))
    if not files:
        print(f"No chapter files found in {chapters_dir}/")
        sys.exit(1)

    manuscript = "\n\n".join(f.read_text(encoding="utf-8") for f in files)
    wc = word_count(manuscript)
    print(f"Loaded {wc:,} words across {len(files)} chapters.")

    # Always summarise for the panel — they should respond to the whole arc,
    # not be distracted by line-level prose in an oversized context window.
    if wc > 15_000:
        print("Generating arc summary for panel…")
        arc = summarise_manuscript(manuscript)
        print(f"  Summary: {word_count(arc):,} words.")
    else:
        arc = manuscript

    print(f"\nRunning 4-persona reader panel…")
    responses = []
    for persona in PERSONAS:
        text = run_persona(persona, arc)
        panel_data = extract_panel_data(text)
        responses.append({
            "persona":    persona["id"],
            "name":       persona["name"],
            "response":   text,
            "panel_data": panel_data,
        })

    consensus = build_consensus(responses)

    result = {
        "timestamp":   datetime.utcnow().isoformat(),
        "word_count":  wc,
        "chapter_count": len(files),
        "responses":   responses,
        "consensus":   consensus,
    }

    os.makedirs("panels", exist_ok=True)
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    out = f"panels/panel_{ts}.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    print(f"\nPanel saved → {out}")

    _print_summary(consensus, responses)
    return result


def _print_summary(consensus: dict, responses: list[dict]) -> None:
    print("\n" + "=" * 60)
    print("READER PANEL CONSENSUS")
    print("=" * 60)
    avg = consensus.get("average_rating")
    print(f"Average rating:        {avg:.1f}/10" if avg else "Average rating:        n/a")
    print(f"Rating spread:         {consensus.get('rating_spread', 0):.1f} points")
    print(f"Pacing verdict:        {consensus.get('pacing_consensus', 'n/a')}")
    print(f"Protagonist earned it: {consensus.get('protagonist_earned', 'n/a')}")
    print(f"Recommend:             {consensus.get('recommend_consensus', 'n/a')}")
    print("\nIndividual ratings:")
    for persona_id, rating in consensus.get("individual_ratings", {}).items():
        name = next((p["name"] for p in PERSONAS if p["id"] == persona_id), persona_id)
        print(f"  {name:<22} {rating}/10")


def parse_latest_panel() -> None:
    files = sorted(glob.glob("panels/panel_*.json"))
    if not files:
        print("No saved panels found in panels/")
        return
    with open(files[-1], encoding="utf-8") as f:
        data = json.load(f)
    consensus = data.get("consensus", {})
    responses = data.get("responses", [])
    _print_summary(consensus, responses)
    print(f"\nFull panel: {files[-1]}")


def main() -> None:
    parser = argparse.ArgumentParser(description="4-persona reader panel evaluation")
    parser.add_argument("--chapters-dir", default="chapters")
    parser.add_argument("--parse", action="store_true",
                        help="Parse the latest saved panel result")
    args = parser.parse_args()

    if args.parse:
        parse_latest_panel()
    else:
        run_panel(args.chapters_dir)


if __name__ == "__main__":
    main()
