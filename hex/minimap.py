"""Decode Discworld's `room.map` GMCP packet (stdlib only).

The packet is an ASCII map coloured with MXP tags rather than ANSI, e.g.
    ESC[3z ESC[4z MXP<RedMXP>+ ESC[3z - ESC[3z ESC[4z MXP<YellowMXP>@ ESC[3z
ESC[3z ends a colour, Yellow marks the player's room, and a colour can also be a
hex value. Format worked out from rgrams/discworld_mud_plugins (RossAsciiMap).
"""
from __future__ import annotations

import re

TAG_RE = re.compile(r"MXP<(.*?)MXP>")
HEX_RE = re.compile(r"#[0-9a-fA-F]{6}")
ESC_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
RESET = "\x1b[3z"

Segment = tuple[str, "str | None"]     # (text, colour name or #rrggbb)


def _colour(tag: str) -> str | None:
    """Colour named by a tag; None for closing tags and links (`send`)."""
    if tag.startswith("/") or tag.lower().startswith("send"):
        return None
    found = HEX_RE.search(tag)
    return found.group(0).lower() if found else tag.strip().lower() or None


def parse_map(raw: object) -> list[list[Segment]]:
    """Turn a room.map payload into lines of coloured segments."""
    if not isinstance(raw, str):
        return []
    text = raw.replace("\r", "").replace("\x1b[4z", "").replace(RESET, "MXP</MXP>")
    text = ESC_RE.sub("", text)
    lines = []
    for line in text.rstrip("\n").split("\n"):
        segs: list[Segment] = []
        colour, pos = None, 0
        for m in TAG_RE.finditer(line):
            if m.start() > pos:
                segs.append((line[pos:m.start()], colour))
            colour, pos = _colour(m.group(1)), m.end()
        if pos < len(line):
            segs.append((line[pos:], colour))
        lines.append(segs)
    return lines


def plain_map(raw: object) -> str:
    return "\n".join("".join(t for t, _ in segs) for segs in parse_map(raw))
