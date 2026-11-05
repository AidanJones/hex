"""Terminal front end: plays the MUD normally, with Hex, an LLM thinking engine, on demand.

Anything you type goes to the MUD, except lines starting with '/':
  /a [question]   ask Hex (summary + numbered suggestions)
  /1 .. /5        send suggestion N (you choose it; nothing is sent automatically)
  /auto           toggle advice whenever you enter a new room
  /learn <topic>  send `help <topic>` and save the page to the knowledge base
  /docs <query>   search local notes without calling the LLM
  /goal <text>    tell Hex what you're trying to do
  /help, /quit    this text / leave.   //text sends "/text" literally.
"""
from __future__ import annotations

import argparse
import asyncio
import getpass
import os
import sys
import time
from pathlib import Path

try:
    import readline  # noqa: F401  (history + line editing where available)
except ImportError:
    pass

from .advisor import Advice, Advisor
from .env import load_dotenv
from .knowledge import KnowledgeBase
from .llm import LLMError, make_llm
from .state import GameState
from .telnet import Event, MudConnection, strip_ansi

CYAN, YEL, RED, DIM, RST = "\x1b[36m", "\x1b[33m", "\x1b[31m", "\x1b[2m", "\x1b[0m"
ROOT = Path(__file__).resolve().parent.parent
GOALS_FILE = ROOT / "knowledge" / "cache" / "goals.txt"


def show_advice(a: Advice) -> None:
    w = sys.stdout.write
    w(f"\n{CYAN}┌─ Hex ─────────────────────────────{RST}\n")
    if a.summary:
        w(f"{CYAN}│{RST} {a.summary}\n")
    if a.explain:
        w(f"{CYAN}│{RST} {DIM}{a.explain}{RST}\n")
    for i, s in enumerate(a.suggestions, 1):
        w(f"{CYAN}│{RST} {YEL}/{i}{RST}  {s['command']}  {DIM}— {s.get('why', '')}{RST}\n")
    if a.watch_out:
        w(f"{CYAN}│{RST} {RED}⚠ {a.watch_out}{RST}\n")
    w(f"{CYAN}└────────────────────────────────────────{RST}\n")
    sys.stdout.flush()


class App:
    def __init__(self, args) -> None:
        self.state = GameState()
        self.kb = KnowledgeBase(ROOT / "knowledge")
        goals = GOALS_FILE.read_text().strip() if GOALS_FILE.exists() else ""
        try:
            self.advisor: Advisor | None = Advisor(make_llm(args.backend, args.model), self.kb, goals)
        except LLMError as e:
            print(f"{RED}Hex disabled: {e}{RST}")
            self.advisor = None
        self.conn = MudConnection(args.host, args.port, on_event=self.on_event)
        self.auto = args.auto
        self.last: Advice | None = None
        self.busy = False
        self.last_auto = 0.0
        self.capture: tuple[str, list[str], float] | None = None

    # ---- server -> us --------------------------------------------------
    def on_event(self, ev: Event) -> None:
        if ev.kind == "text":
            sys.stdout.write(ev.text)
            sys.stdout.flush()
            self.state.add_text(ev.text)
            if self.capture:
                self.capture[1].append(ev.text)
        elif ev.kind == "prompt":
            self.state.flush_prompt()
            self._finish_capture()
        elif ev.kind == "gmcp":
            if self.state.add_gmcp(ev.package, ev.data) and self.auto:
                if time.time() - self.last_auto > 8:   # don't spam while running about
                    self.last_auto = time.time()
                    asyncio.get_running_loop().create_task(self.ask(""))
        elif ev.kind == "echo_off":
            self.state.hidden_input = True
        elif ev.kind == "echo_on":
            self.state.hidden_input = False

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
            print(f"{DIM}[saved help '{topic}' → {p.relative_to(ROOT)}]{RST}")

    # ---- us -> server --------------------------------------------------
    async def ask(self, question: str) -> None:
        if not self.advisor:
            print(f"{RED}Hex is off (no API key / backend).{RST}")
            return
        if self.busy:
            return
        self.busy = True
        print(f"{DIM}[Hex thinking…]{RST}")
        try:
            self.last = await asyncio.to_thread(self.advisor.advise, self.state, question)
            show_advice(self.last)
        except LLMError as e:
            print(f"{RED}+++ Out Of Cheese Error +++ {e}{RST}")
        finally:
            self.busy = False

    def send(self, line: str) -> None:
        self.state.add_command(line)
        self.conn.send(line)

    async def handle(self, line: str) -> bool:
        if not line.startswith("/") or line.startswith("//"):
            self.send(line[1:] if line.startswith("//") else line)
            return True
        cmd, _, arg = line[1:].partition(" ")
        if cmd in {"q", "quit"}:
            return False
        if cmd == "a":
            asyncio.get_running_loop().create_task(self.ask(arg.strip()))
        elif cmd.isdigit():
            i = int(cmd) - 1
            if self.last and 0 <= i < len(self.last.suggestions):
                c = self.last.suggestions[i]["command"]
                print(f"{DIM}→ {c}{RST}")
                self.send(c)
            else:
                print("No such suggestion. /a to ask first.")
        elif cmd == "auto":
            self.auto = not self.auto
            print(f"auto-advice on room change: {'ON' if self.auto else 'off'}")
        elif cmd == "learn" and arg.strip():
            self.capture = (arg.strip(), [], time.time())
            self.send(f"help {arg.strip()}")
            asyncio.get_running_loop().call_later(5, self._finish_capture, True)
        elif cmd == "docs":
            for c in self.kb.search(arg, k=3):
                print(f"{CYAN}[{c.source} :: {c.title}]{RST}\n{c.text[:700]}\n")
        elif cmd == "goal":
            GOALS_FILE.parent.mkdir(parents=True, exist_ok=True)
            GOALS_FILE.write_text(arg.strip())
            if self.advisor:
                self.advisor.goals = arg.strip()
            print("goal noted.")
        else:
            print(__doc__)
        return True

    async def input_loop(self) -> None:
        while True:
            reader = getpass.getpass if self.state.hidden_input else input
            try:
                line = await asyncio.to_thread(reader, "")
            except EOFError:
                break
            if not await self.handle(line.rstrip("\n")):
                break

    async def main(self) -> None:
        await self.conn.connect()
        print(f"{DIM}connected to {self.conn.host}:{self.conn.port} — /help for Hex commands{RST}")
        net = asyncio.create_task(self.conn.run())
        keys = asyncio.create_task(self.input_loop())
        await asyncio.wait({net, keys}, return_when=asyncio.FIRST_COMPLETED)
        self.conn.close()


def main() -> None:
    load_dotenv(Path.cwd() / ".env", ROOT / ".env")
    env = os.environ.get
    p = argparse.ArgumentParser(prog="hex", description="Discworld MUD with Hex, an LLM thinking engine, at your elbow")
    p.add_argument("--host", default=env("HEX_HOST", "discworld.starturtle.net"))
    p.add_argument("--port", type=int, default=int(env("HEX_PORT", "23")), help="23, or 4242 if 23 is blocked")
    p.add_argument("--backend", choices=["anthropic", "minimax", "openai"], default=env("HEX_BACKEND", "anthropic"),
                   help="minimax = MiniMax API (MINIMAX_API_KEY); openai = any OpenAI-compatible server (Ollama, LM Studio…)")
    p.add_argument("--model", default=None)
    p.add_argument("--auto", action="store_true", help="advise automatically on room change")
    args = p.parse_args()
    try:
        asyncio.run(App(args).main())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
