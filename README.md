# Auto-Novel-ChatGPT

**An autonomous novel-writing pipeline powered by GPT-4o and o1.**

Adapted from [NousResearch/autonovel](https://github.com/NousResearch/autonovel) — replaces the Anthropic/Claude backend with OpenAI's API, re-engineers every prompt for GPT-4o's strengths, and adds a dual-expert review system that knows when to stop revising.

---

## How It Works

The pipeline writes a complete 70-90k word novel in four phases:

| Phase | What happens | Models used |
|-------|-------------|-------------|
| **0. Seed** | Generate or provide a high-concept premise | gpt-4o (temp 1.0) |
| **1. Foundation** | World-building, characters, outline, voice, canon | gpt-4o (temp 0.75) |
| **2. First Draft** | Sequential chapter writing with per-chapter evaluation | gpt-4o (temp 0.85) |
| **3. Revision** | Adversarial edits, reader panel, dual-expert deep review | gpt-4o + o1 |
| **4. Export** | Manuscript assembly | -- |

State is persisted to `state.json` so the pipeline can resume after interruption. Every accepted result is committed to git.

## Quick Start

```bash
git clone https://github.com/smittyPNW/Auto-Novel-ChatGPT.git
cd Auto-Novel-ChatGPT

pip install -r requirements.txt
cp .env.example .env
# Edit .env — add your OPENAI_API_KEY or OPENAI_OAUTH_TOKEN
```

**Generate a seed and run the full pipeline:**

```bash
python seed.py                          # generate 10 seed concepts
python seed.py --pick 3                 # write seed 3 to seed.txt
python run_pipeline.py                  # run full pipeline
```

**Or run phases individually:**

```bash
# Phase 1 — Foundation
python gen_world.py > world.md
python gen_characters.py > characters.md

# Phase 2 — Draft
python draft_chapter.py --chapter 1
python evaluate.py --mode chapter --chapter 1

# Phase 3 — Revision
python adversarial_edit.py --chapter 1
python review.py --output review.md
```

**Resume after interruption:**

```bash
python run_pipeline.py --resume
python run_pipeline.py --status         # check current state
```

## Architecture

```
seed.py               # Generate novel seed concepts (temp 1.0)
gen_world.py          # Phase 1: Generate world.md from seed
gen_characters.py     # Phase 1: Generate characters.md
draft_chapter.py      # Phase 2: Write chapters (gpt-4o, temp 0.85)
evaluate.py           # Phase 2/3: Score foundation, chapters, full novel
adversarial_edit.py   # Phase 3: Find ~500 words to cut per chapter
reader_panel.py       # Phase 3: Four-persona reader evaluation panel
review.py             # Phase 3: Dual-expert deep review
run_pipeline.py       # Master orchestrator — runs all phases
llm_client.py         # Shared OpenAI client (OAuth + API key)
requirements.txt
.env.example
```

## The Dual-Expert Review System

The core innovation of the revision phase. Two fully-committed AI personas analyse the manuscript independently:

### Margaret Holloway — Literary Critic
- **Background:** 25-year newspaper critic (The Atlantic, NYRB, The Guardian)
- **Produces:** 600-900 word book review with star rating (1-5, half-star increments)
- **Model:** o1 for full manuscripts, gpt-4o for chapters
- **Temperature:** 0.5
- **Evaluates:** opening strength, prose quality, pacing, world-building, character depth, emotional resonance

### Dr. James Whitfield — Professor of Fiction
- **Background:** MFA director and developmental editor (30 years, 60+ published novels)
- **Produces:** 8-15 numbered craft notes with severity (MAJOR/MODERATE/MINOR) and fix type
- **Model:** o1 for full manuscripts, gpt-4o for chapters
- **Temperature:** 0.1
- **Fix types:** STRUCTURAL, CHARACTER, PACING, PROSE, COMPRESSION, ADDITION, CONTINUITY

### When Revision Stops

The loop terminates when any of these conditions are met:

- Rating >= 4.5 with zero MAJOR items
- Rating >= 4.0 with >50% of items qualified/hedged
- <= 2 total items remaining (noise floor)

## Four-Persona Reader Panel

Before the deep review, a panel of four simulated readers evaluates the manuscript from different angles:

| Reader | Role | Reads for |
|--------|------|-----------|
| **Judith Crane** | Senior acquisitions editor, 25 years | Prose texture, voice consistency, sentence-level craft |
| **Marcus Webb** | Genre reader, 3-4 novels/month for 20 years | Momentum, worldbuilding payoff, narrative satisfaction |
| **Priya Nair** | Published literary fiction author and MFA professor | Architecture, economy, foreshadowing, scene function |
| **Fourth reader** | General audience perspective | Engagement, clarity, emotional connection |

## Model Mapping (Claude to GPT-4o)

| Original (Claude) | This fork (OpenAI) | Rationale |
|---|---|---|
| claude-sonnet-4-6 | gpt-4o (temp 0.85) | Creative writing, world-building |
| claude-opus-4-6 (reviewer) | o1 | Long-form reasoning, manuscript coherence |
| claude-opus-4-6 (judge) | gpt-4o (temp 0.1-0.2) | Evaluation, JSON extraction |
| Anthropic beta (1M ctx) | o1 (200k) + chunking | Context limit handling |

## Authentication

**API Key (simplest):**
```env
OPENAI_API_KEY=sk-...
```

**OAuth Token (for Herminator Dashboard integration):**
```env
OPENAI_OAUTH_TOKEN=<token from dashboard>
```

Configure in `.env` (copy `.env.example`).

## Prompt Engineering Decisions

1. **Committed personas** — Named professional identities ("Margaret Holloway, senior critic at The Atlantic") outperform abstract role descriptions with GPT-4o
2. **Calibrated scoring anchors** — Explicit number-to-meaning mappings prevent GPT-4o's default score inflation
3. **Anti-pattern lists in system prompts** — Banned patterns named up-front, not after-the-fact
4. **Temperature by task** — Writer: 0.85 | World-builder: 0.75 | Critic: 0.5 | Judge: 0.2 | Editor: 0.3
5. **o1 for deep review** — Extended reasoning chains track long-range narrative coherence better than gpt-4o
6. **JSON schemas demonstrated, not described** — Complete example schemas in every JSON-output prompt

## Context Window Notes

- **gpt-4o:** 128k tokens (~91k words usable with response headroom)
- **o1:** 200k tokens (~143k words usable)
- Manuscripts exceeding limits are automatically summarised by `llm_client.summarise_manuscript()` before review

## Credits

Based on [NousResearch/autonovel](https://github.com/NousResearch/autonovel). Adapted for OpenAI models with re-engineered prompts, the dual-expert review system, reader panel, seed generator, and full pipeline orchestrator.

## License

[MIT](./LICENSE)
