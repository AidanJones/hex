"""Decode Discworld's `room.map` GMCP packet (stdlib only).

The packet is an ASCII map coloured with MXP tags rather than ANSI, e.g.
    ESC[3z ESC[4z MXP<RedMXP>+ ESC[3z - ESC[3z ESC[4z MXP<YellowMXP>@ ESC[3z
ESC[3z ends a colour, Yellow marks the player's room, and a colour can also be a
hex value. Format worked out from rgrams/discworld_mud_plugins (RossAsciiMap).

`room.writtenmap` is the same view in words ("a peasant is one west, exits north
and south of one east..."). Symbol meanings are from the game's `help map`.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

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


# ---- what the symbols mean ---------------------------------------------
SYMBOLS = {"@": "You are here", "*": "Room", "$": "Room with living things",
           "&": "Room with living things and non-cardinal exits", "+": "Door",
           "-": "East-west exit", "|": "North-south exit",
           "/": "Diagonal exit", "\\": "Diagonal exit"}
LEGEND = [("@", "You"), ("*", "Room"), ("$", "Someone there"), ("&", "Someone there"),
          ("+", "Door")]


def legend(raw: object) -> list[tuple[str, str]]:
    """Key for the symbols this map uses, with $ and & sharing a line when both appear."""
    used = set(plain_map(raw))
    rows = [(s, label) for s, label in LEGEND if s in used]
    if "$" in used and "&" in used:
        rows.remove(("&", "Someone there"))
        rows[[s for s, _ in rows].index("$")] = ("$&", "Someone there")
    if used & set("-|/\\"):
        rows.append(("-|", "Path"))
    if used - set(SYMBOLS) - set(" \n"):
        rows.append(("abc", "Terrain"))
    return rows


# ---- room.writtenmap -----------------------------------------------------
VECTORS = {"north": (0, 1), "northeast": (1, 1), "east": (1, 0), "southeast": (1, -1),
           "south": (0, -1), "southwest": (-1, -1), "west": (-1, 0), "northwest": (-1, 1)}
COUNTS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7}
_DIR = "northeast|northwest|southeast|southwest|north|south|east|west"
_STEP = rf"(?:(?:{'|'.join(COUNTS)}) )?(?:{_DIR})"
_MOVES = rf"{_STEP}(?:(?:, | and ){_STEP})*|here"
_END = r"(?=, | and |\.|\s*$)"
LIMIT_RE = re.compile(rf"the limit of your vision is (?:({_MOVES}) from )?here{_END}", re.I)
EXIT_RE = re.compile(rf"(?:an? )?(exit|door)s? ((?:{_DIR})(?:(?:, | and )(?:{_DIR}))*) of ({_MOVES}){_END}", re.I)
THING_RE = re.compile(rf"(.+?) (?:is|are) ({_MOVES}){_END}", re.I)
GAP_RE = re.compile(r"(?:, | and |\.|\s)+")
STEP_RE = re.compile(rf"(?:({'|'.join(COUNTS)}) )?({_DIR})", re.I)


@dataclass
class Place:
    moves: list[str] = field(default_factory=list)      # e.g. ["south", "southwest"]
    things: list[str] = field(default_factory=list)
    exits: list[str] = field(default_factory=list)
    doors: list[str] = field(default_factory=list)
    limit: bool = False                                 # you can't see beyond here

    @property
    def route(self) -> str:
        """"2 east, then south"."""
        parts: list[list] = []
        for move in self.moves:
            if parts and parts[-1][1] == move:
                parts[-1][0] += 1
            else:
                parts.append([1, move])
        return ", then ".join(f"{n} {m}" for n, m in parts) or "here"


def _steps(text: str) -> list[str]:
    return [m.group(2).lower() for m in STEP_RE.finditer(text)
            for _ in range(COUNTS.get((m.group(1) or "one").lower(), 1))]


def parse_written(raw: object) -> dict[tuple[int, int], Place]:
    """room.writtenmap as {(dx, dy) from you: Place}. North and east are positive."""
    if not isinstance(raw, str):
        return {}
    text = TAG_RE.sub("", ESC_RE.sub("", raw)).strip()
    places: dict[tuple[int, int], Place] = {}

    def place(moves: str | None) -> Place:
        steps = _steps(moves or "")
        key = (sum(VECTORS[s][0] for s in steps), sum(VECTORS[s][1] for s in steps))
        return places.setdefault(key, Place(steps))

    pos = 0
    while pos < len(text):
        gap = GAP_RE.match(text, pos)
        pos = gap.end() if gap else pos
        if m := LIMIT_RE.match(text, pos):
            place(m.group(1)).limit = True
        elif m := EXIT_RE.match(text, pos):
            target = place(m.group(3))
            (target.exits if m.group(1).lower() == "exit" else target.doors).extend(
                d.lower() for d in re.findall(_DIR, m.group(2), re.I))
        elif m := THING_RE.match(text, pos):
            name = m.group(1)
            place(m.group(2)).things.append(name[0].lower() + name[1:] if name[:2] in ("A ", "An") else name)
        else:
            break                       # unknown wording: keep what we understood
        pos = m.end()
    return places


def find_player(raw: object) -> tuple[int, int] | None:
    for y, line in enumerate(plain_map(raw).split("\n")):
        if "@" in line:
            return line.index("@"), y
    return None


def describe_cell(raw: object, places: dict, x: int, y: int) -> str | None:
    """Words for one character of the map, for hovering. None if it's blank."""
    rows = plain_map(raw).split("\n")
    if not 0 <= y < len(rows) or not 0 <= x < len(rows[y]) or rows[y][x] == " ":
        return None
    symbol = rows[y][x]
    lines = [SYMBOLS.get(symbol, "Terrain")]
    player = find_player(raw)
    if player and (x - player[0]) % 2 == 0 and (player[1] - y) % 2 == 0:
        # Rooms sit on every other row and column of the map.
        spot = places.get(((x - player[0]) // 2, (player[1] - y) // 2))
        if spot:
            if spot.moves:
                lines.append(f"Way there: {spot.route}")
            lines += spot.things
            if spot.exits:
                lines.append(f"Exits: {', '.join(spot.exits)}")
            if spot.doors:
                lines.append(f"Doors: {', '.join(spot.doors)}")
            if spot.limit:
                lines.append("You can't see further this way")
    return "\n".join(lines)
