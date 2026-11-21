import unittest

from mods_pack import BlightMod


class BlightCorruptScoreOverflowTests(unittest.TestCase):
    def test_scores_at_or_below_historical_cap_are_unchanged(self):
        for raw_score in (-1.0, 0.0, 2.75, 3.499, 3.5):
            with self.subTest(raw_score=raw_score):
                self.assertEqual(
                    BlightMod._rank_preserving_corrupt_score(raw_score),
                    raw_score,
                )

    def test_overflow_preserves_strict_order_above_cap(self):
        raw_scores = (3.5, 3.5001, 3.6, 4.0, 5.0, 10.0, 1000.0)
        ranked = [
            BlightMod._rank_preserving_corrupt_score(value)
            for value in raw_scores
        ]
        self.assertEqual(ranked, sorted(ranked))
        self.assertEqual(len(set(ranked)), len(ranked))

    def test_overflow_is_bounded_to_twelve_hundredths(self):
        for raw_score in (3.5001, 4.0, 5.0, 10.0, 1000.0):
            with self.subTest(raw_score=raw_score):
                score = BlightMod._rank_preserving_corrupt_score(raw_score)
                self.assertGreater(score, 3.5)
                self.assertLess(score, 3.62)

    def test_overflow_is_continuous_at_the_cap(self):
        at_cap = BlightMod._rank_preserving_corrupt_score(3.5)
        just_above = BlightMod._rank_preserving_corrupt_score(3.500001)
        self.assertEqual(at_cap, 3.5)
        self.assertGreater(just_above, at_cap)
        self.assertLess(just_above - at_cap, 1e-6)


if __name__ == "__main__":
    unittest.main()
