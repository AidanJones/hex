import argparse
import asyncio
import unittest
from unittest import mock

from hex.advisor import Advice
from hex.minimap import parse_map, plain_map
from hex.rooms import RoomDB
from hex.session import Session, UI
from hex.state import GameState
from hex.telnet import IAC, OPT_ECHO, WILL, WONT, gmcp_frame

try:
    from hex.tui import HexApp, render_map, render_vitals
except ImportError:       # Textual is optional
    HexApp = None

# Real packet shape, from the RossAsciiMap plugin's notes.
ROOM_MAP = ("\x1b[3z\x1b[4zMXP<RedMXP>+\x1b[3z     \x1b[3z\x1b[4zMXP<RedMXP>+\x1b[3z   \n"
            "\x1b[3z\x1b[4zMXP<GreenMXP>&\x1b[3z-\x1b[3z\x1b[4zMXP<CyanMXP>*\x1b[3z-"
            "\x1b[3z\x1b[4zMXP<YellowMXP>@\x1b[3z-\x1b[3z\x1b[4zMXP<GreenMXP>$\x1b[3z   \n"
            "       \x1b[3z\\\x1b[3z  \n"
            "        \x1b[3z\x1b[4zMXP<CyanMXP>*\x1b[3z-\x1b[3z\n")


def args(port=1):
    return argparse.Namespace(host="127.0.0.1", port=port, backend="minimax", model=None, auto=False, no_log=True)


class MinimapTests(unittest.TestCase):
    def test_plain_shape(self):
        self.assertEqual(plain_map(ROOM_MAP).split("\n"),
                         ["+     +   ", "&-*-@-$   ", "       \\  ", "        *-"])

    def test_colours(self):
        row = parse_map(ROOM_MAP)[1]
        self.assertIn(("@", "yellow"), row)
        self.assertIn(("-", None), row)

    def test_hex_colour_and_links(self):
        raw = 'MXP<send href="x"MXP>MXP<C #FF8700MXP>~\x1b[3z'
        self.assertEqual(parse_map(raw), [[("~", "#ff8700")]])

    def test_bad_payload(self):
        self.assertEqual(parse_map(None), [])


class StateTests(unittest.TestCase):
    def test_exits(self):
        s = GameState()
        s.add_text("There are three obvious exits: north, east and west.\n")
        self.assertEqual(s.exits, ["north", "east", "west"])

    def test_exits_wrapped_and_cleared_on_move(self):
        s = GameState()
        s.add_text("There are four obvious exits: north, south,\nnortheast and west.\nA cat is here.\n")
        self.assertEqual(s.exits, ["north", "south", "northeast", "west"])
        s.add_command("north")
        s.add_gmcp("room.info", {"identifier": "2"})
        self.assertEqual(s.exits, [])

    def test_exits_kept_when_room_text_beats_the_gmcp_packet(self):
        s = GameState()
        s.add_command("north")
        s.add_text("There is one obvious exit: south.\n")
        s.add_gmcp("room.info", {"identifier": "2"})
        self.assertEqual(s.exits, ["south"])

    def test_vitals_merge(self):
        s = GameState()
        s.add_gmcp("char.vitals", {"hp": 10, "maxhp": 20})
        s.add_gmcp("char.vitals", {"hp": 5})
        self.assertEqual(s.gmcp["char.vitals"], {"hp": 5, "maxhp": 20})


class PasswordTests(unittest.IsolatedAsyncioTestCase):
    async def test_slash_password_is_not_a_hex_command(self):
        with mock.patch.dict("os.environ", {"MINIMAX_API_KEY": "k"}):
            s = Session(args(), UI(), RoomDB())
        s.conn.writer = mock.Mock()
        s.advisor = mock.Mock()
        s.state.hidden_input = True
        await s.handle("/a hunter2")
        await asyncio.sleep(0)
        s.advisor.advise.assert_not_called()
        s.conn.writer.write.assert_called_once_with(b"/a hunter2\r\n")
        self.assertNotIn("hunter2", s.state.recent())

    async def test_suggestion_blocked_at_password_prompt(self):
        with mock.patch.dict("os.environ", {"MINIMAX_API_KEY": "k"}):
            s = Session(args(), UI(), RoomDB())
        s.conn.writer = mock.Mock()
        s.last = Advice(suggestions=[{"command": "look"}])
        s.state.hidden_input = True
        s.pick(1)
        s.conn.writer.write.assert_not_called()


