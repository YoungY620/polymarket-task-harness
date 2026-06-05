import unittest

from pm_task_harness.positions import position_market_slug


class PositionsTest(unittest.TestCase):
    def test_position_market_slug_accepts_common_shapes(self):
        self.assertEqual(position_market_slug({"marketSlug": "a"}), "a")
        self.assertEqual(position_market_slug({"market": {"slug": "b"}}), "b")
        self.assertEqual(position_market_slug({}), "")


if __name__ == "__main__":
    unittest.main()
