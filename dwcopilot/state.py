"""Game state the advisor sees: rolling transcript + latest GMCP packets."""
from __future__ import annotations

import json
import time
from collections import deque
from dataclasses import dataclass, field

from .telnet import strip_ansi


@dataclass
class GameState:
    max_lines: int = 400
    lines: deque = field(default_factory=lambda: deque(maxlen=400))
    gmcp: dict = field(default_factory=dict)         # package -> latest data
    room_id: str | None = None
    room_changed_at: float = 0.0
    hidden_input: bool = False                       # True while server hides echo (password)
    _partial: str = ""

    def add_text(self, text: str) -> None:
        text = self._partial + strip_ansi(text).replace("\r", "")
        *complete, self._partial = text.split("\n")
        for ln in complete:
            if ln.strip():
                self.lines.append(ln)

    def flush_prompt(self) -> None:
        if self._partial.strip():
            self.lines.append(self._partial)
        self._partial = ""

    def add_command(self, cmd: str) -> None:
        # Never record anything typed while echo is off (i.e. passwords).
        self.lines.append("> <hidden>" if self.hidden_input else f"> {cmd}")

    def add_gmcp(self, package: str, data: object) -> bool:
        """Store a packet. Returns True if it means we moved room."""
        self.gmcp[package] = data
        if package == "room.info" and isinstance(data, dict):
            rid = str(data.get("identifier") or data.get("id") or data.get("name"))
            if rid != self.room_id:
                self.room_id = rid
                self.room_changed_at = time.time()
                return True
        return False

    def recent(self, n: int = 120) -> str:
        return "\n".join(list(self.lines)[-n:])

    def gmcp_summary(self) -> str:
        keep = {k: v for k, v in self.gmcp.items()
                if k in ("char.vitals", "char.info", "room.info", "room.writtenmap")}
        return json.dumps(keep, indent=1, default=str)[:4000] if keep else "(no GMCP data yet)"
