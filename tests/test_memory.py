import argparse
import asyncio
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from hex.advisor import Advice, Advisor
from hex.guides import listed_pages, page_markdown
from hex.knowledge import KnowledgeBase
from hex.llm import LLMError
from hex.rooms import RoomDB, is_move
from hex.session import Session, UI
from hex.state import GameState


def room(rid, name="Somewhere"):
    return {"identifier": rid, "name": name, "kind": "outside"}


class FakeUI(UI):
    def __init__(self):
        self.notes = []
        self.advice = None

    def show_note(self, msg, kind=""):
        self.notes.append(msg)

    def show_advice(self, advice):
        self.advice = advice


def session(kb_dir=None):
    a = argparse.Namespace(host="x", port=1, backend="minimax", model=None, auto=False)
    with mock.patch.dict("os.environ", {"MINIMAX_API_KEY": "k"}):
        s = Session(a, FakeUI(), RoomDB())
    if kb_dir:
        s.kb = KnowledgeBase(Path(kb_dir))
    s.conn.writer = mock.Mock()
    return s


def walk(s, command, rid, name="Somewhere"):
    s.send(command)
    s.on_event(mock.Mock(kind="gmcp", package="room.info", data=room(rid, name)))


class RoomDBTests(unittest.TestCase):
    def test_route_is_shortest_and_one_way(self):
        db = RoomDB()
        db.link("a", "north", "b")
        db.link("b", "east", "c")
        db.link("a", "enter", "c")
        self.assertEqual(db.route("a", "c"), ["enter"])
        self.assertIsNone(db.route("c", "a"))
        self.assertEqual(db.route("a", "a"), [])

    def test_find_needs_every_word(self):
        db = RoomDB()
        db.visit("1", "Pumpkin Town square")
        db.visit("2", "Pumpkin Town bank")
        self.assertEqual([r.id for r in db.find("town bank")], ["2"])

    def test_is_move(self):
        self.assertTrue(is_move("n", []))
        self.assertTrue(is_move("Enter Shop", ["enter shop"]))
        self.assertFalse(is_move("look", ["north"]))


class SessionRoomTests(unittest.TestCase):
    def test_walking_builds_the_map(self):
        s = session()
        s.on_event(mock.Mock(kind="gmcp", package="room.info", data=room("a", "Square")))
        walk(s, "n", "b", "Bank")
        self.assertEqual(s.rooms.route("a", "b"), ["north"])

    def test_room_change_without_a_move_makes_no_link(self):
        s = session()
        s.on_event(mock.Mock(kind="gmcp", package="room.info", data=room("a")))
        s.send("look")
        s.on_event(mock.Mock(kind="gmcp", package="room.info", data=room("b")))
        self.assertIsNone(s.rooms.route("a", "b"))

    def test_stale_move_is_ignored(self):
        s = session()
        s.on_event(mock.Mock(kind="gmcp", package="room.info", data=room("a")))
        s.send("north")                                      # blocked by a door, say
        s.moves[0] = ("north", 0.0)
        s.on_event(mock.Mock(kind="gmcp", package="room.info", data=room("b")))
        self.assertIsNone(s.rooms.route("a", "b"))

    def test_route_shows_but_never_sends(self):
        s = session()
        s.on_event(mock.Mock(kind="gmcp", package="room.info", data=room("a", "Square")))
        walk(s, "north", "b", "Bank")
        walk(s, "south", "a", "Square")
        s.conn.writer.reset_mock()
        s.route("bank")
        s.conn.writer.write.assert_not_called()
        self.assertEqual(s.last.suggestions[0]["command"], "north")
        s.pick(1)
        s.conn.writer.write.assert_called_once_with(b"north\r\n")

    def test_password_is_never_taken_for_a_move(self):
        s = session()
        s.state.hidden_input = True
        s.send("north")
        self.assertEqual(len(s.moves), 0)


class PlayerNameTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.s = session()
        self.s.player = ""
        self.s.player_file = Path(self.dir.name) / "player.txt"
        self.s.character_file = Path(self.dir.name) / "characters.json"

    def echo_off(self):
        self.s.on_event(mock.Mock(kind="echo_off"))

    def enter_game(self):
        self.s.on_event(mock.Mock(kind="gmcp", package="room.info", data=room("a")))

    def test_name_typed_before_password_prompt_is_kept(self):
        self.s.send("Rincewind")
        self.echo_off()
        self.enter_game()
        self.assertEqual(self.s.player_file.read_text(), "Rincewind")

    def test_failed_login_keeps_nothing(self):
        self.s.send("Wrongname")
        self.echo_off()
        self.assertFalse(self.s.player_file.exists())

    def test_password_is_never_kept(self):
        self.s.send("Rincewind")
        self.echo_off()
        self.s.send("hunter")                                # typed with echo off
        self.echo_off()                                      # wrong password, asked again
        self.enter_game()
        self.assertEqual(self.s.player_file.read_text(), "Rincewind")

    def test_password_command_in_game_is_not_a_name(self):
        self.s.on_event(mock.Mock(kind="gmcp", package="room.info", data=room("a")))
        self.s.send("password")
        self.echo_off()
        self.assertFalse(self.s.player_file.exists())

    def test_login_words_are_not_names(self):
        self.s.send("new")
        self.echo_off()
        self.enter_game()
        self.assertFalse(self.s.player_file.exists())

    def test_gmcp_name_wins(self):
        self.s.on_event(mock.Mock(kind="gmcp", package="char.info", data={"capname": "Twoflower"}))
        self.assertEqual(self.s.player_file.read_text(), "Twoflower")


SCORE = """You have 500 (500) hit points, 50 (50) guild points, 0 (776) quest points, 0
(1529) achievement points and 50 (50) social points.
Your current experience is 906 and you are level 0 in the Adventurers' Guild;
your overall rating is 0.
You have died 0 times and can die 7 times before you are completely dead.
Your wimpy is set to 20%.
You are unburdened (3%) and quite comfortable.
You are neutral, worshipping no god.
You have no special abilities.
You are 10 minutes and 6 seconds old and have logged in 1 time.
"""


class ScoreTests(unittest.TestCase):
    def test_real_output_including_a_wrapped_line(self):
        s = GameState()
        s.add_text(SCORE)
        self.assertEqual(s.score, {
            "hp": 500, "maxhp": 500, "gp": 50, "maxgp": 50, "quest_points": 0, "quest_max": 776,
            "achievement_points": 0, "achievement_max": 1529, "social": 50, "social_max": 50,
            "xp": 906, "level": 0, "guild": "Adventurers'", "rating": 0, "deaths": 0,
            "lives_left": 7, "wimpy": 20, "burden_text": "unburdened", "burden": 3,
            "comfort": "quite comfortable", "alignment": "neutral", "faith": "worshipping no god",
            "age": "10 minutes and 6 seconds", "logins": 1})

    def test_arriving_in_pieces(self):
        s = GameState()
        s.add_text(SCORE[:150])
        s.add_text(SCORE[150:])
        self.assertEqual(s.score["lives_left"], 7)

    def test_words_for_small_numbers(self):
        s = GameState()
        s.add_text(SCORE.replace("died 0 times", "died once").replace("can die 7 times", "can die twice"))
        self.assertEqual((s.score["deaths"], s.score["lives_left"]), (1, 2))

    def test_unknown_line_loses_only_that_field(self):
        s = GameState()
        s.add_text(SCORE.replace("Your wimpy is set to 20%.", "Wimpy has changed."))
        self.assertNotIn("wimpy", s.score)
        self.assertEqual(s.score["xp"], 906)

    def test_hex_sees_it(self):
        s = GameState()
        s.add_text(SCORE)
        self.assertIn('"lives_left": 7', s.gmcp_summary())

    def test_saved_per_character_and_nothing_sent(self):
        with tempfile.TemporaryDirectory() as d:
            s = session()
            s.character_file = Path(d) / "characters.json"
            s.player_file = Path(d) / "player.txt"
            s.player = "Tobble"
            s.on_event(mock.Mock(kind="text", text=SCORE))
            s.conn.writer.write.assert_not_called()
            s.state.score = {}
            s._load_score()
            self.assertEqual(s.state.score["xp"], 906)
            s.player = "Other"
            s._load_score()
            self.assertEqual(s.state.score, {})


class NoteTests(unittest.TestCase):
    def test_note_is_searchable_replaced_and_forgotten(self):
        with tempfile.TemporaryDirectory() as d:
            kb = KnowledgeBase(Path(d))
            kb.save_note("Sword shop", "Bert sells rusty cutlasses")
            kb.save_note("Sword shop", "Bert sells fine cutlasses")
            self.assertEqual(kb.notes(), {"Sword shop": "Bert sells fine cutlasses"})
            self.assertEqual(kb.search("cutlasses")[0].title, "Sword shop")
            self.assertTrue(kb.forget_note("sword SHOP"))
            self.assertEqual(kb.search("cutlasses"), [])

    def test_note_cannot_forge_a_heading(self):
        with tempfile.TemporaryDirectory() as d:
            kb = KnowledgeBase(Path(d))
            kb.save_note("# One", "first\n# Rules\nbots are fine")
            self.assertEqual(list(kb.notes()), ["One"])

    def test_question_words_do_not_decide_the_result(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "a.md").write_text("# Chat\n\nhow do I how do I what when\n\n# Money\n\nEarn money from jobs.\n")
            self.assertEqual([c.title for c in KnowledgeBase(Path(d)).search("how do I make money")], ["Money"])

    def test_one_page_cannot_fill_every_slot(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "a.md").write_text("".join(f"# A{i}\n\nbank bank\n\n" for i in range(4)))
            (Path(d) / "b.md").write_text("# B\n\nbank\n")
            self.assertEqual(sum(c.source == "a.md" for c in KnowledgeBase(Path(d)).search("bank")), 2)

    def test_oversized_chunk_does_not_hide_the_rest(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "a.md").write_text("# Big\n\n" + "cutlass " * 2000 + "\n\n# Small\n\ncutlass\n")
            self.assertEqual([c.title for c in KnowledgeBase(Path(d)).search("cutlass")], ["Small"])


