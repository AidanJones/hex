"""Split-screen front end (needs Textual): game on the left; map, vitals and Hex on the right.

Hard rule: nothing here sends to the MUD except Enter in the input box or F1-F5.
Timers and GMCP updates only ever redraw.
"""
from __future__ import annotations

import subprocess
import sys

from rich.ansi import AnsiDecoder
from rich.errors import StyleSyntaxError
from rich.style import Style
from rich.text import Text
from textual.actions import SkipAction
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.content import Content
from textual.strip import Strip
from textual.suggester import Suggester
from textual.widgets import Input, RichLog, Static

from .advisor import Advice
from .minimap import parse_map
from .session import Session, UI

NOTE_STYLES = {"dim": "dim", "head": "cyan", "error": "red", "": ""}
PLAYER = "yellow"           # Discworld colours the player's room yellow
MIN_WIDTH_FOR_PANELS = 90
PLACEHOLDER = "Type to the game, /a to ask Hex, /help for more"


def render_map(raw: object) -> Text:
    lines = parse_map(raw)
    width = max((sum(len(t) for t, _ in segs) for segs in lines), default=0)
    out = Text(no_wrap=True, overflow="crop")
    for i, segs in enumerate(lines):
        if i:
            out.append("\n")
        for text, colour in segs:
            out.append(text, _map_style(colour))
        out.append(" " * (width - sum(len(t) for t, _ in segs)))
    return out


def _map_style(colour: str | None) -> Style:
    if colour is None:
        return Style(dim=True)
    try:
        return Style.parse(f"bold {colour}" if colour == PLAYER else colour)
    except StyleSyntaxError:
        return Style.null()


def bar(label: str, value: object, top: object, width: int = 20) -> Text:
    if not isinstance(value, (int, float)) or not isinstance(top, (int, float)) or top <= 0:
        return Text(f"{label} ?", style="dim")
    share = max(0.0, min(1.0, value / top))
    colour = "green" if share > 0.5 else "yellow" if share > 0.25 else "red"
    filled = round(share * width)
    out = Text(f"{label} ")
    out.append("█" * filled, colour)
    out.append("░" * (width - filled), "dim")
    out.append(f" {value}/{top}")
    return out


def render_vitals(v: dict, score: dict | None = None) -> Text:
    score = score or {}
    v = {**score, **v}                  # live GMCP numbers beat the last `score`
    if not v:
        return Text("Waiting for the game…", style="dim")
    rows = [bar("HP", v.get("hp"), v.get("maxhp")), bar("GP", v.get("gp"), v.get("maxgp"))]
    extra = []
    if "burden" in v:
        extra.append(f"Burden {v['burden']}%")
    if isinstance(v.get("xp"), (int, float)):
        extra.append(f"XP {v['xp']:,}")
    if extra:
        rows.append(Text("  ".join(extra)))
    if v.get("alignment"):
        rows.append(Text(str(v["alignment"]), style="dim"))
    if "lives_left" in score:
        left = score["lives_left"]
        rows.append(Text(f"Lives left {left}  Deaths {score.get('deaths', '?')}",
                         style="red" if isinstance(left, int) and left <= 2 else ""))
    if "guild" in score:
        rows.append(Text(f"{score['guild']} level {score.get('level', '?')}"))
    if "quest_points" in score:
        rows.append(Text(f"Quest points {score['quest_points']}/{score.get('quest_max', '?')}"))
    return Text("\n").join(rows)


def render_advice(a: Advice) -> Text:
    out = Text()
    if a.summary:
        out.append(f"{a.summary}\n\n")
    if a.explain:
        out.append(f"{a.explain}\n\n", "dim")
    for i, s in enumerate(a.suggestions, 1):
        out.append(f"F{i} ", "bold yellow")
        out.append(f"{s['command']}\n", "bold")
        if s.get("why"):
            out.append(f"   {s['why']}\n", "dim")
    if a.watch_out:
        out.append(f"\n⚠ {a.watch_out}\n", "red")
    return out


