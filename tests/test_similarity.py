import unittest

from feeds import canonical_story_key
from similarity import headline_tokens, same_story


def same(a, b):
    return same_story(headline_tokens(a), headline_tokens(b))


class SimilarityTests(unittest.TestCase):
    def test_reworded_coverage_of_one_event_matches(self):
        self.assertTrue(same(
            "Hundreds detained as French school protests escalate",
            "Hundreds held, dozens wounded as French school protests escalate",
        ))
        self.assertTrue(same(
            "UK-France 'one in, one out' migrant exchange deal is scrapped",
            "UK-France 'one in, one out' migrant scheme scrapped",
        ))

    def test_new_developments_are_not_suppressed(self):
        self.assertFalse(same(
            "French far-right leader Bardella denies anti-Semitism allegations",
            "French far-right leader Bardella says filed lawsuit over anti-Semitic remarks claim",
        ))
        self.assertFalse(same("Russia attacks Ukraine", "Russia attacks Ukraine grid"))

    def test_rte_story_key_ignores_slug_changes(self):
        self.assertEqual(
            canonical_story_key("https://www.rte.ie/news/uk/2026/0930/1593556-fairford-terror-plot/"),
            canonical_story_key(
                "https://www.rte.ie/news/uk/2026/0930/1593556-strong-indications-iran-involved/"
            ),
        )


if __name__ == "__main__":
    unittest.main()
