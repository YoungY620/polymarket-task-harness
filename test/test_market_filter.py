import unittest

from pm_task_harness.market_filter import load_markets, select_tasks


def row(
    slug,
    question,
    price=0.22,
    category="politics",
    liquidity=100000,
    end_date="2026-12-31T00:00:00Z",
    tags=None,
):
    tag_slugs = tags if tags is not None else [category]
    return {
        "id": slug,
        "event_slug": slug,
        "slug": slug,
        "question": question,
        "description": question,
        "url": f"https://polymarket.com/event/{slug}",
        "outcomes": ["Yes", "No"],
        "outcome_prices": [price, 1 - price],
        "clob_token_ids": [f"{slug}-yes", f"{slug}-no"],
        "liquidity": liquidity,
        "volume_24hr": 50000,
        "end_date": end_date,
        "category_slug": category,
        "tags": [{"slug": tag} for tag in tag_slugs],
    }


class MarketFilterTest(unittest.TestCase):
    def test_selects_few_political_research_tasks_and_dedupes(self):
        markets = load_markets(
            {
                "markets": [
                    row("iran-nuclear", "Will the US and Iran reach a nuclear agreement before December 31?"),
                    row("iran-nuclear-copy", "Will the US and Iran reach a nuclear agreement before December 31?"),
                    row("host-words", "Will this podcast host say the word recession more than 5 times?"),
                    row("btc-up", "Bitcoin Up or Down on June 7?", category="crypto"),
                    row("senate-bill", "Will the US Senate pass a sanctions bill on Russia before October 1?"),
                ]
            }
        )
        tasks = select_tasks(markets, max_tasks=3)
        slugs = [task.market.market_slug for task in tasks]
        self.assertIn("iran-nuclear", slugs)
        self.assertIn("senate-bill", slugs)
        self.assertNotIn("host-words", slugs)
        self.assertNotIn("btc-up", slugs)
        self.assertEqual(len([s for s in slugs if "iran-nuclear" in s]), 1)

    def test_rejects_low_liquidity_and_nearly_resolved(self):
        markets = load_markets(
            [
                row("thin", "Will Trump announce a tariff deal before September?", liquidity=100),
                row("resolved", "Will Iran sign a nuclear agreement before September?", price=0.98),
            ]
        )
        self.assertEqual(select_tasks(markets), [])

    def test_uses_polymarket_tags_not_topic_keywords_for_preferred_types(self):
        markets = load_markets(
            [
                row(
                    "tag-only",
                    "Will the agreement be signed before December 31?",
                    category="",
                    tags=["geopolitics"],
                ),
                row("sports", "Will Team A win the match before December 31?", category="", tags=["sports"]),
            ]
        )
        tasks = select_tasks(markets, max_tasks=3)
        slugs = [task.market.market_slug for task in tasks]
        self.assertIn("tag-only", slugs)
        self.assertNotIn("sports", slugs)
        self.assertEqual(tasks[0].market_type, "geopolitics")

    def test_weighted_sampling_probability_uses_type_weight_and_count(self):
        markets = load_markets(
            [
                row("geo-1", "Will agreement A be signed before December 31?", tags=["geopolitics"]),
                row("election-1", "Will candidate A win before December 31?", tags=["elections"]),
                row("election-2", "Will candidate B win before December 31?", tags=["elections"]),
                row("election-3", "Will candidate C win before December 31?", tags=["elections"]),
            ]
        )
        counts = {"geopolitics": 0, "elections": 0}
        for seed in range(200):
            task = select_tasks(
                markets,
                max_tasks=1,
                tag_weights={"geopolitics": 1.0, "elections": 1.0},
                seed=seed,
            )[0]
            counts[task.market_type] += 1

        self.assertGreater(counts["elections"], counts["geopolitics"])


if __name__ == "__main__":
    unittest.main()
