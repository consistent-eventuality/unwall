"""Render an approved job into static, AI-readable files."""
from __future__ import annotations

import json
import re
import shutil
from html import escape
from pathlib import Path


def stamp(seconds: float) -> str:
    whole = int(seconds)
    return f"{whole // 60}:{whole % 60:02d}"


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:70].strip("-") or "answer"


def credit_parts(job: dict) -> list[str]:
    meta = job["meta"]
    parts = []
    if meta.get("followers"):
        parts.append(f"{meta['followers']} followers on {meta.get('platform') or 'social'}")
    if meta.get("views"):
        parts.append(f"This video has {meta['views']} views")
    if parts and meta.get("stats_date"):
        parts.append(f"Stats from {meta['stats_date']}")
    return parts


NOINDEX = '<meta name="robots" content="noindex">'


def is_noindex(page: Path) -> bool:
    return NOINDEX in page.read_text(encoding="utf-8")


def json_ld(job: dict, url: str, thumb: str) -> str:
    draft, meta = job["draft"], job["meta"]
    person = {"@type": "Person", "name": meta["creator"]}
    if meta.get("credential"):
        person["description"] = meta["credential"]
    if meta.get("profile_url"):
        person["sameAs"] = [meta["profile_url"]]
    clips = [{
        "@type": "Clip", "name": c["text"], "startOffset": int(c["start_s"]),
        "endOffset": max(int(c["end_s"]), int(c["start_s"]) + 1), "url": meta["source_url"],
    } for c in draft["claims"]]
    video = {
        "@type": "VideoObject", "name": draft["title"], "description": draft["answer"], "thumbnailUrl": thumb,
        "uploadDate": meta["posted_date"], "duration": f"PT{int(round(job['duration']))}S",
        "url": meta["source_url"], "creator": person, "hasPart": clips,
    }
    if str(meta.get("views_count") or "").isdigit():
        video["interactionStatistic"] = {
            "@type": "InteractionCounter", "interactionType": "https://schema.org/WatchAction",
            "userInteractionCount": int(meta["views_count"]),
        }
    question = {
        "@type": "Question", "name": draft["question"],
        "acceptedAnswer": {"@type": "Answer", "text": draft["answer"], "author": person, "url": url},
    }
    graph = {"@context": "https://schema.org", "@graph": [video, question]}
    # A literal "</script>" in creator text must not be able to end this block.
    return json.dumps(graph, ensure_ascii=False, indent=2).replace("<", "\\u003c")


def markdown(job: dict, url: str) -> str:
    draft, meta = job["draft"], job["meta"]
    out = [f"# {draft['question']}", "", draft["answer"], "",
           f"Source: video by {meta['creator']}" + (f", {meta['credential']}" if meta.get("credential") else ""),
           f"Original video: {meta['source_url']}", f"Canonical page: {url}",
           f"Reviewed and approved for publication on {job['approved_at']}."]
    parts = credit_parts(job)
    if parts:
        out.append(". ".join(parts) + ".")
    out += ["", "## What the video shows", ""]
    for claim in draft["claims"]:
        out.append(f"- [{stamp(claim['start_s'])}-{stamp(claim['end_s'])}] {claim['text']}")
    out += ["", f"Watch the original: {meta['source_url']}", ""]
    return "\n".join(out)


PAGE_CSS = """
:root{color-scheme:light dark;--bg:#f6f4ec;--ink:#171815;--soft:#62645c;--line:#d9d6ca;--mark:#315c2b}
@media(prefers-color-scheme:dark){:root{--bg:#121310;--ink:#f1efe6;--soft:#a3a498;--line:#2d2f29;--mark:#dcef78}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:17px/1.6 ui-sans-serif,system-ui,sans-serif}
main{max-width:720px;margin:auto;padding:56px 24px 72px}h1{font:500 clamp(32px,6vw,46px)/1.1 Georgia,serif;letter-spacing:-.02em;margin:0 0 22px}
.answer{font-size:20px;line-height:1.55}.credit{border-block:1px solid var(--line);margin:30px 0;padding:16px 0;color:var(--soft);font-size:15px}.credit b{color:var(--ink)}
.watch{display:inline-block;margin-top:10px;color:var(--mark);font-weight:650}h2{font-size:14px;letter-spacing:.08em;text-transform:uppercase;margin:38px 0 8px}
ol{list-style:none;padding:0;margin:0}li{display:grid;grid-template-columns:96px 1fr;gap:14px;padding:13px 0;border-bottom:1px solid var(--line)}
time{font:13px/1.9 ui-monospace,monospace;color:var(--soft)}footer{margin-top:30px;color:var(--soft);font-size:14px}a{color:inherit}
"""