class GameLog(RichLog):
    """RichLog with mouse selection, which Textual only provides on its plain Log."""

    ALLOW_SELECT = True

    def get_selection(self, selection):
        return selection.extract("\n".join(line.text for line in self.lines)), "\n"

    def selection_updated(self, selection) -> None:
        self._line_cache.clear()
        self.refresh()

    def _render_line(self, y: int, scroll_x: int, width: int) -> Strip:
        line = super()._render_line(y, scroll_x, width)
        selection = self.text_selection
        span = selection.get_span(y) if selection and y < len(self.lines) else None
        if span:
            start, end = span
            end = self.lines[y].cell_length if end == -1 else end
            before, chosen, after = line.divide([start, end, line.cell_length])
            style = self.screen.get_component_rich_style("screen--selection")
            line = Strip.join([before, chosen.apply_style(style), after])
        return line.apply_offsets(scroll_x, y)


class ContextSuggester(Suggester):
    def __init__(self, app: "HexApp") -> None:
        super().__init__(use_cache=False, case_sensitive=True)     # the context keeps changing
        self.hex_app = app

    async def get_suggestion(self, value: str) -> str | None:
        return self.hex_app.session.complete(value, self.hex_app.query_one(CommandInput).past)


class CommandInput(Input):
    BINDINGS = [Binding("up", "history(-1)", show=False), Binding("down", "history(1)", show=False),
                Binding("tab", "accept_suggestion", show=False)]

    def __init__(self, **kw) -> None:
        super().__init__(**kw)
        self.past: list[str] = []
        self.past_pos = 0

    def remember(self, line: str) -> None:
        if line and line != (self.past[-1] if self.past else None):
            self.past.append(line)
        self.past_pos = len(self.past)

    def action_accept_suggestion(self) -> None:
        if self._suggestion:
            self.value = self._suggestion
            self.cursor_position = len(self.value)

    def action_history(self, step: int) -> None:
        if self.password or not self.past:
            return
        self.past_pos = max(0, min(len(self.past), self.past_pos + step))
        self.value = self.past[self.past_pos] if self.past_pos < len(self.past) else ""
        self.cursor_position = len(self.value)


