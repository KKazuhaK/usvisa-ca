import unittest
from datetime import date
from unittest.mock import patch

import reschedule


class RescheduleHelpersTest(unittest.TestCase):
    def test_acceptable_dates_scans_past_excluded_earliest_date(self):
        dates = [date(2026, 10, 2), date(2026, 10, 9), date(2027, 1, 1)]
        with (
            patch.object(reschedule, "EARLIEST_ACCEPTABLE_DATE", "2026-10-01"),
            patch.object(reschedule, "LATEST_ACCEPTABLE_DATE", "2026-12-31"),
            patch.object(
                reschedule,
                "EXCLUSION_DATE_RANGES",
                [("2026-10-01", "2026-10-07")],
            ),
        ):
            self.assertEqual(reschedule.acceptable_dates(dates), [date(2026, 10, 9)])


if __name__ == "__main__":
    unittest.main()
