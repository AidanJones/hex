import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from hex import llm
from hex.env import load_dotenv


class EnvTests(unittest.TestCase):
    def test_load_dotenv(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / ".env"
            p.write_text('# comment\nHEX_T1=abc  # note\nexport HEX_T2="x y"\nHEX_T3=keep\n')
            with mock.patch.dict(os.environ, {"HEX_T3": "real"}, clear=False):
                load_dotenv(p)
                self.assertEqual(os.environ["HEX_T1"], "abc")
                self.assertEqual(os.environ["HEX_T2"], "x y")
                self.assertEqual(os.environ["HEX_T3"], "real")  # real env wins
            for k in ("HEX_T1", "HEX_T2"):
                os.environ.pop(k, None)


class LLMTests(unittest.TestCase):
    def test_strip_thinking(self):
        self.assertEqual(llm.strip_thinking('<think>{hmm}</think>\n{"a":1}'), '{"a":1}')
        self.assertEqual(llm.strip_thinking('<think>ran out of tok'), "")

    def test_minimax_request(self):
        sent = {}

        def fake_post(url, headers, payload, timeout=60):
            sent.update(url=url, headers=headers, payload=payload)
            return {"choices": [{"message": {"content": '<think>x</think>{"summary":"ok"}'}}]}

        with mock.patch.dict(os.environ, {"MINIMAX_API_KEY": "k"}), \
             mock.patch.object(llm, "_post", fake_post):
            os.environ.pop("HEX_MODEL", None)
            os.environ.pop("HEX_BASE_URL", None)
            out = llm.make_llm("minimax").complete("sys", "user")
        self.assertEqual(out, '{"summary":"ok"}')
        self.assertEqual(sent["url"], "https://api.minimax.io/v1/chat/completions")
        self.assertEqual(sent["headers"]["authorization"], "Bearer k")
        self.assertEqual(sent["payload"]["model"], "MiniMax-M2.5")
        self.assertGreaterEqual(sent["payload"]["max_tokens"], 4000)

    def test_minimax_needs_key(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(llm.LLMError):
                llm.make_llm("minimax")


if __name__ == "__main__":
    unittest.main()
