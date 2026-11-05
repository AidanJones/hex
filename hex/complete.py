"""Tab completion for the input box. It only finishes what you've typed; it never sends."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .knowledge import STOP

SLASH = ["/a ", "/auto", "/docs ", "/forget ", "/gmcp", "/goal ", "/help", "/learn ",
         "/name", "/notes", "/quit", "/route ", "/stats", "/watch"]
VERBS = ["browse", "buy", "close", "consider", "drink", "drop", "eat", "follow", "get", "give",
         "glance", "help", "hold", "inventory", "kill", "learn", "list", "look", "map", "open",
         "read", "remove", "say", "score", "skills", "stop", "teach", "tell", "wear"]
WORD = re.compile(r"[a-z][a-z'-]{2,}")


@dataclass
class Context:
    """What the game and Hex know right now, most relevant first in each list."""
    suggestions: list[str] = field(default_factory=list)    # Hex's current advice
    exits: list[str] = field(default_factory=list)
    history: list[str] = field(default_factory=list)        # newest first
    commands: list[str] = field(default_factory=list)       # recorded by Hex
    words: list[str] = field(default_factory=list)          # from recent game text
    topics: list[str] = field(default_factory=list)         # help pages Hex knows about
    rooms: list[str] = field(default_factory=list)          # names for /route
    notes: list[str] = field(default_factory=list)          # titles for /forget


def recent_words(lines: list[str]) -> list[str]:
    """Words from game text, newest first. Skips what you typed."""
    seen: dict[str, None] = {}
    for line in reversed(lines):
        if not line.startswith(">"):
            for word in reversed(WORD.findall(line.lower())):
                if word not in STOP:
                    seen.setdefault(word)
    return list(seen)


def _finish(typed: str, options: list[str]) -> str | None:
    low = typed.lower()
    for option in options:
        if len(option) > len(typed) and option.lower().startswith(low):
            return typed + option[len(typed):]
    return None


def complete(line: str, ctx: Context) -> str | None:
    """The whole line with its most likely ending, or None."""
    if not line.strip():
        return None
    head, space, tail = line.partition(" ")
    if line.startswith("/"):
        if not space:
            return _finish(line, SLASH)
        options = {"/route": ctx.rooms, "/forget": ctx.notes, "/learn": ctx.topics}.get(head, [])
        rest = _finish(tail, options) if tail else None
        return f"{head} {rest}" if rest else None
    whole = _finish(line, ctx.suggestions + ctx.exits + ctx.history + ctx.commands + VERBS)
    if whole or not space:
        return whole
    before, _, word = line.rpartition(" ")
    rest = _finish(word, ctx.topics if head.lower() == "help" else ctx.words) if word else None
    return f"{before} {rest}" if rest else None
