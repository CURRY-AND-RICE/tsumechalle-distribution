import importlib.util
import unittest
from datetime import datetime, timezone
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts/update_data.py"
SPEC = importlib.util.spec_from_file_location("update_data", MODULE_PATH)
assert SPEC and SPEC.loader
update_data = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(update_data)


class AggregateRatingsTest(unittest.TestCase):
    def test_same_rating_has_same_rank(self):
        rows = update_data.aggregate_ratings([2000, 1900, 1900, 1800])
        self.assertEqual(
            rows,
            [
                {"rating": 2000, "count": 1, "rank": 1, "usersAtOrAbove": 1},
                {"rating": 1900, "count": 2, "rank": 2, "usersAtOrAbove": 3},
                {"rating": 1800, "count": 1, "rank": 4, "usersAtOrAbove": 4},
            ],
        )

    def test_validation_rejects_incomplete_snapshot(self):
        errors = update_data.validate_snapshot([1000, 900], 3, None, 0.25)
        self.assertTrue(any("一致しません" in error for error in errors))

    def test_validation_rejects_large_change(self):
        errors = update_data.validate_snapshot([1000] * 50, 50, 100, 0.25)
        self.assertTrue(any("前回比" in error for error in errors))

    def test_next_update_is_monday_0315_jst(self):
        now = datetime(2026, 7, 24, 12, 0, tzinfo=timezone.utc)
        actual = update_data.next_scheduled_update(now)
        self.assertEqual(actual, datetime(2026, 7, 26, 18, 15, tzinfo=timezone.utc))

    def test_rating_range(self):
        self.assertEqual(update_data.parse_rating("1234"), 1234)
        self.assertEqual(update_data.parse_rating("-8"), -8)
        with self.assertRaises(ValueError):
            update_data.parse_rating("-10001")
        with self.assertRaises(ValueError):
            update_data.parse_rating("10001")


if __name__ == "__main__":
    unittest.main()
