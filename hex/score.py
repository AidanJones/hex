"""Read the game's `score` output when it scrolls past. Hex never sends `score` itself.

Keys shared with GMCP char.vitals (hp, gp, xp, burden, alignment) use the same names.
Any line that doesn't match is skipped, so a changed format loses a field, not the lot.
"""
from __future__ import annotations

import re

START = re.compile(r"^You have \d+ \(\d+\) hit points")
END = re.compile(r"\bold and have logged in\b")
MAX_LINES = 16          # give up if the end line never comes
COUNTS = {"once": 1, "twice": 2}

PATTERNS = [
    r"You have (?P<hp>\d+) \((?P<maxhp>\d+)\) hit points",
    r"(?P<gp>\d+) \((?P<maxgp>\d+)\) guild points",
    r"(?P<quest_points>\d+) \((?P<quest_max>\d+)\) quest points",
    r"(?P<achievement_points>\d+) \((?P<achievement_max>\d+)\) achievement points",
    r"(?P<social>\d+) \((?P<social_max>\d+)\) social points",
    r"current experience is (?P<xp>\d+)",
    r"level (?P<level>\d+) in the (?P<guild>.+?)(?: Guild)?;",
    r"overall rating is (?P<rating>\d+)",
    r"You have died (?P<deaths>\d+|once|twice)",
    r"can die (?P<lives_left>\d+|once|twice)",
    r"wimpy is set to (?P<wimpy>\d+)%",
    r"You are (?P<burden_text>[\w ]+?) \((?P<burden>\d+)%\) and (?P<comfort>[\w ]+?)\.",
    r"You are (?P<alignment>[\w ]+?), (?P<faith>worshipping [\w ]+?)\.",
    r"You are (?P<age>\d[\w ,]*?) old and have logged in (?P<logins>\d+|once|twice)",
]


def _value(text: str) -> int | str:
    return int(text) if text.isdigit() else COUNTS.get(text, text)


def parse_score(lines: list[str]) -> dict:
    text = " ".join(" ".join(lines).split())    # the game wraps lines at any point
    found: dict = {}
    for pattern in PATTERNS:
        m = re.search(pattern, text)
        if m:
            found.update({k: _value(v) for k, v in m.groupdict().items()})
    return found
