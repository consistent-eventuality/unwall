"""Publish live, end to end, against a local stand-in host and a fake IndexNow endpoint.

No network calls leave this machine and no AI models are called.
"""
from __future__ import annotations

import httpx

from conftest import make_job, settle


def test_publish_live(world):
    client, base, data, sent = world["client"], world["base"], world["data"], world["submissions"]
    real, sample, second = "a" * 12, "b" * 12, "c" * 12
    make_job(data, real)
    make_job(data, sample, sample="demo", question="Sample question?")
    make_job(data, second, question="Second real page?")

    assert client.post(f"/api/jobs/{real}/publish", json={"consent": False}).status_code == 422
    unsafe = client.get(f"/api/jobs/{real}").json()["meta"]
    client.patch(f"/api/jobs/{real}", json={"meta": {**unsafe, "source_url": 'https://x.com/a"b'}})
    assert "can't contain" in client.post(f"/api/jobs/{real}/publish", json={"consent": True}).json()["detail"]
    client.patch(f"/api/jobs/{real}", json={"meta": unsafe})

    # A failed deploy is reported as failed and never recorded as live.
    (world["tmp"] / "fail_deploy").touch()
    client.post(f"/api/jobs/{real}/publish", json={"consent": True})
    job = settle(client, real)
    assert job["launch"]["state"] == "failed" and not sent
    assert not (data / "live" / job["slug"]).exists()

    (world["tmp"] / "fail_deploy").unlink()
    assert client.post(f"/api/jobs/{real}/launch").status_code == 200
    job = settle(client, real)
    assert job["launch"]["state"] == "live"
    url = job["launch"]["url"]
    html = httpx.get(url).text
    assert f'rel="canonical" href="{url}"' in html
    assert "8799" not in html and 'content="noindex"' not in html
    # Visible links count clicks through /go; structured data keeps the direct post URL.
    assert f'href="/go/{job["slug"]}"' in html and '"url": "https://instagram.com/reel/x"' in html
    assert f'redir /go/{job["slug"]} "https://instagram.com/reel/x" 302' in (world["tmp"] / "Caddyfile").read_text()
    local = client.get(f"/go/{job['slug']}", follow_redirects=False)
    assert local.status_code == 302 and local.headers["location"] == "https://instagram.com/reel/x"

    # The studio's preview tabs show the public copy, not the local one.
    assert f"Canonical page: {url}" in client.get(f"/api/jobs/{real}/output/page.md").text

    key = (data / "indexnow.key").read_text()
    assert httpx.get(f"{base}/{key}.txt").text == key
    assert url in httpx.get(f"{base}/sitemap.xml").text
    assert f"Sitemap: {base}/sitemap.xml" in httpx.get(f"{base}/robots.txt").text
    assert sent[-1] == {"host": "127.0.0.1", "key": key, "keyLocation": f"{base}/{key}.txt", "urlList": [url]}

    # Only static pages are public: no API, videos, or job files.
    assert httpx.get(f"{base}/api/jobs/{real}").status_code == 404
    assert not list(world["public"].rglob("*.mp4")) and not list(world["public"].rglob("job.json"))

    # Sample pages go live as noindex and are never submitted.
    count = len(sent)
    client.post(f"/api/jobs/{sample}/publish", json={"consent": True})
    demo = settle(client, sample)
    assert demo["launch"]["state"] == "live" and len(sent) == count
    assert all(t["status"] == "skipped" for t in demo["launch"]["targets"])
    assert 'content="noindex"' in httpx.get(demo["launch"]["url"]).text
    assert f'/p/{demo["slug"]}/* X-Robots-Tag' in (world["tmp"] / "Caddyfile").read_text()
    assert demo["slug"] not in httpx.get(f"{base}/sitemap.xml").text
    assert demo["slug"] not in httpx.get(f"{base}/").text
    assert httpx.get(url).status_code == 200  # Earlier pages survive later deploys.

    # A rejected key is shown as a failed submission; the page itself stays live.
    world["codes"].extend([403, 403, 403])
    client.post(f"/api/jobs/{second}/publish", json={"consent": True})
    other = settle(client, second)
    assert other["launch"]["state"] == "live" and other["launch"]["targets"][0]["status"] == "failed"
    world["codes"].append(202)
    client.post(f"/api/jobs/{second}/launch")
    other = settle(client, second)
    assert other["launch"]["targets"][0]["status"] == "received"
    assert all(t["status"] == "discoverable" for t in other["launch"]["targets"][1:])

    # An edit that hasn't been re-approved never ships with someone else's deploy.
    client.patch(f"/api/jobs/{real}", json={"draft": {**job["draft"], "answer": "UNAPPROVED EDIT"}})
    client.post(f"/api/jobs/{second}/launch")
    settle(client, second)
    assert "UNAPPROVED EDIT" not in httpx.get(url).text