def page_html(job: dict, url: str, thumb: str) -> str:
    draft, meta = job["draft"], job["meta"]
    source = escape(meta["source_url"], quote=True)
    who = f"<b>By {escape(meta['creator'])}</b>"
    if meta.get("credential"):
        who += f", {escape(meta['credential'])}"
    parts = credit_parts(job)
    stats = f"<br>{escape(' · '.join(parts))}" if parts else ""
    items = "".join(
        f"<li><time>{stamp(c['start_s'])}–{stamp(c['end_s'])}</time><span>{escape(c['text'])}</span></li>"
        for c in draft["claims"])
    reason = escape(draft.get("watch_reason") or "See the full technique in the original video.")
    description = escape(draft["answer"][:155], quote=True)
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{escape(draft['question'])} | {escape(meta['creator'])}</title><meta name="description" content="{description}">
{NOINDEX if job.get('sample') else ''}<meta name="author" content="{escape(meta['creator'], quote=True)}"><link rel="canonical" href="{escape(url, quote=True)}">
<link rel="alternate" type="text/markdown" href="{escape(url, quote=True)}page.md"><meta property="og:title" content="{escape(draft['question'], quote=True)}">
<meta property="og:description" content="{description}"><meta property="og:image" content="{escape(thumb, quote=True)}"><meta property="og:type" content="article">
<script type="application/ld+json">{json_ld(job, url, thumb)}</script><style>{PAGE_CSS}</style></head><body><main><article>
<h1>{escape(draft['question'])}</h1><p class="answer">{escape(draft['answer'])}</p>
<p class="credit">{who}{stats}<br><a class="watch" rel="noopener" href="{source}">Watch the original video →</a></p>
<h2>What the video shows</h2><ol>{items}</ol><p>{reason}</p>
<footer>Reviewed and approved for publication on {escape(job['approved_at'])}. <a href="{source}" rel="noopener">View the source</a>.</footer>
</article></main></body></html>"""


def publish(job: dict, job_dir: Path, site: Path, base_url: str) -> str:
    slug = job["slug"]
    url = f"{base_url}/p/{slug}/"
    target = site / slug
    target.mkdir(parents=True, exist_ok=True)
    frames = job["frames"]
    thumb_source = job_dir / "frames" / frames[min(1, len(frames) - 1)]["file"]
    shutil.copyfile(thumb_source, target / "thumb.jpg")
    thumb = f"{url}thumb.jpg"
    (target / "index.html").write_text(page_html(job, url, thumb), encoding="utf-8")
    (target / "page.md").write_text(markdown(job, url), encoding="utf-8")
    claims = {"question": job["draft"]["question"], "answer": job["draft"]["answer"],
              "creator": job["meta"]["creator"], "source_url": job["meta"]["source_url"],
              "approved_at": job["approved_at"], "claims": job["draft"]["claims"]}
    (target / "claims.json").write_text(json.dumps(claims, ensure_ascii=False, indent=2), encoding="utf-8")
    return url


def sitemap(site: Path, base_url: str) -> str:
    urls = "".join(f"<url><loc>{escape(base_url)}/p/{p.parent.name}/</loc></url>" for p in sorted(site.glob("*/index.html")) if not is_noindex(p))
    return f'<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{urls}</urlset>'


def robots(base_url: str) -> str:
    agents = ["OAI-SearchBot", "ChatGPT-User", "Claude-SearchBot", "Claude-User", "PerplexityBot", "Googlebot", "Bingbot"]
    rules = "\n\n".join(f"User-agent: {a}\nAllow: /p/" for a in agents)
    return f"{rules}\n\nUser-agent: *\nDisallow: /api/\n\nSitemap: {base_url}/sitemap.xml\n"
