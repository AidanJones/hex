import argparse
import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from hex.advisor import Advice, Advisor
from hex.guides import listed_pages, page_markdown
from hex.knowledge import KnowledgeBase
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

    def echo_off(self):
        self.s.on_event(mock.Mock(kind="echo_off"))

    def test_name_typed_before_password_prompt_is_kept(self):
        self.s.send("Rincewind")
        self.echo_off()
        self.assertEqual(self.s.player_file.read_text(), "Rincewind")

    def test_password_is_never_kept(self):
        self.s.send("Rincewind")
        self.echo_off()
        self.s.send("hunter")                                # typed with echo off
        self.echo_off()                                      # wrong password, asked again
        self.assertEqual(self.s.player_file.read_text(), "Rincewind")

    def test_password_command_in_game_is_not_a_name(self):
        self.s.on_event(mock.Mock(kind="gmcp", package="room.info", data=room("a")))
        self.s.send("password")
        self.echo_off()
        self.assertFalse(self.s.player_file.exists())

    def test_login_words_are_not_names(self):
        self.s.send("new")
        self.echo_off()
        self.assertFalse(self.s.player_file.exists())

    def test_gmcp_name_wins(self):
        self.s.on_event(mock.Mock(kind="gmcp", package="char.info", data={"capname": "Twoflower"}))
        self.assertEqual(self.s.player_file.read_text(), "Twoflower")


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