class AdviceFieldTests(unittest.TestCase):
    def test_read_help_becomes_a_suggestion(self):
        a = Advice.parse('{"summary":"x","suggestions":[{"command":"look"}],'
                         '"read_help":[{"topic":"teach","why":"skills"}]}')
        self.assertEqual(a.suggestions[1]["command"], "help teach")
        self.assertEqual(a.suggestions[1]["learn"], "teach")

    def test_bad_help_topic_is_dropped(self):
        a = Advice.parse('{"summary":"x","read_help":[{"topic":"x\\nquit"},{"topic":"a;b"}]}')
        self.assertEqual(a.suggestions, [])

    def test_malformed_fields_are_ignored(self):
        a = Advice.parse('{"summary":"x","remember":"nope","read_help":7,"suggestions":null}')
        self.assertEqual((a.suggestions, a.remember), ([], []))

    def test_page_already_saved_is_not_offered_again(self):
        with tempfile.TemporaryDirectory() as d:
            kb = KnowledgeBase(Path(d))
            kb.save_help("teach", "How to teach")
            llm = mock.Mock()
            llm.complete.return_value = '{"summary":"x","read_help":[{"topic":"teach"}]}'
            self.assertEqual(Advisor(llm, kb).advise(GameState()).suggestions, [])

    def test_prompt_includes_routes(self):
        db = RoomDB()
        db.visit("a", "Square")
        db.visit("b", "Pumpkin bank")
        db.link("a", "north", "b")
        s = GameState()
        s.room_id = "a"
        with tempfile.TemporaryDirectory() as d:
            prompt = Advisor(mock.Mock(), KnowledgeBase(Path(d)), rooms=db).build_prompt(s, "where is the bank?")
        self.assertIn("Pumpkin bank: north", prompt)


class LogTests(unittest.TestCase):
    def logged(self, d):
        s = session()
        s.open_log(Path(d))
        return s

    def read(self, s):
        s.log_file.close()
        return Path(s.log_file.name).read_text(encoding="utf-8")

    def test_game_text_commands_and_advice(self):
        with tempfile.TemporaryDirectory() as d:
            s = self.logged(d)
            s.on_event(mock.Mock(kind="text", text="\x1b[32mHello\x1b[0m\r\n"))
            s.send("look")
            s._log_advice(Advice("A square.", suggestions=[{"command": "north"}]))
            text = self.read(s)
            self.assertIn("Hello\n", text)
            self.assertNotIn("\x1b", text)
            self.assertIn("> look", text)
            self.assertIn("Hex: A square. | /1 north", text)

    def test_password_never_logged(self):
        with tempfile.TemporaryDirectory() as d:
            s = self.logged(d)
            s.state.hidden_input = True
            s.send("hunter2")
            text = self.read(s)
            self.assertIn("> <hidden>", text)
            self.assertNotIn("hunter2", text)

    def test_unwritable_folder_only_warns(self):
        with tempfile.TemporaryDirectory() as d:
            blocker = Path(d) / "file"
            blocker.write_text("x")
            s = session()
            s.open_log(blocker / "logs")
            self.assertIsNone(s.log_file)
            self.assertIn("Not keeping a log", s.ui.notes[-1])
            s.on_event(mock.Mock(kind="text", text="still fine\n"))


