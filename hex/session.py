"""The game session, independent of how it is drawn (plain terminal or TUI)."""
from __future__ import annotations

import asyncio
import json
import re
import time
from collections import deque
from pathlib import Path

from .advisor import Advice, Advisor
from .complete import Context, complete, recent_words
from .knowledge import COMMAND_PREFIX, SITUATION, KnowledgeBase
from .llm import LLMError, make_llm
from .rooms import RoomDB, is_move, normalise
from .state import GameState
from .telnet import Event, MudConnection, strip_ansi

ROOT = Path(__file__).resolve().parent.parent
GOALS_FILE = ROOT / "knowledge" / "cache" / "goals.txt"
ROOMS_FILE = ROOT / "knowledge" / "cache" / "rooms.db"
LOG_DIR = ROOT / "logs"
PLAYER_FILE = ROOT / "knowledge" / "cache" / "player.txt"
CHARACTER_FILE = ROOT / "knowledge" / "cache" / "characters.json"
OBSERVE_LINES = 40      # new game lines before Hex takes a quiet look...
OBSERVE_GAP = 120.0     # ...and never more often than this many seconds
NOT_A_NAME = {"new", "guest", "quit", "who", "finger"}     # other answers at the login prompt
MOVE_WINDOW = 3.0       # seconds a typed move can still explain a room change

