"""
run_pipeline.py — Master orchestrator for the autonovel-gpt pipeline.

Manages the full four-phase lifecycle of an autonomous novel:

  Phase 0 — Setup & validation
  Phase 1 — Foundation (world → characters → outline → voice → canon)
              Loop until foundation_score > FOUNDATION_THRESHOLD
  Phase 2 — First draft (sequential chapter writing + per-chapter evaluation)
              Keep chapters scoring > CHAPTER_THRESHOLD; retry up to MAX_RETRIES
  Phase 3 — Revision
    3a: Adversarial edits → Reader panel → Diagnosis → Targeted rewrites
        Loop up to MAX_REVISION_CYCLES or until score plateau
    3b: Deep review loop (review.py dual-expert agents)
        Loop until stopping condition met (see review.py)
  Phase 4 — Export (manuscript assembly, optional LaTeX/ePub)

State is persisted in state.json so the pipeline can be resumed after
interruption.  Every accepted result is committed to git.

Usage
-----
    python run_pipeline.py                        # full pipeline
    python run_pipeline.py --phase foundation     # run only Phase 1
    python run_pipeline.py --phase draft          # run only Phase 2
    python run_pipeline.py --phase revision       # run only Phase 3
    python run_pipeline.py --phase export         # run only Phase 4
    python run_pipeline.py --resume               # resume from last saved state
    python run_pipeline.py --status               # print current state and exit
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Configuration thresholds (all overridable via environment variables)
# ---------------------------------------------------------------------------

FOUNDATION_THRESHOLD  = float(os.getenv("FOUNDATION_THRESHOLD",  "7.5"))
LORE_THRESHOLD        = float(os.getenv("LORE_THRESHOLD",        "7.0"))
CHAPTER_THRESHOLD     = float(os.getenv("CHAPTER_THRESHOLD",     "6.0"))
CHAPTER_MAX_RETRIES   = int(os.getenv("CHAPTER_MAX_RETRIES",     "5"))
FOUNDATION_MAX_ITER   = int(os.getenv("FOUNDATION_MAX_ITER",     "20"))
MAX_REVISION_CYCLES   = int(os.getenv("MAX_REVISION_CYCLES",     "6"))
REVISION_PLATEAU_DELTA = float(os.getenv("REVISION_PLATEAU_DELTA", "0.3"))
REVIEW_MAX_ROUNDS     = int(os.getenv("REVIEW_MAX_ROUNDS",       "4"))

STATE_FILE = "state.json"

# ---------------------------------------------------------------------------
# State management
# ---------------------------------------------------------------------------

DEFAULT_STATE = {
    "version":          1,
    "created":          None,
    "updated":          None,
    "phase":            "setup",          # setup|foundation|draft|revision|export|done
    "foundation_score": None,
    "lore_score":       None,
    "foundation_iter":  0,
    "chapters_drafted": [],               # list of chapter numbers
    "chapter_scores":   {},               # {chapter_num: score}
    "revision_cycle":   0,
    "revision_scores":  [],               # score after each cycle
    "review_rounds":    0,
    "last_rating":      None,
    "stop_revising":    False,
    "exported":         False,
    "log":              [],               # brief event log
}


def load_state() -> dict:
    if Path(STATE_FILE).exists():
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    state = DEFAULT_STATE.copy()
    state["created"] = datetime.utcnow().isoformat()
    return state


def save_state(state: dict) -> None:
    state["updated"] = datetime.utcnow().isoformat()
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)


def log_event(state: dict, msg: str) -> None:
    ts = datetime.utcnow().strftime("%H:%M:%S")
    entry = f"[{ts}] {msg}"
    state["log"].append(entry)
    print(entry)


# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------

def git_commit(message: str) -> None:
    try:
        subprocess.run(["git", "add", "-A"], check=True, capture_output=True)
        result = subprocess.run(
            ["git", "diff", "--cached", "--quiet"], capture_output=True
        )
        if result.returncode == 0:
            return  # Nothing staged
        subprocess.run(["git", "commit", "-m", message], check=True, capture_output=True)
    except subprocess.CalledProcessError as e:
        print(f"  [git] Warning: {e}")


# ---------------------------------------------------------------------------
# Script runners
# ---------------------------------------------------------------------------

def run_script(args: list[str], capture: bool = False) -> subprocess.CompletedProcess:
    """Run a pipeline script with the current Python interpreter."""
    cmd = [sys.executable] + args
    if capture:
        return subprocess.run(cmd, capture_output=True, text=True)
    return subprocess.run(cmd, check=False)


def run_and_capture(args: list[str]) -> str:
    result = run_script(args, capture=True)
    return (result.stdout or "") + (result.stderr or "")


# ---------------------------------------------------------------------------
# Score extraction helpers
# ---------------------------------------------------------------------------

def latest_json(directory: str, pattern: str) -> dict | None:
    files = sorted(Path(directory).glob(pattern))
    if not files:
        return None
    with open(files[-1], encoding="utf-8") as f:
        return json.load(f)


def get_foundation_scores() -> tuple[float | None, float | None]:
    data = latest_json("evaluations", "foundation_*.json")
    if not data:
        return None, None
    return data.get("foundation_score"), data.get("lore_score")


def get_chapter_score(chapter_num: int) -> float | None:
    data = latest_json("evaluations", f"chapter_{chapter_num:02d}_*.json")
    if not data:
        # Try any chapter eval file
        data = latest_json("evaluations", f"chapter_*{chapter_num}*.json")
    if not data:
        return None
    return data.get("final_score")


def get_latest_review() -> dict | None:
    return latest_json("reviews", "review_*.json")


def get_latest_panel() -> dict | None:
    return latest_json("panels", "panel_*.json")


def get_revision_score() -> float | None:
    """Estimate current manuscript quality from the latest full evaluation."""
    data = latest_json("evaluations", "full_*.json")
    if data:
        return data.get("novel_score")
    # Fall back to average chapter score
    chapter_evals = sorted(Path("evaluations").glob("chapter_*.json"))
    if not chapter_evals:
        return None
    scores = []
    for f in chapter_evals:
        with open(f, encoding="utf-8") as fh:
            d = json.load(fh)
        s = d.get("final_score")
        if s is not None:
            scores.append(float(s))
    return sum(scores) / len(scores) if scores else None


# ---------------------------------------------------------------------------
# Phase 0 — Setup
# ---------------------------------------------------------------------------

def phase_setup(state: dict) -> None:
    log_event(state, "Phase 0: Setup")

    missing = []
    for f in ("seed.txt",):
        if not Path(f).exists():
            missing.append(f)
    if missing:
        print(f"\n  Missing required files: {', '.join(missing)}")
        print("  Run: python seed.py --pick N   (or --custom 'your concept')")
        sys.exit(1)

    seed = Path("seed.txt").read_text(encoding="utf-8").strip()
    log_event(state, f"Seed: {seed[:80]}")

    os.makedirs("chapters",    exist_ok=True)
    os.makedirs("evaluations", exist_ok=True)
    os.makedirs("reviews",     exist_ok=True)
    os.makedirs("panels",      exist_ok=True)
    os.makedirs("edits",       exist_ok=True)

    state["phase"] = "foundation"
    save_state(state)
    git_commit("Pipeline setup complete")


# ---------------------------------------------------------------------------
# Phase 1 — Foundation
# ---------------------------------------------------------------------------

def phase_foundation(state: dict) -> None:
    log_event(state, "Phase 1: Foundation generation")

    for iteration in range(state["foundation_iter"], FOUNDATION_MAX_ITER):
        state["foundation_iter"] = iteration + 1
        log_event(state, f"  Foundation iteration {iteration + 1}/{FOUNDATION_MAX_ITER}")

        # Generate documents
        for script, output in [
            ("gen_world.py",      "world.md"),
            ("gen_characters.py", "characters.md"),
        ]:
            if not Path(output).exists() or iteration > 0:
                log_event(state, f"    Running {script}…")
                raw = run_and_capture([script])
                if raw.strip():
                    Path(output).write_text(raw, encoding="utf-8")

        # Evaluate
        log_event(state, "    Evaluating foundation…")
        run_script(["evaluate.py", "--mode", "foundation"])

        foundation_score, lore_score = get_foundation_scores()
        state["foundation_score"] = foundation_score
        state["lore_score"]       = lore_score
        save_state(state)

        log_event(state,
            f"    Scores — foundation: {foundation_score}, lore: {lore_score}"
        )

        if (foundation_score is not None and foundation_score >= FOUNDATION_THRESHOLD and
                lore_score is not None and lore_score >= LORE_THRESHOLD):
            log_event(state, f"  Foundation threshold met ({foundation_score} / {lore_score})")
            break

        if iteration < FOUNDATION_MAX_ITER - 1:
            log_event(state, "  Threshold not met — regenerating…")
            time.sleep(2)

    git_commit(f"Foundation documents (score: {state['foundation_score']})")
    state["phase"] = "draft"
    save_state(state)


# ---------------------------------------------------------------------------
# Phase 2 — First draft
# ---------------------------------------------------------------------------

def phase_draft(state: dict) -> None:
    log_event(state, "Phase 2: First draft")

    # Determine chapter count from outline
    outline = Path("outline.md")
    if not outline.exists():
        log_event(state, "  No outline.md found — generating minimal outline…")
        # Minimal fallback: 12 chapters
        chapter_nums = list(range(1, 13))
    else:
        text = outline.read_text(encoding="utf-8")
        chapter_nums = sorted(set(
            int(m) for m in re.findall(r"(?:^|\n)#+\s*Chapter\s+(\d+)", text, re.IGNORECASE)
        ))
        if not chapter_nums:
            chapter_nums = list(range(1, 13))

    log_event(state, f"  Chapters to draft: {chapter_nums}")

    for chapter_num in chapter_nums:
        if chapter_num in state["chapters_drafted"]:
            log_event(state, f"  Chapter {chapter_num} already drafted — skipping.")
            continue

        accepted = False
        best_score = 0.0
        best_file = None

        for attempt in range(1, CHAPTER_MAX_RETRIES + 1):
            log_event(state, f"  Chapter {chapter_num} — attempt {attempt}/{CHAPTER_MAX_RETRIES}")
            run_script(["draft_chapter.py", "--chapter", str(chapter_num)])
            run_script(["evaluate.py", "--mode", "chapter", "--chapter", str(chapter_num)])

            score = get_chapter_score(chapter_num)
            log_event(state, f"    Score: {score}")

            if score is not None and score > best_score:
                best_score = score
                # Find the latest chapter file
                ch_files = sorted(Path("chapters").glob(f"chapter_{chapter_num:02d}_*.md"))
                if ch_files:
                    best_file = str(ch_files[-1])

            if score is not None and score >= CHAPTER_THRESHOLD:
                accepted = True
                break

        if not accepted:
            log_event(state, f"  Chapter {chapter_num} did not reach threshold after "
                             f"{CHAPTER_MAX_RETRIES} attempts (best: {best_score:.2f}). "
                             "Keeping best-effort.")

        state["chapters_drafted"].append(chapter_num)
        state["chapter_scores"][str(chapter_num)] = best_score
        save_state(state)
        git_commit(f"Chapter {chapter_num} drafted (score: {best_score:.2f})")

    state["phase"] = "revision"
    save_state(state)


# ---------------------------------------------------------------------------
# Phase 3a — Automated revision cycles
# ---------------------------------------------------------------------------

def phase_revision_auto(state: dict) -> None:
    log_event(state, "Phase 3a: Automated revision cycles")

    for cycle in range(state["revision_cycle"], MAX_REVISION_CYCLES):
        state["revision_cycle"] = cycle + 1
        log_event(state, f"  Revision cycle {cycle + 1}/{MAX_REVISION_CYCLES}")

        # Adversarial edits
        log_event(state, "    Adversarial editing…")
        run_script(["adversarial_edit.py", "--all"])

        # Reader panel
        log_event(state, "    Reader panel…")
        run_script(["reader_panel.py"])

        # Full evaluation
        log_event(state, "    Full novel evaluation…")
        run_script(["evaluate.py", "--mode", "full"])

        current_score = get_revision_score()
        log_event(state, f"    Novel score: {current_score}")

        state["revision_scores"].append(current_score)
        save_state(state)
        git_commit(f"Revision cycle {cycle + 1} (score: {current_score})")

        # Plateau detection
        if len(state["revision_scores"]) >= 3:
            recent = [s for s in state["revision_scores"][-3:] if s is not None]
            if len(recent) == 3:
                delta = max(recent) - min(recent)
                if delta < REVISION_PLATEAU_DELTA:
                    log_event(state, f"  Score plateau detected (delta {delta:.2f} < {REVISION_PLATEAU_DELTA}) — stopping auto-revision.")
                    break


# ---------------------------------------------------------------------------
# Phase 3b — Deep review loop
# ---------------------------------------------------------------------------

def phase_revision_review(state: dict) -> None:
    log_event(state, "Phase 3b: Deep review loop (dual-expert agents)")

    for round_num in range(state["review_rounds"], REVIEW_MAX_ROUNDS):
        state["review_rounds"] = round_num + 1
        log_event(state, f"  Review round {round_num + 1}/{REVIEW_MAX_ROUNDS}")

        run_script(["review.py"])

        review = get_latest_review()
        if review:
            rating = review.get("rating")
            stop   = review.get("stop_revising", False)
            reason = review.get("stop_reason", "")
            state["last_rating"]   = rating
            state["stop_revising"] = stop
            save_state(state)

            log_event(state, f"    Rating: {rating}/5.0 | Stop: {stop} | {reason}")
            git_commit(f"Review round {round_num + 1} (rating: {rating})")

            if stop:
                log_event(state, "  Stopping condition met — revision complete.")
                break
        else:
            log_event(state, "  No review data found — continuing.")

    state["phase"] = "export"
    save_state(state)


# ---------------------------------------------------------------------------
# Phase 4 — Export
# ---------------------------------------------------------------------------

def phase_export(state: dict) -> None:
    log_event(state, "Phase 4: Export")

    files = sorted(Path("chapters").glob("*.md"))
    if not files:
        log_event(state, "  No chapter files found — skipping export.")
        return

    # Assemble manuscript.md
    parts = [f.read_text(encoding="utf-8") for f in files]
    manuscript = "\n\n---\n\n".join(parts)
    Path("manuscript.md").write_text(manuscript, encoding="utf-8")
    wc = len(manuscript.split())
    log_event(state, f"  manuscript.md written ({wc:,} words, {len(files)} chapters)")

    # Final review parse
    run_script(["review.py", "--parse"])

    git_commit(f"Export: manuscript.md ({wc:,} words)")
    state["exported"] = True
    state["phase"]    = "done"
    save_state(state)

    log_event(state, "\n  Pipeline complete!")
    log_event(state, f"  Novel: {wc:,} words across {len(files)} chapters")
    if state.get("last_rating"):
        log_event(state, f"  Final rating: {state['last_rating']}/5.0")


# ---------------------------------------------------------------------------
# Status display
# ---------------------------------------------------------------------------

def print_status(state: dict) -> None:
    print("\n" + "=" * 60)
    print("AUTONOVEL-GPT PIPELINE STATUS")
    print("=" * 60)
    print(f"Phase:             {state['phase']}")
    print(f"Created:           {state.get('created', 'n/a')}")
    print(f"Updated:           {state.get('updated', 'n/a')}")
    print(f"Foundation score:  {state.get('foundation_score', 'n/a')}")
    print(f"Lore score:        {state.get('lore_score', 'n/a')}")
    drafted = state.get("chapters_drafted", [])
    print(f"Chapters drafted:  {len(drafted)} ({drafted})")
    scores = state.get("chapter_scores", {})
    if scores:
        avg = sum(float(v) for v in scores.values()) / len(scores)
        print(f"Avg chapter score: {avg:.2f}")
    print(f"Revision cycles:   {state.get('revision_cycle', 0)}")
    print(f"Review rounds:     {state.get('review_rounds', 0)}")
    print(f"Last rating:       {state.get('last_rating', 'n/a')}")
    print(f"Stop revising:     {state.get('stop_revising', False)}")
    print(f"Exported:          {state.get('exported', False)}")
    if state.get("log"):
        print(f"\nLast 5 events:")
        for entry in state["log"][-5:]:
            print(f"  {entry}")
    print()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

PHASES = {
    "setup":      phase_setup,
    "foundation": phase_foundation,
    "draft":      phase_draft,
    "revision":   lambda s: (phase_revision_auto(s), phase_revision_review(s)),
    "export":     phase_export,
}

PHASE_ORDER = ["setup", "foundation", "draft", "revision", "export"]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Autonovel-GPT master pipeline orchestrator"
    )
    parser.add_argument("--phase",
                        choices=["foundation", "draft", "revision", "export"],
                        help="Run only this specific phase")
    parser.add_argument("--resume", action="store_true",
                        help="Resume from last saved state")
    parser.add_argument("--status", action="store_true",
                        help="Print current state and exit")
    parser.add_argument("--reset", action="store_true",
                        help="Delete state.json and start fresh (WARNING: destructive)")
    args = parser.parse_args()

    if args.reset:
        if Path(STATE_FILE).exists():
            Path(STATE_FILE).unlink()
            print("state.json deleted. Starting fresh.")
        state = DEFAULT_STATE.copy()
        state["created"] = datetime.utcnow().isoformat()
    else:
        state = load_state()

    if args.status:
        print_status(state)
        return

    if args.phase:
        # Run a single specific phase
        fn = PHASES.get(args.phase)
        if fn:
            fn(state)
        return

    # Full pipeline — run from current phase forward
    current = state.get("phase", "setup")
    if current == "done":
        print("Pipeline already complete.  Use --reset to start over.")
        print_status(state)
        return

    start_idx = PHASE_ORDER.index(current) if current in PHASE_ORDER else 0

    for phase_name in PHASE_ORDER[start_idx:]:
        fn = PHASES.get(phase_name)
        if fn:
            fn(state)
        if state.get("phase") == "done":
            break

    print_status(state)


if __name__ == "__main__":
    main()