class HexApp(App, UI):
    TITLE = "Hex"
    ENABLE_COMMAND_PALETTE = False
    CSS = """
    #game { width: 1fr; }
    #log { height: 1fr; border: round $primary-darken-2; overflow-x: hidden; scrollbar-size-vertical: 1; }
    #input { border: round $accent; }
    #side { width: 44; }
    #map { height: 15; border: round $primary-darken-2; content-align: center middle; }
    #vitals { height: auto; min-height: 3; border: round $primary-darken-2; padding: 0 1; }
    #hex { height: 1fr; border: round $accent; padding: 0 1; scrollbar-size-vertical: 1; }
    """
    BINDINGS = [
        *[Binding(f"f{n}", f"pick({n})", show=False, priority=True) for n in range(1, 6)],
        Binding("f6", "ask", show=False, priority=True),
        Binding("ctrl+c", "copy_selection", show=False, priority=True),
        Binding("pageup", "scroll_log(-1)", show=False, priority=True),
        Binding("pagedown", "scroll_log(1)", show=False, priority=True),
    ]

    def __init__(self, args, rooms=None) -> None:
        super().__init__()
        self.session = Session(args, self, rooms)
        self.decoder = AnsiDecoder()
        self.partial = ""
        self.partial_timer = None

    def compose(self) -> ComposeResult:
        with Horizontal():
            with Vertical(id="game"):
                yield GameLog(id="log", wrap=True, max_lines=5000, min_width=20)
                yield CommandInput(id="input", placeholder=PLACEHOLDER, suggester=ContextSuggester(self))
            with Vertical(id="side"):
                yield Static(Text("No map yet", style="dim"), id="map")
                yield Static(render_vitals({}), id="vitals")
                with VerticalScroll(id="hex"):
                    yield Static(Text("/a or F6 to ask Hex what to do next.", style="dim"), id="advice")

    def on_mount(self) -> None:
        log = self.query_one("#log", RichLog)
        log.can_focus = False
        self.query_one("#hex").can_focus = False
        log.border_title = "Discworld"
        self.query_one("#map").border_title = "Map"
        self.query_one("#vitals").border_title = "Vitals"
        self.query_one("#hex").border_title = "Hex"
        self.query_one("#input").focus()
        self.run_worker(self.play(), exclusive=True)

    async def play(self) -> None:
        try:
            await self.session.connect()
            await self.session.conn.run()
        except OSError as e:
            self.show_note(f"Connection failed: {e}", "error")
            return
        self.flush_partial()
        self.show_note("Disconnected. Ctrl+Q to leave.", "error")

    def on_unmount(self) -> None:
        self.session.conn.close()

    def on_resize(self, event) -> None:
        self.query_one("#side").display = event.size.width >= MIN_WIDTH_FOR_PANELS

    # ---- keys -----------------------------------------------------------
    async def on_input_submitted(self, event: Input.Submitted) -> None:
        box = self.query_one(CommandInput)
        line, hidden = event.value, self.session.state.hidden_input
        box.value = ""
        if not hidden:
            box.remember(line)
            self.flush_partial()
            self.show_note(f"> {line}", "dim")
        if not await self.session.handle(line):
            self.exit()

    def action_pick(self, n: int) -> None:
        self.session.pick(n)

    def action_ask(self) -> None:
        self.run_worker(self.session.ask(""))

    def action_copy_selection(self) -> None:
        text = self.screen.get_selected_text()
        if not text:
            raise SkipAction()              # let the input box copy its own selection
        self.copy_to_clipboard(text)
        if sys.platform == "win32":         # not every Windows terminal honours the escape code
            subprocess.run("clip", input=text.encode("utf-16-le"), shell=True, check=False)
        self.screen.clear_selection()
        self.notify("Copied", timeout=1.5)

    def on_mouse_up(self) -> None:
        self.query_one(CommandInput).focus()    # keep typing after selecting text

    def action_scroll_log(self, direction: int) -> None:
        log = self.query_one("#log", RichLog)
        if direction < 0:
            log.scroll_page_up()
        else:
            log.scroll_page_down()

    # ---- drawing ---------------------------------------------------------
    def _write(self, content) -> None:
        log = self.query_one("#log", RichLog)
        log.write(content, scroll_end=log.is_vertical_scroll_end)   # don't yank you back down

    def show_text(self, text: str) -> None:
        *lines, self.partial = (self.partial + text.replace("\r", "")).split("\n")
        for line in lines:
            self._write(self.decoder.decode_line(line))
        if self.partial_timer:
            self.partial_timer.stop()
        # Some prompts ("Enter your name:") have no newline, so show them after a beat.
        self.partial_timer = self.set_timer(0.2, self.flush_partial) if self.partial else None

    def flush_partial(self) -> None:
        if self.partial:
            self._write(self.decoder.decode_line(self.partial))
            self.partial = ""

    def show_prompt(self) -> None:
        self.flush_partial()

    def show_note(self, msg: str, kind: str = "") -> None:
        self._write(Text(msg, style=NOTE_STYLES[kind]))

    def show_advice(self, advice: Advice) -> None:
        self.query_one("#advice", Static).update(render_advice(advice))
        self.query_one("#hex").scroll_home(animate=False)

    def show_thinking(self, busy: bool) -> None:
        self.query_one("#hex").border_title = "Hex · thinking…" if busy else "Hex"

    def show_hidden(self, hidden: bool) -> None:
        box = self.query_one(CommandInput)
        box.password = hidden
        box.placeholder = "Password (hidden)" if hidden else PLACEHOLDER

    def show_state(self) -> None:
        s = self.session.state
        self.query_one("#vitals", Static).update(render_vitals(s.gmcp.get("char.vitals") or {}, s.score))
        panel = self.query_one("#map", Static)
        if "room.map" in s.gmcp:
            panel.update(render_map(s.gmcp["room.map"]))
        room = s.gmcp.get("room.info")
        name = room.get("name") if isinstance(room, dict) else None
        panel.border_title = Content(name or "Map")
        panel.border_subtitle = Content(", ".join(s.exits)) if s.exits else None
