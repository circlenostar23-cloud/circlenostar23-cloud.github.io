"""Checks that Pages is serving the build this run just deployed, and exits 1 if it isn't within a few minutes.

deploy-pages can report success while Pages keeps serving the previous build (2026-11-03), so this compares
the live feed with the built one, byte for byte, and checks that the newest episode's MP3 is live at full size.
It never prints the feed's URL, which holds the secret path.
"""
import os
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

from build_site import SITE_DIR

WAIT = 5 * 60  # seconds
INTERVAL = 15


def fetch(url: str, method: str = "GET") -> tuple[int, bytes, dict]:
    # A fresh query string each time, so the CDN can't answer from a cached copy (feed.xml is cached 10 min).
    req = urllib.request.Request(f"{url}?v={os.environ.get('GITHUB_RUN_ID', '0')}-{time.time_ns()}", method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, resp.read(), dict(resp.headers)
    except urllib.error.HTTPError as exc:
        return exc.code, b"", {}
    except OSError:
        return 0, b"", {}


def problems(base: str, secret: str, built: bytes, mp3_url: str, mp3_size: str) -> list[str]:
    found = []
    status, body, _ = fetch(f"{base}/{secret}/feed.xml")
    if status != 200:
        found.append(f"feed returned {status}")
    elif body != built:
        found.append("live feed isn't the one just built")
    status, _, headers = fetch(mp3_url, method="HEAD")
    name = mp3_url.rsplit("/", 1)[-1]
    if status != 200:
        found.append(f"{name} returned {status}")
    elif headers.get("Content-Length") != mp3_size:
        found.append(f"{name} is {headers.get('Content-Length')} bytes, not {mp3_size}")
    return found


def main() -> int:
    base, secret = os.environ["SITE_BASE_URL"].rstrip("/"), os.environ["FEED_SECRET_PATH"]
    built = (SITE_DIR / secret / "feed.xml").read_bytes()
    enclosure = ET.fromstring(built).find("channel/item/enclosure")
    if enclosure is None:
        print("The built feed has no episodes")
        return 1
    mp3_url, mp3_size = enclosure.get("url"), enclosure.get("length")
    deadline = time.monotonic() + WAIT
    while True:
        found = problems(base, secret, built, mp3_url, mp3_size)
        if not found:
            print(f"Pages is serving this build ({mp3_url.rsplit('/', 1)[-1]} is live)")
            return 0
        if time.monotonic() > deadline:
            print(f"Pages still isn't serving this build after {WAIT // 60} min: {'; '.join(found)}")
            return 1
        time.sleep(INTERVAL)


if __name__ == "__main__":
    sys.exit(main())
