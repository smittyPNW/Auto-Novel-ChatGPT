"""
Shared OpenAI client utility for autonovel-gpt.

Authentication
--------------
Supports two modes, checked in order:

1. OAuth bearer token  — set OPENAI_OAUTH_TOKEN in .env or pass the token
   at runtime via the OPENAI_OAUTH_TOKEN environment variable.  This is the
   token returned after a user completes the OpenAI OAuth 2.0 PKCE flow in
   the Herminator Dashboard web app.

2. API key fallback    — set OPENAI_API_KEY in .env for direct (non-OAuth)
   usage (local dev, CI, etc.).

The OpenAI REST API accepts both in the same Authorization: Bearer <token>
header, so no code-path changes are needed between the two modes.

Models
------
WRITER_MODEL  = gpt-4o   (creative writing,  temp 0.85)
JUDGE_MODEL   = gpt-4o   (scoring / JSON,    temp 0.2)
DEEP_MODEL    = o1       (dual-expert review, full-manuscript reasoning)
EDITOR_MODEL  = gpt-4o   (adversarial edit,  temp 0.3)

Context limits
--------------
gpt-4o  → 128 000 tokens  (~91 000 words)
o1      → 200 000 tokens  (~143 000 words)
"""

from __future__ import annotations

import os
import time
import textwrap
import re
from typing import Optional

from dotenv import load_dotenv
from openai import OpenAI, OpenAIError

load_dotenv()


# ---------------------------------------------------------------------------
# OAuth / API-key resolution
# ---------------------------------------------------------------------------

def _resolve_auth() -> tuple[str, str | None]:
    """
    Returns (api_key_or_token, base_url).

    Priority:
      1. OPENAI_OAUTH_TOKEN  — user OAuth bearer token from the dashboard
      2. OPENAI_API_KEY      — static API key for local / CI use
    """
    oauth_token = os.getenv("OPENAI_OAUTH_TOKEN", "").strip()
    if oauth_token:
        # OAuth tokens issued by OpenAI's authorization server are accepted
        # by the API exactly like API keys.
        base_url = os.getenv("OPENAI_BASE_URL", None)  # None → default
        return oauth_token, base_url

    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if api_key:
        base_url = os.getenv("OPENAI_BASE_URL", None)
        return api_key, base_url

    raise RuntimeError(
        "No OpenAI credentials found.  Set OPENAI_OAUTH_TOKEN (preferred, "
        "via the dashboard OAuth flow) or OPENAI_API_KEY in your .env file."
    )


# ---------------------------------------------------------------------------
# Client factory — call this instead of caching a module-level singleton so
# that OAuth token rotations are picked up without restarting the process.
# ---------------------------------------------------------------------------

def get_client() -> OpenAI:
    token, base_url = _resolve_auth()
    kwargs: dict = {"api_key": token}
    if base_url:
        kwargs["base_url"] = base_url
    return OpenAI(**kwargs)


# ---------------------------------------------------------------------------
# Model name constants (override via env vars)
# ---------------------------------------------------------------------------

WRITER_MODEL = os.getenv("WRITER_MODEL", "gpt-4o")
JUDGE_MODEL  = os.getenv("JUDGE_MODEL",  "gpt-4o")
DEEP_MODEL   = os.getenv("DEEP_MODEL",   "o1")
EDITOR_MODEL = os.getenv("EDITOR_MODEL", "gpt-4o")

# Conservative word budgets (leaving headroom for the response)
GPT4O_CTX_WORDS = 70_000
O1_CTX_WORDS    = 120_000


# ---------------------------------------------------------------------------
# Core call helpers
# ---------------------------------------------------------------------------

def call_gpt(
    system: str,
    user: str,
    model: str = WRITER_MODEL,
    temperature: float = 0.85,
    max_tokens: int = 16_000,
    retries: int = 4,
) -> str:
    """
    Call a standard GPT-4o-family model (supports temperature).
    Returns the assistant's text content.
    """
    client = get_client()
    messages = [
        {"role": "system", "content": system},
        {"role": "user",   "content": user},
    ]
    backoff = 2
    last_err: Exception | None = None
    for attempt in range(retries):
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            return resp.choices[0].message.content or ""
        except OpenAIError as exc:
            last_err = exc
            print(f"[llm_client] OpenAI error (attempt {attempt+1}/{retries}): {exc}")
            if attempt < retries - 1:
                time.sleep(backoff)
                backoff *= 2
    raise RuntimeError(f"call_gpt failed after {retries} attempts: {last_err}")


def call_o1(
    developer_prompt: str,
    user: str,
    model: str = DEEP_MODEL,
    max_completion_tokens: int = 16_000,
    retries: int = 4,
) -> str:
    """
    Call an o1-family reasoning model.

    o1 models:
      - Do NOT support the 'system' role → use 'developer' role instead.
      - Do NOT accept a temperature parameter.
      - Use max_completion_tokens (not max_tokens).

    Returns the assistant's text content.
    """
    client = get_client()
    messages = [
        {"role": "developer", "content": developer_prompt},
        {"role": "user",      "content": user},
    ]
    backoff = 2
    last_err: Exception | None = None
    for attempt in range(retries):
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=messages,
                max_completion_tokens=max_completion_tokens,
            )
            return resp.choices[0].message.content or ""
        except OpenAIError as exc:
            last_err = exc
            print(f"[llm_client] OpenAI error (attempt {attempt+1}/{retries}): {exc}")
            if attempt < retries - 1:
                time.sleep(backoff)
                backoff *= 2
    raise RuntimeError(f"call_o1 failed after {retries} attempts: {last_err}")


# ---------------------------------------------------------------------------
# Manuscript helpers
# ---------------------------------------------------------------------------

def word_count(text: str) -> int:
    return len(text.split())


def chunk_manuscript(text: str, max_words: int = GPT4O_CTX_WORDS) -> list[str]:
    """
    Split a manuscript into word-budget chunks, preferring chapter boundaries.
    """
    if word_count(text) <= max_words:
        return [text]

    chapters = re.split(r"(?m)^(?=# Chapter)", text)
    chunks: list[str] = []
    current = ""
    for ch in chapters:
        if word_count(current + ch) > max_words:
            if current:
                chunks.append(current.strip())
            current = ch
        else:
            current += ch
    if current.strip():
        chunks.append(current.strip())
    return chunks or [text]


def summarise_manuscript(text: str, title: str = "the novel") -> str:
    """
    Produce a compact chapter-by-chapter summary for manuscripts that exceed
    the context window.  Used by review.py when full text > O1_CTX_WORDS.
    """
    system = textwrap.dedent("""\
        You are a professional literary analyst.
        Produce a dense, accurate chapter-by-chapter synopsis.
        For each chapter write:
          Chapter N [title if present]: 3-5 sentences covering plot events,
          character development, and any significant craft choices.
        Do not editorialize.  Do not pad.  Be specific and complete.
    """)
    user = f"Summarise every chapter of {title}:\n\n{text}"
    return call_gpt(system, user, model=JUDGE_MODEL, temperature=0.2, max_tokens=8_000)
