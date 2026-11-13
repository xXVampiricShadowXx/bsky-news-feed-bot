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
        self.assertAccepted("NATO ministers meet over a dispute between France and Russia")
        self.assertAccepted("Who will be the UN's next secretary-general?")
        self.assertAccepted("U.S. and China agree trade truce")
        self.assertAccepted("International sanctions are imposed on a regional regime")
        self.assertAccepted("Gen Z could shape Kenya's next election")

    def test_accepts_current_international_security_and_crises(self):
        self.assertAccepted("Cross-border drone strike kills civilians near the border")
        self.assertAccepted("Earthquake triggers major international rescue response")
        self.assertAccepted("Migrant boat disaster leaves dozens missing off Greece")
        self.assertAccepted("Government opens national security investigation into state espionage")
        self.assertAccepted("Government opens national security investigation into TechCo's foreign ties")
        self.assertAccepted(
            "A century-old treaty is now at the center of a dispute between France and Germany"
        )

    def test_rejects_domestic_and_soft_news(self):
        self.assertRejected("Primary school 'devastated' by suspected theft of kid goats")
        self.assertRejected("Women charged after separate home invasions")
        self.assertRejected("Which states get a public holiday on Monday?")
        self.assertRejected("SpaceX launches crew to ISS")
        self.assertRejected("Commonwealth Bank boss says Reserve Bank rate hikes probably finished")
        self.assertRejected("Swiss glaciers suffer another year of record ice loss")
        self.assertRejected("UK minister unveils housing plan")
        self.assertRejected("Protesters march over local rent increases in France")
        self.assertRejected("Australia's next election is dominated by local tax policy")
        self.assertRejected("Albanese's campaign is focused on Australia's next election")
        self.assertRejected("CEO faces questions over alleged China espionage as shares fall")
        self.assertRejected(
            "TechCo shares fall after government opens a security investigation into espionage"
        )
        self.assertRejected(
            "TechCo shares fall after government opens a national security investigation into its foreign ties"
        )
        self.assertRejected("UK minister sued over local housing plan")
        self.assertRejected("A century-old letter reveals a family's wartime history")
        self.assertRejected("A century-old treaty shaped the history of Europe's borders")
        self.assertRejected("France and Germany recall the treaty signed during World War II")

    def test_summit_personal_appearance_is_not_geopolitical(self):
        self.assertRejected("Activist delivers remarks on arrival at UN summit")
        self.assertRejected("Singer attends G7 summit to promote a new album")
        self.assertAccepted("Activist's remarks prompt G7 leaders to sign a new treaty")

    def test_source_metadata_does_not_change_classification(self):
        headline = "UK minister unveils housing plan"
        plain = topics.assess(headline)
        tagged = topics.assess(
            headline,
            tags=["international relations", "foreign affairs"],
            url="https://example.com/world/politics",
        )
        self.assertEqual(plain[0], tagged[0])
        self.assertFalse(tagged[0])

        headline = "U.S. and China agree trade truce"
        plain = topics.assess(headline)
        tagged = topics.assess(
            headline,
            tags=["celebrity", "lifestyle"],
            url="https://example.com/entertainment/story",
        )
        self.assertEqual(plain[0], tagged[0])
        self.assertTrue(tagged[0])

    def test_off_topic_markers_veto_weak_matches(self):
        self.assertRejected("Kanye West's Russia concerts cancelled", tags=["Music"])
        self.assertRejected("Man City charged in Premier League scandal", url="https://x.com/sport/1")
        self.assertAccepted(
            "Olympic boycott: NATO allies impose sanctions on Russia over invasion of Ukraine",
        )

    def test_case_sensitive_acronyms(self):
        self.assertRejected("Let us know who won the cup of tea contest")


    def test_reported_ap_false_negatives_are_accepted(self):
        self.assertAccepted(
            "Pakistani security forces killed 74 militants in multiple raids on hideouts bordering Afghanistan"
        )
        self.assertAccepted(
            "COP31 President Murat Kurum opened the U.N. climate summit in Turkey"
        )
        self.assertAccepted(
            "Indonesia's vice president resigns after a dispute over his academic credentials"
        )
        self.assertAccepted("Congo's Ebola cases have crossed 11,000")
        self.assertAccepted(
            "U.S. authorities charged three men with plotting nearly 20 attacks targeting "
            "Jewish communities in Europe and the U.S., and a British military base"
        )
        self.assertAccepted("China's trade surplus exceeds $1 trillion for a second straight year")
        self.assertAccepted(
            "Greek authorities recovered 12 bodies from a migrant boat found sailing south of Crete"
        )

    def test_front_line_is_a_phrase_not_two_independent_triggers(self):
        self.assertRejected(
            "People line up each night outside a charity-run food pantry in Havana"
        )
        self.assertAccepted(
            "Civilians trapped along the front line as fighting intensifies in Ukraine"
        )

    def test_foreign_leadership_changes_are_geopolitical(self):
        self.assertAccepted("Indonesia's vice president steps down after coalition dispute")
        self.assertAccepted("Pakistan's prime minister appoints a new cabinet after coalition talks")

    def test_large_scale_international_health_crisis_is_geopolitical(self):
        self.assertAccepted("Ebola outbreak spreads across Congo as cases exceed 11,000")
        self.assertRejected("Local clinic reports 12 seasonal flu cases")

    def test_cross_border_trade_story_is_geopolitical(self):
        self.assertAccepted("China's trade surplus exceeds $1 trillion")
        self.assertRejected("Local retailer reports record holiday sales")

    def test_domestic_us_leadership_story_is_not_geopolitical(self):
        self.assertRejected("US president resigns amid a domestic political scandal")
