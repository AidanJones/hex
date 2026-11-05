"""Tiny local knowledge base: markdown files chunked by heading, BM25 retrieval.

- knowledge/core/   hand-written notes shipped with the repo
- knowledge/cache/  in-game help captured with /learn, guides saved by hex.guides,
                    and hex-notes.md, the notes Hex writes for itself
Drop any extra .md (e.g. your own wiki notes) into either folder.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

TOKEN = re.compile(r"[a-z0-9']+")
NOTES_FILE = "hex-notes.md"
MAX_NOTES = 300
STOP = set("the a an of to and or in on is it you your for with at be this that are as by from "
           "i me my we how do does did what when where why who can could should would will am".split())


SAME_WORD = dict.fromkeys(("die", "dies", "died", "dying", "dead"), "death")
PER_SOURCE = 2          # so one long page can't fill every slot


def tokens(s: str) -> list[str]:
    return [SAME_WORD.get(t, t) for t in TOKEN.findall(s.lower()) if t not in STOP]


@dataclass
class Chunk:
    source: str
    title: str
    text: str


class KnowledgeBase:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.reload()

    def reload(self) -> None:
        self.chunks: list[Chunk] = []
        for p in sorted(self.root.rglob("*.md")):
            self.chunks += self._split(p)
        self._index()

    @staticmethod
    def _split(path: Path) -> list[Chunk]:
        text = path.read_text(encoding="utf-8", errors="replace")
        parts = re.split(r"(?m)^(#{1,3} .+)$", text)
        out, title = [], path.stem
        if parts[0].strip() and not parts[0].startswith("Source:"):
            out.append(Chunk(path.name, title, parts[0].strip()))
        for i in range(1, len(parts), 2):
            body = parts[i + 1].strip() if i + 1 < len(parts) else ""
            if body:
                out.append(Chunk(path.name, parts[i].lstrip("# ").strip(), body))
        return out

    def _index(self) -> None:
        self.docs = [Counter(tokens(c.title + " " + c.title + " " + c.text)) for c in self.chunks]
        self.lens = [sum(d.values()) for d in self.docs]
        self.avg = (sum(self.lens) / len(self.lens)) if self.lens else 1
        df = Counter(t for d in self.docs for t in d)
        n = len(self.docs)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}

    def search(self, query: str, k: int = 4, max_chars: int = 6000) -> list[Chunk]:
        q = tokens(query)
        scored = []
        for i, d in enumerate(self.docs):
            s = 0.0
            for t in q:
                f = d.get(t, 0)
                if f:
                    s += self.idf[t] * f * 2.2 / (f + 1.2 * (0.25 + 0.75 * self.lens[i] / self.avg))
            if s > 0:
                scored.append((s, i))
        out, used, per_source = [], 0, Counter()
        for _, i in sorted(scored, reverse=True):
            c = self.chunks[i]
            if used + len(c.text) > max_chars or per_source[c.source] >= PER_SOURCE:
                continue
            out.append(c)
            used += len(c.text)
            per_source[c.source] += 1
            if len(out) == k:
                break
        return out

    def topics(self) -> list[str]:
        """Names of the help and guide pages saved locally."""
        names = {re.sub(r"^(guide_(newbie|concepts)|help)_", "", p.stem)
                 for p in (self.root / "cache").glob("*.md") if p.name != NOTES_FILE}
        return sorted(names)

    def _help_path(self, topic: str) -> Path:
        safe = re.sub(r"[^a-z0-9_-]+", "_", topic.lower()).strip("_") or "topic"
        return self.root / "cache" / f"help_{safe}.md"

    def has_help(self, topic: str) -> bool:
        return self._help_path(topic).exists()

    # ---- Hex's own notes ------------------------------------------------
    @property
    def notes_path(self) -> Path:
        return self.root / "cache" / NOTES_FILE

    def notes(self) -> dict[str, str]:
        if not self.notes_path.exists():
            return {}
        return {c.title: c.text for c in self._split(self.notes_path)}

    def _write_notes(self, notes: dict[str, str]) -> None:
        self.notes_path.parent.mkdir(parents=True, exist_ok=True)
        body = "\n".join(f"# {t}\n\n{x}\n" for t, x in notes.items())
        self.notes_path.write_text(body, encoding="utf-8")
        self.reload()

    def save_note(self, title: str, text: str) -> bool:
        """Add or replace a note. One line each, so a note can't forge a heading."""
        title = " ".join(title.lstrip("# ").split())[:80]
        text = " ".join(text.split())[:500]
        if not title or not text:
            return False
        notes = self.notes()
        notes.pop(title, None)
        notes[title] = text
        self._write_notes(dict(list(notes.items())[-MAX_NOTES:]))
        return True

    def forget_note(self, title: str) -> bool:
        notes = self.notes()
        match = next((t for t in notes if t.lower() == title.strip().lower()), None)
        if match:
            del notes[match]
            self._write_notes(notes)
        return bool(match)

    def save_help(self, topic: str, text: str) -> Path:
        p = self._help_path(topic)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f"# help {topic}\n\n{text.strip()}\n", encoding="utf-8")
        self.reload()
        return p
