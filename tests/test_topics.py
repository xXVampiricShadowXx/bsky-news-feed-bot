import unittest

import topics


class GeopoliticsFilterTests(unittest.TestCase):
    def assertAccepted(self, headline, summary="", tags=()):
        ok, score, reason = topics.assess(headline, summary, tags)
        self.assertTrue(ok, f"{headline!r} rejected ({score}: {reason})")

    def assertRejected(self, headline, summary="", tags=(), url=""):
        ok, score, reason = topics.assess(headline, summary, tags, url)
        self.assertFalse(ok, f"{headline!r} accepted ({score}: {reason})")

    def test_accepts_geopolitical_stories(self):
        self.assertAccepted("Putin warns Russia will use 'all weapons' if Kaliningrad threatened")
        self.assertAccepted("Russia unleashes heavy attack on Ukrainian energy grid")
        self.assertAccepted("At least seven killed in separate Israeli attacks in Gaza")
        self.assertAccepted("Ethiopia and Eritrea break diplomatic ties over conflict")
        self.assertAccepted("G20 trade ministers deadlocked over industrial overcapacity")
        self.assertAccepted("Who will be the UN's next secretary-general?")
        self.assertAccepted("U.S. and China agree trade truce")
        self.assertAccepted("Gen Z could shape Kenya's next election")

    def test_rejects_domestic_and_soft_news(self):
        self.assertRejected("Primary school 'devastated' by suspected theft of kid goats")
        self.assertRejected("Women charged after separate home invasions")
        self.assertRejected("Which states get a public holiday on Monday?")
        self.assertRejected("SpaceX launches crew to ISS")
        self.assertRejected("Commonwealth Bank boss says Reserve Bank rate hikes probably finished")
        self.assertRejected("Swiss glaciers suffer another year of record ice loss")

    def test_off_topic_markers_veto_weak_matches(self):
        self.assertRejected("Kanye West's Russia concerts cancelled", tags=["Music"])
        self.assertRejected("Man City charged in Premier League scandal", url="https://x.com/sport/1")
        self.assertAccepted(
            "Olympic boycott: NATO allies impose sanctions on Russia over invasion of Ukraine",
        )

    def test_case_sensitive_acronyms(self):
        self.assertRejected("Let us know who won the cup of tea contest")
