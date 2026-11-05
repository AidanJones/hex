"""Entry point, plus the plain scrolling terminal front end.

Uses the split-screen interface in tui.py when Textual is installed. Commands
are listed in session.HELP.
"""
from __future__ import annotations

import argparse
import asyncio
import getpass
import os
import sys
from pathlib import Path

try:
    import readline  # noqa: F401  (history + line editing where available)
except ImportError:
    pass

from .advisor import Advice
from .env import load_dotenv
from .session import ROOT, Session, UI

CYAN, YEL, RED, DIM, RST = "\x1b[36m", "\x1b[33m", "\x1b[31m", "\x1b[2m", "\x1b[0m"
NOTE_COLOURS = {"dim": DIM, "head": CYAN, "error": RED}


class PlainUI(UI):
    def show_text(self, text: str) -> None:
        sys.stdout.write(text)
        sys.stdout.flush()

    def show_note(self, msg: str, kind: str = "") -> None:
        print(f"{NOTE_COLOURS[kind]}{msg}{RST}" if kind else msg)

    def show_thinking(self, busy: bool) -> None:
        if busy:
            print(f"{DIM}[Hex thinking…]{RST}")

    def show_advice(self, a: Advice) -> None:
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


async def input_loop(session: Session) -> None:
    while True:
        reader = getpass.getpass if session.state.hidden_input else input
        try:
            line = await asyncio.to_thread(reader, "")
        except EOFError:
            break
        if not await session.handle(line.rstrip("\n")):
            break


async def run_plain(args) -> None:
    session = Session(args, PlainUI())
    await session.connect()
    net = asyncio.create_task(session.conn.run())
    keys = asyncio.create_task(input_loop(session))
    await asyncio.wait({net, keys}, return_when=asyncio.FIRST_COMPLETED)
    session.conn.close()


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
    p.add_argument("--plain", action="store_true", help="plain scrolling terminal instead of the split-screen interface")
    args = p.parse_args()
    if not args.plain:
        try:
            from .tui import HexApp
        except ImportError:
            print(f"{DIM}Plain mode. For the map and panels: uv run python -m hex, or pip install textual{RST}")
        else:
            HexApp(args).run()
            return
    try:
        asyncio.run(run_plain(args))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
