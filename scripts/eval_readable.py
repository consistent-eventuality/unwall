"""Level 1 test: can an AI answer questions about a video from its published page alone?

Each model sees only the page's visible text. Answers are graded by required facts,
so a pass can't come from a lenient judge. Bar: 13 of 15 per model.
"""
from __future__ import annotations

import json
import re
import sys
from html.parser import HTMLParser
from pathlib import Path

import anthropic
import httpx
from openai import OpenAI

ROOT = Path(__file__).resolve().parents[1]
CLAUDE = "claude-opus-5-5"
GPT = sys.argv[1] if len(sys.argv) > 1 else "gpt-5"

# Every inner list is "any of these"; all inner lists must match.
QUESTIONS = {
    "narrated": [
        ("Why do white midsoles turn yellow?", [["oxidiz", "oxidation"]]),
        ("Where should sneakers not be stored?", [["windowsill"]]),
        ("What should sneakers be stuffed with?", [["acid-free", "acid free"]]),
        ("Why avoid newspaper?", [["ink"]]),
        ("What should be used instead of chlorine bleach?", [["soap"], ["brush"]]),
    ],
    "onscreen": [
        ("How long should wet suede air dry?", [["24"]]),
        ("What kind of brush is used?", [["crepe"]]),
        ("What goes on the cloth?", [["vinegar"]]),
        ("How long is the steaming step?", [["10"]]),
        ("How far away should the kettle be held?", [["15"]]),
    ],
    "demo": [
        ("What gets removed before cleaning?", [["lace"], ["insole"]]),
        ("What is the soap to water ratio?", [["1 tsp", "1 teaspoon", "one teaspoon"], ["1 cup", "one cup"]]),
        ("Which direction do you work across the shoe?", [["toe"], ["heel"]]),
        ("What do you blot with?", [["microfiber"]]),
        ("How long should they dry?", [["12"]]),
    ],
}


class Text(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        self.skip += tag in {"script", "style", "head"}

    def handle_endtag(self, tag):
        self.skip -= tag in {"script", "style", "head"}

    def handle_data(self, data):
        if not self.skip and data.strip():
            self.parts.append(data.strip())


def latest_pages() -> dict[str, str]:
    pages: dict[str, tuple[float, str]] = {}
    for path in (ROOT / "data" / "jobs").glob("*/job.json"):
        job = json.loads(path.read_text())
        if job.get("sample") and job["status"] == "published":
            mtime = path.stat().st_mtime
            if job["sample"] not in pages or mtime > pages[job["sample"]][0]:
                pages[job["sample"]] = (mtime, job["url"])
    return {key: value[1] for key, value in pages.items()}


def visible_text(url: str) -> str:
    response = httpx.get(url, timeout=10)
    response.raise_for_status()
    parser = Text()
    parser.feed(response.text)
    return "\n".join(parser.parts)


def prompt(page: str, question: str) -> str:
    return f"Answer in one sentence using only this page. If it isn't stated, say 'not stated'.\n\n<page>\n{page}\n</page>\n\nQuestion: {question}"


def ask_claude(page: str, question: str) -> str:
    message = anthropic.Anthropic().messages.create(
        model=CLAUDE, max_tokens=2000, output_config={"effort": "low"},
        messages=[{"role": "user", "content": prompt(page, question)}],
    )
    return "".join(block.text for block in message.content if block.type == "text")


def ask_gpt(page: str, question: str) -> str:
    return OpenAI().responses.create(model=GPT, input=prompt(page, question)).output_text


def passes(answer: str, required: list[list[str]]) -> bool:
    low = re.sub(r"\s+", " ", answer.lower())
    return all(any(option in low for option in group) for group in required)


def main() -> int:
    pages = latest_pages()
    if set(pages) != set(QUESTIONS):
        print(f"Publish all three samples first. Found: {sorted(pages)}")
        return 2
    ok = True
    for name, ask in (("Claude", ask_claude), (GPT, ask_gpt)):
        score = 0
        for sample, questions in QUESTIONS.items():
            page = visible_text(pages[sample])
            for question, required in questions:
                answer = ask(page, question)
                hit = passes(answer, required)
                score += hit
                if not hit:
                    print(f"MISS {name} [{sample}] {question} -> {answer}")
        print(f"{name}: {score}/15")
        ok = ok and score >= 13
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
