import asyncio
import json
import unittest
from pathlib import Path

from hex.advisor import Advice, Advisor
from hex.knowledge import KnowledgeBase
from hex.state import GameState
from hex.telnet import (DO, GA, IAC, OPT_ECHO, OPT_GMCP, SB, SE, WILL, WONT,
                              MudConnection, TelnetParser, gmcp_frame)

ROOT = Path(__file__).resolve().parent.parent


class TelnetTests(unittest.TestCase):
    def test_text_and_prompt(self):
        evs, _ = TelnetParser().feed(b"Hello\r\nName: " + bytes([IAC, GA]))
        self.assertEqual([e.kind for e in evs], ["text", "prompt"])

    def test_gmcp_split_across_reads(self):
        p = TelnetParser()
        frame = gmcp_frame("room.info", {"identifier": "abc", "name": "Pumpkin Town"})
        evs1, _ = p.feed(frame[:7])
        evs2, _ = p.feed(frame[7:])
        g = [e for e in evs1 + evs2 if e.kind == "gmcp"]
        self.assertEqual(g[0].package, "room.info")
        self.assertEqual(g[0].data["name"], "Pumpkin Town")

    def test_negotiation(self):
        p = TelnetParser()
        evs, reply = p.feed(bytes([IAC, DO, OPT_GMCP, IAC, WILL, OPT_ECHO]))
        self.assertIn(bytes([IAC, WILL, OPT_GMCP]), reply)
        self.assertEqual([e.kind for e in evs], ["gmcp_ready", "echo_off"])
        evs, _ = p.feed(bytes([IAC, WONT, OPT_ECHO]))
        self.assertEqual(evs[0].kind, "echo_on")

    def test_server_offering_gmcp_gets_a_hello(self):
        p = TelnetParser()
        evs, reply = p.feed(bytes([IAC, WILL, OPT_GMCP]))
        self.assertEqual(reply, bytes([IAC, DO, OPT_GMCP]))
        self.assertEqual([e.kind for e in evs], ["gmcp_ready"])
        evs, _ = p.feed(bytes([IAC, DO, OPT_GMCP]))
        self.assertEqual(evs, [])                            # only once

    def test_escaped_iac(self):
        evs, _ = TelnetParser().feed(b"a" + bytes([IAC, IAC]) + b"b")
        self.assertEqual(evs[0].text.encode("utf-8", "surrogateescape")[:1], b"a")


class StateTests(unittest.TestCase):
    def test_password_hidden(self):
        s = GameState()
        s.hidden_input = True
        s.add_command("hunter2")
        self.assertNotIn("hunter2", s.recent())

    def test_room_change(self):
        s = GameState()
        self.assertTrue(s.add_gmcp("room.info", {"identifier": "1"}))
        self.assertFalse(s.add_gmcp("room.info", {"identifier": "1"}))
        self.assertTrue(s.add_gmcp("room.info", {"identifier": "2"}))

    def test_partial_lines(self):
        s = GameState()
        s.add_text("You are in a str")
        s.add_text("eet.\nExits: north\n")
        self.assertEqual(s.recent(), "You are in a street.\nExits: north")


class KnowledgeTests(unittest.TestCase):
    def test_search_finds_death_notes(self):
        kb = KnowledgeBase(ROOT / "knowledge" / "core")
        hits = kb.search("I died, where is my corpse")
        self.assertTrue(any("Death" in c.title for c in hits))


class FakeLLM:
    def __init__(self):
        self.last_user = ""

    def complete(self, system, user, max_tokens=900):
        self.last_user = user
        return 'Sure! {"summary":"You are in Pumpkin Town.","suggestions":' \
               '[{"command":"look","why":"see around"}],"watch_out":""}'


class AdvisorTests(unittest.TestCase):
    def test_parse_and_prompt(self):
        llm = FakeLLM()
        s = GameState()
        s.add_text("You are standing in Pumpkin Town square.\n")
        a = Advisor(llm, KnowledgeBase(ROOT / "knowledge" / "core")).advise(s, "how do I learn skills?")
        self.assertEqual(a.suggestions[0]["command"], "look")
        self.assertIn("Pumpkin Town square", llm.last_user)
        self.assertIn("teach", llm.last_user)  # retrieved progression notes

    def test_parse_garbage(self):
        self.assertEqual(Advice.parse("no json here").summary, "no json here")


class EndToEnd(unittest.IsolatedAsyncioTestCase):
    async def test_fake_mud(self):
        got_client = asyncio.Event()
        received = bytearray()

        async def handler(r, w):
            w.write(bytes([IAC, DO, OPT_GMCP]))
            w.write(b"Welcome to the Disc.\r\n")
            w.write(gmcp_frame("char.vitals", {"hp": 300, "maxhp": 300}))
            await w.drain()
            received.extend(await r.read(1024))
            got_client.set()
            w.close()

        srv = await asyncio.start_server(handler, "127.0.0.1", 0)
        port = srv.sockets[0].getsockname()[1]
        events = []
        conn = MudConnection("127.0.0.1", port, on_event=events.append)
        await conn.connect()
        await asyncio.wait_for(conn.run(), 3)
        conn.close()
        srv.close()
        self.assertIn(bytes([IAC, WILL, OPT_GMCP]), bytes(received))
        self.assertIn(b"Core.Supports.Set", bytes(received))
        self.assertTrue(any(e.kind == "gmcp" and e.package == "char.vitals" for e in events))


if __name__ == "__main__":
    unittest.main()
