"""Tiny local knowledge base: markdown files chunked by heading, BM25 retrieval.

- knowledge/core/   hand-written notes shipped with the repo
- knowledge/cache/  in-game `help <topic>` pages captured with /learn
Drop any extra .md (e.g. your own wiki notes) into either folder.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

TOKEN = re.compile(r"[a-z0-9']+")
STOP = set("the a an of to and or in on is it you your for with at be this that are as by from".split())


def tokens(s: str) -> list[str]:
    return [t for t in TOKEN.findall(s.lower()) if t not in STOP]


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
        if parts[0].strip():
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
        out, used = [], 0
        for _, i in sorted(scored, reverse=True)[:k]:
            c = self.chunks[i]
            if used + len(c.text) > max_chars:
                break
            out.append(c)
            used += len(c.text)
        return out

    def save_help(self, topic: str, text: str) -> Path:
        safe = re.sub(r"[^a-z0-9_-]+", "_", topic.lower()).strip("_") or "topic"
        p = self.root / "cache" / f"help_{safe}.md"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f"# help {topic}\n\n{text.strip()}\n", encoding="utf-8")
        self.reload()
        return p
