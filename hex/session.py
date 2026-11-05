"""The game session, independent of how it is drawn (plain terminal or TUI)."""
from __future__ import annotations

import asyncio
import time
from pathlib import Path

from .advisor import Advice, Advisor
from .knowledge import KnowledgeBase
from .llm import LLMError, make_llm
from .state import GameState
from .telnet import Event, MudConnection, strip_ansi

ROOT = Path(__file__).resolve().parent.parent
GOALS_FILE = ROOT / "knowledge" / "cache" / "goals.txt"

HELP = """Anything you type goes to the MUD, except lines starting with '/':
  /a [question]   ask Hex (summary + numbered suggestions)
  /1 .. /5        send suggestion N (you choose it; nothing is sent automatically)
  /auto           toggle advice whenever you enter a new room
  /learn <topic>  send `help <topic>` and save the page to the knowledge base
  /docs <query>   search local notes without calling the LLM
  /goal <text>    tell Hex what you're trying to do
  /gmcp           list the GMCP packets received so far
  /help, /quit    this text / leave.   //text sends "/text" literally."""


class UI:
    """What a front end draws. Every method is called on the event loop."""

    def show_text(self, text: str) -> None: ...          # raw game output, may hold ANSI
    def show_prompt(self) -> None: ...                   # server finished a prompt
    def show_note(self, msg: str, kind: str = "") -> None: ...   # kind: "", dim, head, error
    def show_advice(self, advice: Advice) -> None: ...
    def show_thinking(self, busy: bool) -> None: ...
    def show_state(self) -> None: ...                    # GameState changed
    def show_hidden(self, hidden: bool) -> None: ...     # server turned echo off/on


class Session:
    def __init__(self, args, ui: UI) -> None:
        self.ui = ui
        self.state = GameState()
        self.kb = KnowledgeBase(ROOT / "knowledge")
        goals = GOALS_FILE.read_text().strip() if GOALS_FILE.exists() else ""
        self.problem = ""
        try:
            self.advisor: Advisor | None = Advisor(make_llm(args.backend, args.model), self.kb, goals)
        except LLMError as e:
            self.problem = f"Hex disabled: {e}"
            self.advisor = None
        self.conn = MudConnection(args.host, args.port, on_event=self.on_event)
        self.auto = args.auto
        self.last: Advice | None = None
        self.busy = False
        self.last_auto = 0.0
        self.capture: tuple[str, list[str], float] | None = None

    async def connect(self) -> None:
        if self.problem:
            self.ui.show_note(self.problem, "error")
        await self.conn.connect()
        self.ui.show_note(f"connected to {self.conn.host}:{self.conn.port} — /help for Hex commands", "dim")

    # ---- server -> us --------------------------------------------------
    def on_event(self, ev: Event) -> None:
        if ev.kind == "text":
            self.ui.show_text(ev.text)
            self.state.add_text(ev.text)
            self.ui.show_state()
            if self.capture:
                self.capture[1].append(ev.text)
        elif ev.kind == "prompt":
            self.ui.show_prompt()
            self.state.flush_prompt()
            self._finish_capture()
        elif ev.kind == "gmcp":
            moved = self.state.add_gmcp(ev.package, ev.data)
            self.ui.show_state()
            if moved and self.auto and time.time() - self.last_auto > 8:   # don't spam while running about
                self.last_auto = time.time()
                asyncio.get_running_loop().create_task(self.ask(""))
        elif ev.kind in ("echo_off", "echo_on"):
            self.state.hidden_input = ev.kind == "echo_off"
            self.ui.show_hidden(self.state.hidden_input)

    def _finish_capture(self, force: bool = False) -> None:
        if not self.capture:
            return
        topic, chunks, started = self.capture
        if not force and time.time() - started < 0.5:
            return
        text = strip_ansi("".join(chunks)).replace("\r", "")
        self.capture = None
        if text.strip():
            p = self.kb.save_help(topic, text)
            self.ui.show_note(f"[saved help '{topic}' → {p.relative_to(ROOT)}]", "dim")

    # ---- us -> server --------------------------------------------------
    async def ask(self, question: str) -> None:
        if not self.advisor:
            self.ui.show_note("Hex is off (no API key / backend).", "error")
            return
        if self.busy:
            return
        self.busy = True
        self.ui.show_thinking(True)
        try:
            self.last = await asyncio.to_thread(self.advisor.advise, self.state, question)
            self.ui.show_advice(self.last)
        except LLMError as e:
            self.ui.show_note(f"+++ Out Of Cheese Error +++ {e}", "error")
        finally:
            self.busy = False
            self.ui.show_thinking(False)

    def send(self, line: str) -> None:
        if not self.conn.writer:
            self.ui.show_note("Not connected yet.", "error")
            return
        self.state.add_command(line)
        self.conn.send(line)

    def pick(self, n: int) -> None:
        """Send suggestion n (1-based). Only ever called from a human keypress."""
        if self.state.hidden_input:
            self.ui.show_note("Not while the game is asking for a password.", "error")
        elif self.last and 1 <= n <= len(self.last.suggestions):
            c = self.last.suggestions[n - 1]["command"]
            self.ui.show_note(f"→ {c}", "dim")
            self.send(c)
        else:
            self.ui.show_note("No such suggestion. /a to ask first.")

    async def handle(self, line: str) -> bool:
        # While echo is off the line is a password: it goes to the MUD untouched,
        # so one starting with "/" can never reach the LLM or the notes.
        if self.state.hidden_input:
            self.send(line)
            return True
        if not line.startswith("/") or line.startswith("//"):
            self.send(line[1:] if line.startswith("//") else line)
            return True
        cmd, _, arg = line[1:].partition(" ")
        if cmd in {"q", "quit"}:
            return False
        if cmd == "a":
            asyncio.get_running_loop().create_task(self.ask(arg.strip()))
        elif cmd.isdigit():
            self.pick(int(cmd))
        elif cmd == "auto":
            self.auto = not self.auto
            self.ui.show_note(f"auto-advice on room change: {'ON' if self.auto else 'off'}")
        elif cmd == "learn" and arg.strip():
            self.capture = (arg.strip(), [], time.time())
            self.send(f"help {arg.strip()}")
            asyncio.get_running_loop().call_later(5, self._finish_capture, True)
        elif cmd == "docs":
            for c in self.kb.search(arg, k=3):
                self.ui.show_note(f"[{c.source} :: {c.title}]", "head")
                self.ui.show_note(f"{c.text[:700]}\n")
        elif cmd == "goal":
            GOALS_FILE.parent.mkdir(parents=True, exist_ok=True)
            GOALS_FILE.write_text(arg.strip())
            if self.advisor:
                self.advisor.goals = arg.strip()
            self.ui.show_note("goal noted.")
        elif cmd == "gmcp":
            self.ui.show_note(", ".join(sorted(self.state.gmcp)) or "No GMCP packets yet.")
        else:
            self.ui.show_note(HELP)
        return True
