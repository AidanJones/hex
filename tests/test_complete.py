import unittest

from hex.complete import Context, complete, recent_words


class CompleteTests(unittest.TestCase):
    def test_exit_beats_a_verb(self):
        self.assertEqual(complete("ea", Context(exits=["east"])), "east")
        self.assertEqual(complete("ea", Context()), "eat")

    def test_hex_suggestion_comes_first(self):
        ctx = Context(suggestions=["look man"], history=["look bin"])
        self.assertEqual(complete("look ", ctx), "look man")

    def test_keeps_what_you_typed(self):
        self.assertEqual(complete("NO", Context(exits=["north"])), "NOrth")

    def test_noun_from_the_room(self):
        self.assertEqual(complete("look se", Context(words=["seller", "town"])), "look seller")

    def test_help_uses_topics(self):
        ctx = Context(topics=["making_money"], words=["magic"])
        self.assertEqual(complete("help ma", ctx), "help making_money")

    def test_slash_commands(self):
        self.assertEqual(complete("/ro", Context()), "/route ")
        self.assertEqual(complete("/route Ba", Context(rooms=["Bank"])), "/route Bank")
        self.assertEqual(complete("/forget Sw", Context(notes=["Sword shop"])), "/forget Sword shop")

    def test_nothing_to_offer(self):
        self.assertIsNone(complete("", Context()))
        self.assertIsNone(complete("zzz", Context()))
        self.assertIsNone(complete("north", Context(exits=["north"])))

    def test_recent_words_skip_your_commands(self):
        self.assertEqual(recent_words(["> secretword", "A tall seller is here"]),
                         ["here", "seller", "tall"])


if __name__ == "__main__":
    unittest.main()