@unittest.skipIf(HexApp is None, "Textual not installed")
class TuiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.received = bytearray()
        self.writer = None

        async def handler(r, w):
            self.writer = w
            while data := await r.read(1024):
                self.received.extend(data)

        self.srv = await asyncio.start_server(handler, "127.0.0.1", 0)
        self.port = self.srv.sockets[0].getsockname()[1]

    async def asyncTearDown(self):
        if self.writer:
            self.writer.close()
        self.srv.close()

    async def server_says(self, pilot, data):
        self.writer.write(data)
        await self.writer.drain()
        await asyncio.sleep(0.05)
        await pilot.pause()

    def app(self):
        with mock.patch.dict("os.environ", {"MINIMAX_API_KEY": "k"}):
            return HexApp(args(self.port), RoomDB())

    @staticmethod
    def log_text(app):
        return "\n".join(s.text for s in app.query_one("#log").lines)

    async def start(self, pilot):
        for _ in range(50):
            await pilot.pause(0.01)
            if self.writer:
                return
        self.fail("client never connected")

    async def test_panels_update_without_sending_anything(self):
        app = self.app()
        async with app.run_test(size=(120, 40)) as pilot:
            await self.start(pilot)
            await self.server_says(pilot, gmcp_frame("room.info", {"identifier": "a", "name": "Pumpkin Town"})
                                   + gmcp_frame("room.map", ROOM_MAP)
                                   + gmcp_frame("char.vitals", {"hp": 150, "maxhp": 300, "gp": 50, "maxgp": 50})
                                   + b"There are two obvious exits: north and east.\r\n")
            panel = app.query_one("#map")
            self.assertIn("&-*-@-$", render_map(app.session.state.gmcp["room.map"]).plain)
            self.assertEqual(str(panel.border_title), "Pumpkin Town")
            self.assertEqual(str(panel.border_subtitle), "north, east")
            self.assertIn("150/300", render_vitals(app.session.state.gmcp["char.vitals"]).plain)
            app.session.last = Advice(suggestions=[{"command": "look", "why": "see"}])
            app.show_advice(app.session.last)
            await pilot.pause(0.3)
            self.assertNotIn(b"look", bytes(self.received))     # shown, never sent

    async def test_f_key_sends_chosen_suggestion(self):
        app = self.app()
        async with app.run_test(size=(120, 40)) as pilot:
            await self.start(pilot)
            app.session.last = Advice(suggestions=[{"command": "look man"}])
            await pilot.press("f1")
            await asyncio.sleep(0.05)
            self.assertIn(b"look man\r\n", bytes(self.received))

    async def test_password_hidden_as_soon_as_server_asks(self):
        app = self.app()
        async with app.run_test(size=(120, 40)) as pilot:
            await self.start(pilot)
            box = app.query_one("#input")
            await self.server_says(pilot, b"Password: " + bytes([IAC, WILL, OPT_ECHO]))
            self.assertTrue(box.password)
            await pilot.press(*"hunter2", "enter")
            await asyncio.sleep(0.05)
            await pilot.pause()
            self.assertIn(b"hunter2\r\n", bytes(self.received))
            self.assertNotIn("hunter2", self.log_text(app))
            self.assertNotIn("hunter2", app.session.state.recent())
            self.assertEqual(box.past, [])
            await self.server_says(pilot, bytes([IAC, WONT, OPT_ECHO]))
            self.assertFalse(box.password)

    async def test_long_lines_wrap_inside_the_pane(self):
        app = self.app()
        async with app.run_test(size=(100, 30)) as pilot:
            await self.start(pilot)
            await self.server_says(pilot, b"word " * 60 + b"\n")
            log = app.query_one("#log")
            self.assertLessEqual(max(s.cell_length for s in log.lines), log.scrollable_content_region.width)

    async def test_mouse_selects_and_copies_game_text(self):
        app = self.app()
        async with app.run_test(size=(120, 40)) as pilot:
            await self.start(pilot)
            await self.server_says(pilot, b"Your name is Rincewind\r\n")
            log = app.query_one("#log")
            row = next(i for i, s in enumerate(log.lines) if "Rincewind" in s.text) + 1
            await pilot.mouse_down(log, offset=(14, row))
            await pilot.hover(log, offset=(23, row))
            await pilot.mouse_up(log, offset=(23, row))
            await pilot.pause()
            self.assertEqual(app.screen.get_selected_text(), "Rincewind")
            self.assertTrue(app.query_one("#input").has_focus)
            with mock.patch("hex.tui.subprocess.run"):
                await pilot.press("ctrl+c")
            self.assertEqual(app.clipboard, "Rincewind")

    async def test_score_appears_under_vitals(self):
        v = render_vitals({"hp": 400, "maxhp": 500},
                          {"hp": 500, "maxhp": 500, "lives_left": 2, "deaths": 5, "guild": "Witches",
                           "level": 3, "quest_points": 1, "quest_max": 776})
        self.assertIn("400/500", v.plain)                   # live number wins
        self.assertIn("Lives left 2  Deaths 5", v.plain)
        self.assertIn("Witches level 3", v.plain)
        self.assertIn("Quest points 1/776", v.plain)

    async def test_tab_completes_without_sending(self):
        app = self.app()
        async with app.run_test(size=(120, 40)) as pilot:
            await self.start(pilot)
            await self.server_says(pilot, b"There are two obvious exits: north and east.\r\n")
            await pilot.press("n", "o")
            await pilot.pause(0.1)
            await pilot.press("tab")
            await pilot.pause(0.1)
            box = app.query_one("#input")
            self.assertEqual(box.value, "north")
            self.assertTrue(box.has_focus)
            self.assertNotIn(b"north", bytes(self.received))

    async def test_no_completion_at_password_prompt(self):
        app = self.app()
        async with app.run_test(size=(120, 40)) as pilot:
            await self.start(pilot)
            await self.server_says(pilot, b"Password: " + bytes([IAC, WILL, OPT_ECHO]))
            await pilot.press("l", "o")
            await pilot.pause(0.1)
            await pilot.press("tab")
            await pilot.pause(0.1)
            self.assertEqual(app.query_one("#input").value, "lo")

    async def test_prompt_without_newline_appears(self):
        app = self.app()
        async with app.run_test(size=(120, 40)) as pilot:
            await self.start(pilot)
            await self.server_says(pilot, b"Enter your name: ")
            await pilot.pause(0.3)
            self.assertIn("Enter your name:", self.log_text(app))


if __name__ == "__main__":
    unittest.main()