HELP = """Anything you type goes to the MUD, except lines starting with '/':
  /a [question]   ask Hex (summary + numbered suggestions)
  /1 .. /5        send suggestion N (you choose it; nothing is sent automatically)
  /auto           toggle advice whenever you enter a new room
  /learn <topic>  send `help <topic>` and save the page to the knowledge base
  /docs <query>   search local notes without calling the LLM
  /goal <text>    tell Hex what you're trying to do
  /route [place]  show the way to somewhere you've been; /1 takes the next step
  /notes          list what Hex has noted.   /forget <title> removes one
  /name           show the character name Hex last saw you log in with
  /stats          show what Hex recorded from your last `score`
  /watch          toggle Hex quietly keeping notes as you play (on by default)
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
    def __init__(self, args, ui: UI, rooms: RoomDB | None = None) -> None:
        self.ui = ui
        self.rooms = rooms or RoomDB(ROOMS_FILE)
        self.moves: deque[tuple[str, float]] = deque()   # typed moves awaiting a room change
        self.destination = ""
        self.player_file = PLAYER_FILE
        self.player = PLAYER_FILE.read_text().strip() if PLAYER_FILE.exists() else ""
        self.character_file = CHARACTER_FILE
        self.keep_log = not getattr(args, "no_log", False)
        self.log_file = None
        self.watch = True
        self.watching = False           # a quiet look is in progress
        self.unseen_lines = 0
        self.last_look = time.time()
        self.last_line = ""             # last line typed with echo on; never a password
        self.logged_in = False
        self.name_typed = ""            # kept only once the login succeeds
        self.state = GameState()
        self.kb = KnowledgeBase(ROOT / "knowledge")
        goals = GOALS_FILE.read_text().strip() if GOALS_FILE.exists() else ""
        self.problem = ""
        try:
            self.advisor: Advisor | None = Advisor(make_llm(args.backend, args.model), self.kb, goals, self.rooms)
        except LLMError as e:
            self.problem = f"Hex disabled: {e}"
            self.advisor = None
        self.conn = MudConnection(args.host, args.port, on_event=self.on_event)
        self.auto = args.auto
        self.last: Advice | None = None
        self.busy = False
        self.last_auto = 0.0
        self.capture: tuple[str, list[str], float] | None = None
        self._load_score()

    async def connect(self) -> None:
        if self.problem:
            self.ui.show_note(self.problem, "error")
        await self.conn.connect()
        if self.keep_log:
            self.open_log(LOG_DIR)
        if self.player:
            self.ui.show_note(f"Last played as {self.player}", "head")
        self.ui.show_note(f"connected to {self.conn.host}:{self.conn.port} — /help for Hex commands", "dim")

    # ---- session log ----------------------------------------------------
    def open_log(self, folder: Path) -> None:
        try:
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / f"{time.strftime('%Y-%m-%d_%H%M%S')}.log"
            self.log_file = path.open("a", encoding="utf-8", buffering=1)
        except OSError as e:
            self.ui.show_note(f"Not keeping a log: {e}", "error")

    def _log(self, text: str) -> None:
        if self.log_file:
            self.log_file.write(text)

    def _log_line(self, text: str) -> None:
        self._log(f"\n[{time.strftime('%H:%M:%S')}] {text}\n")

    def _log_advice(self, a: Advice) -> None:
        lines = [a.summary, *(f"/{i} {s['command']}" for i, s in enumerate(a.suggestions, 1)), a.watch_out]
        self._log_line("Hex: " + " | ".join(x for x in lines if x))

    # ---- server -> us --------------------------------------------------
    def on_event(self, ev: Event) -> None:
        if ev.kind == "text":
            self.ui.show_text(ev.text)
            self._log(strip_ansi(ev.text).replace("\r", ""))
            before = self.state.score
            self.state.add_text(ev.text)
            if self.state.score is not before:
                self._save_score()
            self.ui.show_state()
            self.unseen_lines += ev.text.count("\n")
            self._maybe_observe()
            if self.capture:
                self.capture[1].append(ev.text)
        elif ev.kind == "prompt":
            self.ui.show_prompt()
            self.state.flush_prompt()
            self._finish_capture()
        elif ev.kind == "gmcp":
            before = self.state.room_id
            moved = self.state.add_gmcp(ev.package, ev.data)
            if ev.package in ("room.map", "room.writtenmap"):
                self._log_line(f"{ev.package} {json.dumps(ev.data)}")    # to check our parsing
            if ev.package == "room.info":
                self.logged_in = True
                self._remember_player(self.name_typed)
                self.name_typed = ""
            elif ev.package == "char.info" and isinstance(ev.data, dict):
                self._remember_player(ev.data.get("capname") or ev.data.get("name"))
            if moved:
                self._record_move(before, ev.data)
            self.ui.show_state()
            if moved and self.auto and time.time() - self.last_auto > 8:   # don't spam while running about
                self.last_auto = time.time()
                asyncio.get_running_loop().create_task(self.ask(""))
        elif ev.kind in ("echo_off", "echo_on"):
            self.state.hidden_input = ev.kind == "echo_off"
            self.ui.show_hidden(self.state.hidden_input)
            # The game asks for a password straight after you give your name.
            if self.state.hidden_input and not self.logged_in:
                self.name_typed = self.last_line

    def _remember_player(self, name: object) -> None:
        if (isinstance(name, str) and re.fullmatch(r"[A-Za-z]{2,}", name)
                and name.lower() not in NOT_A_NAME and name.lower() != self.player.lower()):
            self.player = name
            self.player_file.parent.mkdir(parents=True, exist_ok=True)
            self.player_file.write_text(name)
            self._load_score()

    def _characters(self) -> dict:
        try:
            return json.loads(self.character_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _load_score(self) -> None:
        self.state.score = self._characters().get(self.player.lower(), {})

    def _save_score(self) -> None:
        if self.player:
            data = {**self._characters(), self.player.lower(): self.state.score}
            self.character_file.parent.mkdir(parents=True, exist_ok=True)
            self.character_file.write_text(json.dumps(data, indent=1), encoding="utf-8")

    def _record_move(self, before: str | None, info: dict) -> None:
        room = self.state.room_id
        self.rooms.visit(room, str(info.get("name") or "somewhere"), str(info.get("kind") or ""))
        now = time.time()
        while self.moves and now - self.moves[0][1] > MOVE_WINDOW:
            self.moves.popleft()
        if self.moves:
            move, _ = self.moves.popleft()
            if before:
                self.rooms.link(before, move, room)

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
            self.ui.show_note(f"[saved help '{topic}' → {p.name}]", "dim")

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
            self.unseen_lines = 0
            self._log_advice(self.last)
            self._keep_notes(self.last)
        except LLMError as e:
            self.ui.show_note(f"+++ Out Of Cheese Error +++ {e}", "error")
        finally:
            self.busy = False
            self.ui.show_thinking(False)

    def _keep_notes(self, advice: Advice) -> None:
        if advice.status:
            self.kb.save_note(SITUATION, advice.status)
        saved = [n["title"] for n in advice.remember if self.kb.save_note(n["title"], n["text"])]
        known = self.kb.notes()
        for c in advice.commands:
            title = COMMAND_PREFIX + c["command"]
            if title not in known and self.kb.save_note(title, str(c.get("use") or c["command"])):
                saved.append(f"`{c['command']}`")
        if saved:
            self.ui.show_note(f"[Hex noted: {', '.join(saved)}]", "dim")
            self._log_line(f"Hex noted: {', '.join(saved)}")

    def _maybe_observe(self) -> None:
        if (self.watch and self.advisor and not self.watching and not self.busy
                and not self.state.hidden_input and self.unseen_lines >= OBSERVE_LINES
                and time.time() - self.last_look >= OBSERVE_GAP):
            self.watching = True
            asyncio.get_running_loop().create_task(self.observe())

    async def observe(self) -> None:
        """Update the notes from recent game text. Only ever reads; sends nothing."""
        self.unseen_lines, self.last_look = 0, time.time()
        try:
            self._keep_notes(await asyncio.to_thread(self.advisor.observe, self.state))
        except (LLMError, RuntimeError) as e:
            self.watch = False
            self.ui.show_note(f"Hex stopped keeping notes ({e}). /watch to resume.", "error")
        finally:
            self.watching = False

    def complete(self, line: str, history: list[str]) -> str | None:
        """Tab completion. Never offered at a password prompt."""
        if self.state.hidden_input:
            return None
        advice = self.last.suggestions if self.last else []
        words = line.rpartition(" ")[2]
        return complete(line, Context(
            suggestions=[s["command"] for s in advice],
            exits=self.state.exits,
            history=history[::-1],
            commands=self.kb.commands(),
            words=recent_words(list(self.state.lines)[-15:]),
            topics=self.kb.topics(),
            rooms=[r.name for r in self.rooms.find(words)] if line.startswith("/route ") else [],
            notes=list(self.kb.notes()) if line.startswith("/forget ") else []))

    def send(self, line: str) -> None:
        if not self.conn.writer:
            self.ui.show_note("Not connected yet.", "error")
            return
        if not self.state.hidden_input:
            self.last_line = line.strip()
            if is_move(line, self.state.exits):
                self.moves.append((normalise(line), time.time()))
        # Never log anything typed while echo is off (i.e. passwords).
        self._log_line("> <hidden>" if self.state.hidden_input else f"> {line}")
        self.state.add_command(line)
        self.conn.send(line)

    def learn(self, topic: str) -> None:
        self.capture = (topic, [], time.time())
        self.send(f"help {topic}")
        asyncio.get_running_loop().call_later(5, self._finish_capture, True)

    def route(self, place: str) -> None:
        place = place or self.destination
        here = self.state.room_id
        if not place or not here:
            self.ui.show_note("Usage: /route <place you've been>. Needs room data from the game.")
            return
        rooms = self.rooms.find(place)
        if not rooms:
            self.ui.show_note(f"You haven't been anywhere matching '{place}' yet.")
            return
        self.destination = place
        if any(r.id == here for r in rooms):
            self.ui.show_note("You're there.")
            return
        for room in rooms:
            path = self.rooms.route(here, room.id)
            if path:
                self.last = Advice(f"{room.name}: {', '.join(path)}",
                                   suggestions=[{"command": path[0], "why": "next step; /route again after"}])
                self.ui.show_advice(self.last)
                return
        self.ui.show_note(f"No known way to {rooms[0].name} from here.")

    def pick(self, n: int) -> None:
        """Send suggestion n (1-based). Only ever called from a human keypress."""
        if self.state.hidden_input:
            self.ui.show_note("Not while the game is asking for a password.", "error")
        elif self.last and 1 <= n <= len(self.last.suggestions):
            s = self.last.suggestions[n - 1]
            self.ui.show_note(f"→ {s['command']}", "dim")
            if s.get("learn"):
                self.learn(s["learn"])
            else:
                self.send(s["command"])
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
            self.learn(arg.strip())
        elif cmd == "route":
            self.route(arg.strip())
        elif cmd == "notes":
            notes = self.kb.notes()
            for title, text in notes.items():
                self.ui.show_note(title, "head")
                self.ui.show_note(text)
            if not notes:
                self.ui.show_note("Hex hasn't noted anything yet.")
        elif cmd == "forget":
            found = self.kb.forget_note(arg)
            self.ui.show_note("Forgotten." if found else "No note with that title.")
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
        elif cmd == "name":
            self.ui.show_note(self.player or "Hex hasn't seen you log in yet.")
        elif cmd == "watch":
            self.watch = not self.watch
            self.ui.show_note(f"quiet note-keeping: {'ON' if self.watch else 'off'}")
        elif cmd == "stats":
            lines = [f"{k.replace('_', ' ')}: {v}" for k, v in self.state.score.items()]
            self.ui.show_note("\n".join(lines) or "Type score and Hex will keep track of it.")
        elif cmd == "gmcp":
            self.ui.show_note(", ".join(sorted(self.state.gmcp)) or "No GMCP packets yet.")
        else:
            self.ui.show_note(HELP)
        return True
