"""Download Discworld's official newbie docs into knowledge/cache/.

    python -m hex.guides            newbie guides plus the key concept pages
    python -m hex.guides --all      every concept page too (about 7 minutes)
    python -m hex.guides --refresh  fetch again, replacing saved copies

The pages are copyright Discworld MUD with no reuse terms published, so they stay
in the git-ignored cache as your own local copy. Don't commit them. The community
wiki is deliberately not fetched: its robots.txt bars Claude's crawler by name.
"""
from __future__ import annotations

import argparse
import re
import time
import urllib.error
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

BASE = "https://discworld.starturtle.net/lpc/playing/documentation.c?path="
AGENT = "hex-guide-import/0.1"
DELAY = 2.0                                 # seconds between requests
CACHE = Path(__file__).resolve().parent.parent / "knowledge" / "cache"
KEY_CONCEPTS = ["achievement_points", "advancing", "alignment", "automation", "citizenship",
                "combat", "corpse", "crafting", "currency", "death", "equipment",
                "experience", "faq", "rules"]


class PageText(HTMLParser):
    """Pulls the body of a documentation page out as markdown."""

    def __init__(self, page: str) -> None:
        super().__init__(convert_charrefs=True)
        self.page = page
        self.out: list[str] = []
        self.depth = 0                      # divs open inside #content
        self.skip = False                   # inside the page's <h2> title
        self.heading = False

    def handle_starttag(self, tag, attrs):
        if self.depth == 0:
            if tag == "div" and ("id", "content") in attrs:
                self.depth = 1
            return
        if tag == "div":
            self.depth += 1
        elif tag == "h2":
            self.skip = True
        elif tag == "h3":
            self.heading = True
            self.out.append(f"\n\n# {self.page}: ")
        elif tag in ("p", "pre", "br", "tr"):
            self.out.append("\n\n" if tag == "p" else "\n")
        elif tag == "li":
            self.out.append("\n- ")

    def handle_endtag(self, tag):
        if self.depth == 0:
            return
        if tag == "div":
            self.depth -= 1
        elif tag == "h2":
            self.skip = False
        elif tag == "h3":
            self.heading = False
            self.out.append("\n\n")

    def handle_data(self, data):
        if self.depth and not self.skip:
            self.out.append(" ".join(data.split()) + " " if self.heading or data.strip() else "")


def page_markdown(html: str, page: str) -> str:
    parser = PageText(page)
    parser.feed(html)
    text = "".join(parser.out).replace("ITALIC", "")      # leftover markup in the source
    text = re.sub(r"[ \t]+\n", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def listed_pages(html: str, section: str) -> list[str]:
    found = re.findall(rf"documentation\.c\?path=/{section}/([\w-]+)", html)
    return sorted({p for p in found if not p.endswith("_test")})


def fetch(path: str) -> str:
    req = urllib.request.Request(BASE + path, headers={"User-Agent": AGENT})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", "replace")


def save(section: str, page: str, refresh: bool) -> str:
    target = CACHE / f"guide_{section}_{page}.md"
    if target.exists() and not refresh:
        return "kept"
    path = f"/{section}/{page}"
    body = page_markdown(fetch(path), page.replace("_", " "))
    if not body:
        return "empty"
    target.write_text(f"Source: {BASE}{path} (copyright Discworld MUD; local copy, don't redistribute)\n\n"
                      f"{body}\n", encoding="utf-8")
    time.sleep(DELAY)
    return "saved"


def main() -> None:
    p = argparse.ArgumentParser(prog="python -m hex.guides", description="Save Discworld's official docs locally")
    p.add_argument("--all", action="store_true", help="every concept page, not just the key ones")
    p.add_argument("--refresh", action="store_true", help="replace pages already saved")
    args = p.parse_args()
    CACHE.mkdir(parents=True, exist_ok=True)
    wanted = [("newbie", page) for page in listed_pages(fetch("/newbie"), "newbie")]
    time.sleep(DELAY)
    concepts = listed_pages(fetch("/concepts"), "concepts") if args.all else KEY_CONCEPTS
    wanted += [("concepts", page) for page in concepts]
    tally: dict[str, int] = {}
    for section, page in wanted:
        try:
            result = save(section, page, args.refresh)
        except (urllib.error.URLError, TimeoutError) as e:
            result = "failed"
            print(f"{section}/{page}: {e}")
        tally[result] = tally.get(result, 0) + 1
    print(", ".join(f"{n} {k}" for k, n in sorted(tally.items())))


if __name__ == "__main__":
    main()
