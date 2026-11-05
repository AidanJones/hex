"""Turns game state + docs into a short summary and numbered suggestions."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from .knowledge import KnowledgeBase
from .state import GameState

SYSTEM = """You are a friendly, experienced Discworld MUD player sitting beside a newer \
player, helping them past the learning curve. You can see the recent game text, \
structured GMCP data, and excerpts from game help/wiki notes.

Rules:
- You ADVISE. The human decides and types. Never claim you did something in the game.
- Discworld forbids automated play. Never suggest bots, unattended scripts, or \
triggers that send commands. Aliases the player types themselves are fine.
- Discworld's parser wants full words ("north" works, but item nouns often need \
to be written out; e.g. "teach 5 levels of adventuring.points to me").
- Prefer concrete commands the player can type right now. If unsure of exact \
syntax, suggest the relevant `help <topic>` command instead of guessing.
- Flag danger (low HP, aggressive NPCs, death/corpse rot, losing lives) clearly.
- Keep it short. Plain British English. A touch of Pratchett humour is welcome, \
never at the cost of clarity.

Reply with JSON only, no code fences:
{"summary": "1-3 sentences: what just happened / where we are",
 "explain": "optional: decode any confusing game text or jargon, else empty",
 "suggestions": [{"command": "exact text to type", "why": "short reason"}],
 "watch_out": "optional warning, else empty"}
Give 2-5 suggestions, most useful first."""


@dataclass
class Advice:
    summary: str = ""
    explain: str = ""
    suggestions: list[dict] = field(default_factory=list)
    watch_out: str = ""
    raw: str = ""

    @classmethod
    def parse(cls, text: str) -> "Advice":
        m = re.search(r"\{.*\}", text, re.S)
        try:
            d = json.loads(m.group(0)) if m else {}
        except json.JSONDecodeError:
            d = {}
        if not d:
            return cls(summary=text.strip()[:800], raw=text)
        sugg = [s for s in d.get("suggestions", []) if isinstance(s, dict) and s.get("command")]
        return cls(d.get("summary", ""), d.get("explain", ""), sugg[:5], d.get("watch_out", ""), text)


class Advisor:
    def __init__(self, llm, kb: KnowledgeBase, goals: str = "") -> None:
        self.llm, self.kb, self.goals = llm, kb, goals

    def build_prompt(self, state: GameState, question: str = "") -> str:
        recent = state.recent(120)
        last = "\n".join(recent.splitlines()[-25:])
        refs = self.kb.search(f"{question}\n{last}", k=4)
        ref_txt = "\n\n".join(f"[{c.source} :: {c.title}]\n{c.text}" for c in refs) or "(none matched)"
        return (f"## Player goals\n{self.goals or '(not set: assume a new player finding their feet)'}\n\n"
                f"## Reference notes\n{ref_txt}\n\n"
                f"## GMCP state\n{state.gmcp_summary()}\n\n"
                f"## Recent game text (oldest first, '>' = player input)\n{recent}\n\n"
                f"## Player's question\n{question or 'What is going on, and what should I do next?'}")

    def advise(self, state: GameState, question: str = "") -> Advice:
        return Advice.parse(self.llm.complete(SYSTEM, self.build_prompt(state, question)))
