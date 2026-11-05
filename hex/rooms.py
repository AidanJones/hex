"""A map of everywhere you've been, built from what the game already sends.

Recording is passive: it watches GMCP room changes and the moves you typed, and
never sends anything. Links are one-way because Discworld's often are.
"""
from __future__ import annotations

import re
import sqlite3
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path

SHORT = {"n": "north", "s": "south", "e": "east", "w": "west", "ne": "northeast",
         "nw": "northwest", "se": "southeast", "sw": "southwest", "u": "up", "d": "down"}
DIRECTIONS = set(SHORT.values()) | {"enter", "out", "exit"}
SCHEMA = """
CREATE TABLE IF NOT EXISTS rooms (id TEXT PRIMARY KEY, name TEXT, kind TEXT,
                                  visits INTEGER DEFAULT 0, last_seen REAL);
CREATE TABLE IF NOT EXISTS links (src TEXT, direction TEXT, dst TEXT,
                                  PRIMARY KEY (src, direction));
"""


def normalise(command: str) -> str:
    c = " ".join(command.lower().split())
    return SHORT.get(c, c)


def is_move(command: str, exits: list[str]) -> bool:
    c = normalise(command)
    return c in DIRECTIONS or c in (e.lower() for e in exits)


@dataclass
class Room:
    id: str
    name: str
    visits: int


class RoomDB:
    def __init__(self, path: Path | str = ":memory:") -> None:
        if isinstance(path, Path):
            path.parent.mkdir(parents=True, exist_ok=True)
        # Hex asks from a worker thread while the game loop records, hence the lock.
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.lock = threading.Lock()
        with self.lock, self.db:
            self.db.executescript(SCHEMA)

    def visit(self, room_id: str, name: str, kind: str = "") -> None:
        with self.lock, self.db:
            self.db.execute(
                "INSERT INTO rooms VALUES (?, ?, ?, 1, ?) ON CONFLICT(id) DO UPDATE SET "
                "name=excluded.name, kind=excluded.kind, visits=visits+1, last_seen=excluded.last_seen",
                (room_id, name, kind, time.time()))

    def link(self, src: str, direction: str, dst: str) -> None:
        with self.lock, self.db:
            self.db.execute("INSERT OR REPLACE INTO links VALUES (?, ?, ?)", (src, direction, dst))

    def count(self) -> int:
        with self.lock:
            return self.db.execute("SELECT COUNT(*) FROM rooms").fetchone()[0]

    def find(self, text: str, limit: int = 5) -> list[Room]:
        """Rooms whose name holds every word of `text`, most visited first."""
        words = [w for w in re.findall(r"[\w'-]+", text.lower())]
        if not words:
            return []
        where = " AND ".join("lower(name) LIKE ?" for _ in words)
        with self.lock:
            rows = self.db.execute(f"SELECT id, name, visits FROM rooms WHERE {where} "
                                   "ORDER BY visits DESC LIMIT ?",
                                   [f"%{w}%" for w in words] + [limit]).fetchall()
        return [Room(*r) for r in rows]

    def route(self, src: str, dst: str) -> list[str] | None:
        """Shortest list of moves using links you've walked; None if there's no known way."""
        with self.lock:
            links = self.db.execute("SELECT src, direction, dst FROM links").fetchall()
        graph: dict[str, list[tuple[str, str]]] = {}
        for a, direction, b in links:
            graph.setdefault(a, []).append((direction, b))
        came: dict[str, tuple[str, str] | None] = {src: None}
        todo = deque([src])
        while todo:
            here = todo.popleft()
            if here == dst:
                path = []
                while came[here]:
                    here, step = came[here]
                    path.append(step)
                return path[::-1]
            for direction, there in graph.get(here, []):
                if there not in came:
                    came[there] = (here, direction)
                    todo.append(there)
        return None

    def context(self, room_id: str | None, question: str) -> str:
        """Routes to any visited place the player's question mentions, for the LLM."""
        lines = [f"{self.count()} rooms visited so far."]
        seen = set()
        for word in re.findall(r"[a-z'-]{4,}", question.lower()):
            for room in self.find(word, limit=3):
                if room.id in seen or room.id == room_id or not room_id:
                    continue
                seen.add(room.id)
                path = self.route(room_id, room.id)
                if path:
                    lines.append(f"{room.name}: {', '.join(path)}")
        return "\n".join(lines[:8])
