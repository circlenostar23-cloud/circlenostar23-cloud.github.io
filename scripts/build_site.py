"""Builds the show's site from this repo's releases: the feed, and copies of the MP3s and transcripts.

The feed's enclosures point at copies on Pages, not at the release assets: release downloads redirect
to signed URLs that expire within minutes, and Apple Podcasts caches the redirect target. Safari's
player can't play the release assets either, so dnlevitz.com plays the same copies.
"""

import html
import json
import os
import re
import subprocess
from datetime import datetime
from email.utils import format_datetime
from pathlib import Path
from xml.etree import ElementTree as ET

SITE_DIR = Path("site/netherlands-news")
SHOW_TITLE = "Dutch Daily Briefing"
ITUNES = "http://www.itunes.com/dtds/podcast-1.0.dtd"
PODCAST = "https://podcastindex.org/namespace/1.0"
META = re.compile(r"<!-- nlnews (\{.*?\}) -->")
TAG = re.compile(r"^ep-(\d{4}-\d{2}-\d{2})(-.+)?$")
ET.register_namespace("itunes", ITUNES)
ET.register_namespace("podcast", PODCAST)
PAGES_EPISODES = 120  # daily episodes kept on Pages: ~7 MB each, under Pages' 1 GB limit
MAX_EPISODES = 30


def _gh(*args: str) -> str:
    return subprocess.run(["gh", *args], capture_output=True, text=True, check=True).stdout


def _html(notes: str) -> str:
    """The release notes' bit of Markdown as HTML for podcast apps: a paragraph per line,
    **bold** and [links](url)."""
    def inline(text: str) -> str:
        text = html.escape(text, quote=False)
        text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
        return re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)",
                      lambda m: f'<a href="{m[2].replace('"', '%22')}">{m[1]}</a>', text)  # already escaped
    return "".join(f"<p>{inline(re.sub(r'^- ', '', l.strip()))}</p>" for l in notes.splitlines() if l.strip())


def _releases(repo: str) -> list[dict]:
    """Every published episode release, newest first by the date in its tag: copied releases share
    commits and copy dates, so the API's order and dates say nothing about the episodes."""
    releases = []
    for page in range(1, 100):
        batch = json.loads(_gh("api", f"repos/{repo}/releases?per_page=100&page={page}"))
        releases += batch
        if len(batch) < 100:
            break
    releases = [r for r in releases if TAG.match(r["tag_name"]) and not r["draft"]]
    return sorted(releases, key=lambda r: TAG.match(r["tag_name"]).groups(default=""), reverse=True)


def build(repo: str, base: str, secret: str) -> None:
    episodes_dir = SITE_DIR / "episodes"
    episodes_dir.mkdir(parents=True, exist_ok=True)
    releases = _releases(repo)

    def copy(rel: dict, ext: str) -> Path | None:
        match = (lambda n: n.endswith(".mp3")) if ext == ".mp3" else (lambda n: n == "transcript.vtt")
        if not (asset := next((a for a in rel["assets"] if match(a["name"])), None)):
            return None
        local = episodes_dir / f"{rel['tag_name']}{ext}"
        if not local.exists() or local.stat().st_size != asset["size"]:
            _gh("release", "download", rel["tag_name"], "--pattern", asset["name"],
                "--output", str(local), "--clobber", "--repo", repo)
        return local

    daily = [r for r in releases if not TAG.match(r["tag_name"]).group(2)][:PAGES_EPISODES]
    for rel in daily:
        copy(rel, ".mp3")
        copy(rel, ".vtt")
    print(f"{len(releases)} releases, {len(daily)} daily episodes copied")

    rss = ET.Element("rss", {"version": "2.0"})
    ch = ET.SubElement(rss, "channel")
    for tag, text in [
        ("title", SHOW_TITLE),
        ("link", base),
        ("language", "en"),
        ("description", "A daily English-language briefing on news from the Netherlands, for internationals and newcomers."),
        (f"{{{ITUNES}}}author", SHOW_TITLE),
        (f"{{{ITUNES}}}explicit", "false"),
        (f"{{{ITUNES}}}block", "yes"),  # keep it out of the Apple Podcasts directory
    ]:
        ET.SubElement(ch, tag).text = text
    ET.SubElement(ch, f"{{{ITUNES}}}image", {"href": f"{base}/cover.png"})
    ET.SubElement(ch, f"{{{ITUNES}}}category", {"text": "News"})

    for rel in releases[:MAX_EPISODES]:
        if not (mp3 := copy(rel, ".mp3")):
            continue
        m = META.search(rel.get("body") or "")
        meta = json.loads(m.group(1)) if m else {}
        desc = META.sub("", rel.get("body") or "").strip()
        item = ET.SubElement(ch, "item")
        ET.SubElement(item, "title").text = rel["name"]
        ET.SubElement(item, "description").text = _html(desc)
        ET.SubElement(item, "guid", {"isPermaLink": "false"}).text = rel["tag_name"]
        published = datetime.fromisoformat((meta.get("published") or rel["published_at"]).replace("Z", "+00:00"))
        ET.SubElement(item, "pubDate").text = format_datetime(published)
        ET.SubElement(item, "enclosure", {"url": f"{base}/episodes/{mp3.name}",
                                          "length": str(mp3.stat().st_size), "type": "audio/mpeg"})
        if "duration" in meta:
            ET.SubElement(item, f"{{{ITUNES}}}duration").text = str(meta["duration"])
        if local := copy(rel, ".vtt"):
            ET.SubElement(item, f"{{{PODCAST}}}transcript", {"url": f"{base}/episodes/{local.name}",
                                                             "type": "text/vtt", "language": "en"})

    out = SITE_DIR / secret / "feed.xml"
    out.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(rss).write(out, encoding="utf-8", xml_declaration=True)
    (SITE_DIR / "served-by.txt").write_text("publisher\n")  # only exists here: proves who answers the path
    print(f"Feed built with {len(ch.findall('item'))} episodes")


if __name__ == "__main__":
    build(os.environ["GITHUB_REPOSITORY"], os.environ["SITE_BASE_URL"].rstrip("/"), os.environ["FEED_SECRET_PATH"])