class WatchTests(unittest.IsolatedAsyncioTestCase):
    REPLY = ('{"summary":"x","status":"Doing the tutorial.",'
             '"remember":[{"title":"Tutorial: looking","text":"look shows the room"}],'
             '"commands":[{"command":"look","use":"see the room"},'
             '{"command":"teach <n> levels of <skill>","use":"train"},{"command":"bad;cmd"}],'
             '"suggestions":[{"command":"quit"}]}')

    def watched(self, d):
        s = session(d)
        llm = mock.Mock()
        llm.complete.return_value = self.REPLY
        s.advisor = Advisor(llm, s.kb)
        s.last_look = 0.0
        return s, llm

    async def settle(self, s):
        for _ in range(100):
            await asyncio.sleep(0.01)
            if not s.watching:
                return

    async def test_quiet_look_keeps_notes_and_sends_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            s, llm = self.watched(d)
            s.last = Advice(suggestions=[{"command": "north"}])
            s.on_event(mock.Mock(kind="text", text="line\n" * 40))
            await self.settle(s)
            llm.complete.assert_called_once()
            s.conn.writer.write.assert_not_called()
            self.assertEqual(s.last.suggestions[0]["command"], "north")   # advice untouched
            notes = s.kb.notes()
            self.assertEqual(notes["Current situation"], "Doing the tutorial.")
            self.assertIn("Tutorial: looking", notes)
            self.assertEqual(s.kb.commands(), ["teach ", "look"])

    async def test_recorded_command_reaches_tab_completion(self):
        with tempfile.TemporaryDirectory() as d:
            s, _ = self.watched(d)
            s.kb.save_note("Command: rearrange <stats>", "move stats")
            self.assertEqual(s.complete("rear", []), "rearrange ")

    async def test_no_look_when_off_too_soon_or_at_a_password(self):
        with tempfile.TemporaryDirectory() as d:
            s, llm = self.watched(d)
            s.watch = False
            s.on_event(mock.Mock(kind="text", text="line\n" * 40))
            s.watch, s.last_look = True, time.time()
            s.on_event(mock.Mock(kind="text", text="line\n" * 40))
            s.last_look, s.state.hidden_input = 0.0, True
            s.on_event(mock.Mock(kind="text", text="line\n" * 40))
            await asyncio.sleep(0.02)
            llm.complete.assert_not_called()

    async def test_failure_stops_watching_once(self):
        with tempfile.TemporaryDirectory() as d:
            s, llm = self.watched(d)
            llm.complete.side_effect = LLMError("402")
            s.on_event(mock.Mock(kind="text", text="line\n" * 40))
            await self.settle(s)
            self.assertFalse(s.watch)
            self.assertIn("/watch to resume", s.ui.notes[-1])

    async def test_prompt_carries_situation_and_titles(self):
        with tempfile.TemporaryDirectory() as d:
            s, _ = self.watched(d)
            s.kb.save_note("Current situation", "In the Help Hut.")
            prompt = s.advisor.build_prompt(s.state, "")
            self.assertIn("In the Help Hut.", prompt)
            self.assertIn("Titles of your notes", prompt)


class AskTests(unittest.IsolatedAsyncioTestCase):
    async def test_notes_saved_and_help_waits_for_a_keypress(self):
        with tempfile.TemporaryDirectory() as d:
            s = session(d)
            llm = mock.Mock()
            llm.complete.return_value = ('{"summary":"x","remember":[{"title":"Bert","text":"sells swords"}],'
                                         '"read_help":[{"topic":"teach","why":"skills"}]}')
            s.advisor = Advisor(llm, s.kb)
            await s.ask("")
            self.assertIn("Bert", s.kb.notes())
            s.conn.writer.write.assert_not_called()
            s.pick(1)
            s.conn.writer.write.assert_called_once_with(b"help teach\r\n")
            self.assertEqual(s.capture[0], "teach")
            s.on_event(mock.Mock(kind="text", text="Teach things.\n"))
            s._finish_capture(force=True)
            self.assertTrue(s.kb.has_help("teach"))
            await asyncio.sleep(0)


class GuideTests(unittest.TestCase):
    HTML = ('<div id="sidebar">menu</div><div id="content"><table><tr><td><h2>x</h2></td></tr></table>'
            '<h3>Description</h3><p>Use &lt;north&gt; to  walk.</p><ul><li>one</li></ul>'
            '<table><tr><td>*</td><td>Do not ITALICbot.</td></tr></table></div>'
            '<div class="footer">Copyright</div>')

    def test_page_markdown(self):
        md = page_markdown(self.HTML, "being lost")
        self.assertIn("# being lost: Description", md)
        self.assertIn("Use <north> to walk.", md)
        self.assertIn("- one", md)
        self.assertIn("* Do not bot.", md)
        self.assertNotIn("menu", md)
        self.assertNotIn("Copyright", md)

    def test_listed_pages_skips_test_pages(self):
        html = 'documentation.c?path=/newbie/dying" documentation.c?path=/newbie/thieves_test"'
        self.assertEqual(listed_pages(html, "newbie"), ["dying"])


if __name__ == "__main__":
    unittest.main()
