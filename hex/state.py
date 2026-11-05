"""Game state the advisor sees: rolling transcript + latest GMCP packets."""
from __future__ import annotations

import json
import re
import time
from collections import deque
from dataclasses import dataclass, field

from . import score
from .telnet import strip_ansi

EXITS_RE = re.compile(r"(?:obvious exits?|^Exits):\s*(.*)", re.I)


def split_exits(text: str) -> list[str]:
    return [e.strip() for e in re.split(r",|\band\b", text.rstrip(" .")) if e.strip()]


@dataclass
class GameState:
    max_lines: int = 400
    lines: deque = field(default_factory=lambda: deque(maxlen=400))
    gmcp: dict = field(default_factory=dict)         # package -> latest data
    room_id: str | None = None
    room_changed_at: float = 0.0
    hidden_input: bool = False                       # True while server hides echo (password)
    exits: list = field(default_factory=list)        # from room text; GMCP doesn't send them
    score: dict = field(default_factory=dict)        # latest `score` output you ran
    _score_lines: list | None = None                 # `score` output still arriving
    _partial: str = ""
    _exits_text: str | None = None                   # exit list still wrapping onto the next line
    _exits_fresh: bool = False                       # exits seen since your last command

    def add_text(self, text: str) -> None:
        text = self._partial + strip_ansi(text).replace("\r", "")
        *complete, self._partial = text.split("\n")
        for ln in complete:
            if ln.strip():
                self.lines.append(ln)
                self._scan_exits(ln)
                self._scan_score(ln)

    def _scan_score(self, line: str) -> None:
        if score.START.match(line):
            self._score_lines = []
        if self._score_lines is None:
            return
        self._score_lines.append(line)
        if score.END.search(line):
            self.score = score.parse_score(self._score_lines)
            self._score_lines = None
        elif len(self._score_lines) >= score.MAX_LINES:
            self._score_lines = None

    def _scan_exits(self, line: str) -> None:
        m = EXITS_RE.search(line)
        if m:
            self._exits_text = m.group(1)
        elif self._exits_text is not None:
            self._exits_text += " " + line.strip()
        else:
            return
        self.exits = split_exits(self._exits_text)
        self._exits_fresh = True
        if self._exits_text.rstrip().endswith("."):
            self._exits_text = None

    def flush_prompt(self) -> None:
        if self._partial.strip():
            self.lines.append(self._partial)
        self._partial = ""

    def add_command(self, cmd: str) -> None:
        self._exits_fresh = False
        # Never record anything typed while echo is off (i.e. passwords).
        self.lines.append("> <hidden>" if self.hidden_input else f"> {cmd}")

    def add_gmcp(self, package: str, data: object) -> bool:
        """Store a packet. Returns True if it means we moved room."""
        old = self.gmcp.get(package)
        if package == "char.vitals" and isinstance(old, dict) and isinstance(data, dict):
            data = {**old, **data}                   # packets can carry only what changed
        self.gmcp[package] = data
        if package == "room.info" and isinstance(data, dict):
            rid = str(data.get("identifier") or data.get("id") or data.get("name"))
            if rid != self.room_id:
                self.room_id = rid
                self.room_changed_at = time.time()
                if not self._exits_fresh:            # the room text may arrive before this packet
                    self.exits, self._exits_text = [], None
                return True
        return False

    def recent(self, n: int = 120) -> str:
        return "\n".join(list(self.lines)[-n:])

    def gmcp_summary(self) -> str:
        keep = {k: v for k, v in self.gmcp.items()
                if k in ("char.vitals", "char.info", "room.info", "room.writtenmap")}
        if self.score:
            keep = {"last score command": self.score, **keep}
        return json.dumps(keep, indent=1, default=str)[:4000] if keep else "(no GMCP data yet)"
