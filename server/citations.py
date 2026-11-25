"""Ask AI engines the questions a page should answer, and record what they cite.

A baseline runs right after a page goes live, before any index has it. Weekly runs repeat the
same questions so changes can be read against that baseline.
"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

import httpx

from .evidence import STORE_LOCK, measure_dir, read_jsonl

CLAUDE_MODEL = "claude-opus-5-5"
OPENAI_MODEL = os.environ.get("UNWALL_OPENAI_SEARCH_MODEL", "gpt-5")
PERPLEXITY_MODEL = "sonar"
MAX_QUESTIONS = 5
WEEK = 7 * 86400

Answer = dict  # {"text": str, "cited": [url], "consulted": [url]}

# Engines often answer from memory, and then nothing can be cited. Every check asks them to
# search first, so a run measures "when it searches, does it find and cite this page?".
# Whether a search actually happened is recorded on each answer.
SEARCH_FIRST = "Search the web before you answer, and cite the pages you use."


class Skipped(Exception):
    """The engine can't run here, for example because its key isn't set."""


# ---------- Engines ----------

def walk(node, found: Callable[[dict], None]) -> None:
    """Visit every dict in a nested response, so parsing survives small shape changes."""
    if isinstance(node, dict):
        found(node)
        for value in node.values():
            walk(value, found)
    elif isinstance(node, list):
        for value in node:
            walk(value, found)


def ask_chatgpt(question: str) -> Answer:
    if not os.environ.get("OPENAI_API_KEY"):
        raise Skipped("OPENAI_API_KEY isn't set.")
    from openai import OpenAI
    response = OpenAI().responses.create(
        model=OPENAI_MODEL, input=question, instructions=SEARCH_FIRST, tools=[{"type": "web_search"}],
        tool_choice="required", include=["web_search_call.action.sources"],
    )
    cited, consulted = [], []

    def found(node: dict) -> None:
        if node.get("type") == "url_citation" and node.get("url"):
            cited.append(node["url"])
        elif node.get("type") == "web_search_call":
            for source in (node.get("action") or {}).get("sources") or []:
                if isinstance(source, dict) and source.get("url"):
                    consulted.append(source["url"])

    walk(response.model_dump(), found)
    return {"text": response.output_text or "", "cited": cited, "consulted": consulted}


def ask_claude(question: str) -> Answer:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise Skipped("ANTHROPIC_API_KEY isn't set.")
    import anthropic
    client = anthropic.Anthropic()
    # The basic tool version attaches citations to the answer text. The newer dynamic-filtering
    # version routes results through code execution and returns answers without citations.
    tools = [{"type": "web_search_20250305", "name": "web_search", "max_uses": 5}]
    messages: list[dict] = [{"role": "user", "content": question}]
    blocks: list = []
    for _ in range(4):  # A long server-side search can pause; send it back to continue.
        # This model rejects a forced tool_choice, so the instruction asks for the search instead.
        message = client.messages.create(model=CLAUDE_MODEL, max_tokens=4000, tools=tools, messages=messages,
                                         system=SEARCH_FIRST, output_config={"effort": "low"})
        blocks.extend(message.content)
        if message.stop_reason != "pause_turn":
            break
        messages = [messages[0], {"role": "assistant", "content": message.content}]
    cited, consulted, text = [], [], []

    def found(node: dict) -> None:
        kind = node.get("type")
        if kind == "web_search_result" and node.get("url"):
            consulted.append(node["url"])
        elif kind == "web_search_result_location" and node.get("url"):
            cited.append(node["url"])

    for block in blocks:
        data = block.model_dump()
        if data.get("type") == "text":
            text.append(data.get("text") or "")
        walk(data, found)
    return {"text": "".join(text), "cited": cited, "consulted": consulted}


def ask_perplexity(question: str) -> Answer:
    key = os.environ.get("PERPLEXITY_API_KEY")
    if not key:
        raise Skipped("PERPLEXITY_API_KEY isn't set.")
    response = httpx.post(
        "https://api.perplexity.ai/chat/completions", timeout=90,
        headers={"Authorization": f"Bearer {key}"},
        json={"model": PERPLEXITY_MODEL, "messages": [{"role": "system", "content": SEARCH_FIRST},
                                                      {"role": "user", "content": question}]},
    )
    response.raise_for_status()
    body = response.json()
    cited = [u for u in body.get("citations") or [] if isinstance(u, str)]
    consulted = [r["url"] for r in body.get("search_results") or [] if isinstance(r, dict) and r.get("url")]
    text = body["choices"][0]["message"]["content"] if body.get("choices") else ""
    return {"text": text, "cited": cited, "consulted": consulted}


ENGINES: dict[str, Callable[[str], Answer]] = {"ChatGPT": ask_chatgpt, "Claude": ask_claude, "Perplexity": ask_perplexity}


# ---------- Matching ----------

def normalize(url: str) -> str:
    """Compare links by host and path only: no scheme, www, query, fragment, or trailing slash."""
    parsed = urlparse(url.strip())
    host = parsed.netloc.lower().removeprefix("www.").removeprefix("m.")
    return f"{host}{parsed.path.rstrip('/')}".lower()


def matches(urls: list[str], target: str) -> bool:
    if not target:
        return False
    goal = normalize(target)
    return any(normalize(u) == goal or normalize(u).startswith(goal + "/") for u in urls)


def score(answer: Answer, target: dict) -> dict:
    name = (target.get("creator") or "").strip()
    return {
        "searched": bool(answer["cited"] or answer["consulted"]),
        "page_cited": matches(answer["cited"], target["page_url"]),
        "page_found": matches(answer["cited"] + answer["consulted"], target["page_url"]),
        "post_cited": matches(answer["cited"], target["post_url"]),
        "post_found": matches(answer["cited"] + answer["consulted"], target["post_url"]),
        "named": len(name) >= 3 and name.lower() in answer["text"].lower(),
    }


# ---------- Runs ----------

def questions(job: dict) -> list[str]:
    draft = job.get("draft") or {}
    out: list[str] = []
    for q in [draft.get("question", ""), *(draft.get("related_questions") or [])]:
        q = " ".join(str(q).split())
        if q and q.lower() not in {x.lower() for x in out}:
            out.append(q)
    return out[:MAX_QUESTIONS]


def target(job: dict) -> dict:
    launch = job.get("launch") or {}
    return {"slug": job["slug"], "page_url": launch.get("url") or job.get("url") or "",
            "post_url": job["meta"].get("source_url", ""), "creator": job["meta"].get("creator", "")}


def runs_for(data: Path, slug: str) -> list[dict]:
    return [r for r in read_jsonl(measure_dir(data) / "runs.jsonl") if r["slug"] == slug]


def run(job: dict, data: Path, kind: str, engines: dict[str, Callable[[str], Answer]] | None = None) -> dict:
    """Ask every tracked question on every engine and append one record per answer."""
    engines = engines or ENGINES
    goal = target(job)
    run_id = uuid.uuid4().hex[:12]
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    records = []
    for question in questions(job):
        for engine, ask in engines.items():
            record = {"run_id": run_id, "kind": kind, "at": started, "slug": goal["slug"], "question": question,
                      "engine": engine, "status": "ok", "error": None, "cited": [], "consulted": []}
            try:
                answer = ask(question)
                record.update(score(answer, goal), cited=answer["cited"][:30], consulted=answer["consulted"][:30],
                              answer=answer["text"][:1500])
            except Skipped as exc:
                record.update(status="skipped", error=str(exc))
            except Exception as exc:  # One engine failing must not lose the others' answers.
                record.update(status="error", error=f"{type(exc).__name__}: {str(exc)[:300]}")
            records.append(record)
    path = measure_dir(data) / "runs.jsonl"
    with STORE_LOCK:
        with path.open("a") as out:
            out.writelines(json.dumps(r) + "\n" for r in records)
    return {"run_id": run_id, "answers": sum(r["status"] == "ok" for r in records),
            "skipped": sum(r["status"] == "skipped" for r in records), "errors": sum(r["status"] == "error" for r in records)}


def last_run_at(data: Path, slug: str) -> float | None:
    stamps = [datetime.fromisoformat(r["at"]).timestamp() for r in runs_for(data, slug) if r["status"] == "ok"]
    return max(stamps) if stamps else None


def due(job: dict, data: Path, now: float | None = None) -> bool:
    """Weekly checks run on live, non-sample pages at most once every six days."""
    if (job.get("launch") or {}).get("state") != "live" or job.get("sample"):
        return False
    last = last_run_at(data, job["slug"])
    return last is None or (now or time.time()) - last > 6 * 86400


# Pages with a check in progress, so the app doesn't start two at once.
ACTIVE: set[str] = set()
ACTIVE_LOCK = threading.Lock()


def start_background(job: dict, data: Path, kind: str, engines: dict | None = None) -> bool:
    with ACTIVE_LOCK:
        if job["slug"] in ACTIVE:
            return False
        ACTIVE.add(job["slug"])

    def work() -> None:
        try:
            run(job, data, kind, engines)
        finally:
            with ACTIVE_LOCK:
                ACTIVE.discard(job["slug"])

    threading.Thread(target=work, daemon=True).start()
    return True
