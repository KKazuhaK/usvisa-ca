import unittest
from datetime import date
from unittest.mock import MagicMock, call, patch

import reschedule


class RescheduleHelpersTest(unittest.TestCase):
    def test_date_controls_wait_30_seconds_after_selecting_consulate(self):
        driver = MagicMock()
        facility = MagicMock()
        with (
            patch.object(reschedule, "TIMEOUT", 10),
            patch.object(reschedule, "CONSULATE_ID", 95),
            patch("reschedule.WebDriverWait") as wait_class,
            patch("reschedule.Select") as select_class,
            patch("reschedule.sleep"),
            patch("reschedule.log_message"),
        ):
            wait_class.return_value.until.side_effect = ["facility", facility, True]
            reschedule._prepare_appointment_page(driver)

        self.assertEqual(
            wait_class.call_args_list,
            [call(driver, 10), call(driver, 10), call(driver, 30)],
        )
        select_class.assert_called_once_with(facility)
        select_class.return_value.select_by_value.assert_called_once_with("95")
        # The post-selection wait must require a ready date control, rather than
        # treating the still-visible facility selector as success and selecting again.
        ready_condition = wait_class.return_value.until.call_args.args[0]
        with patch("reschedule._appointment_page_state", return_value="facility"):
            self.assertFalse(ready_condition(driver))
        with patch("reschedule._appointment_page_state", return_value="ready"):
            self.assertTrue(ready_condition(driver))

    def test_already_ready_date_controls_do_not_reselect_consulate(self):
        driver = MagicMock()
        with (
            patch("reschedule.WebDriverWait") as wait_class,
            patch("reschedule.Select") as select_class,
        ):
            wait_class.return_value.until.return_value = "ready"
            reschedule._prepare_appointment_page(driver)

        wait_class.assert_called_once_with(driver, 10)
        select_class.assert_not_called()

    def test_missing_date_control_times_out_without_reselecting_consulate(self):
        driver = MagicMock()
        facility = MagicMock()
        with (
            patch("reschedule.WebDriverWait") as wait_class,
            patch("reschedule.Select") as select_class,
            patch("reschedule.sleep"),
            patch("reschedule.log_message"),
        ):
            wait_class.return_value.until.side_effect = [
                "facility", facility, reschedule.TimeoutException("date control not ready")
            ]
            with self.assertRaises(reschedule.TimeoutException):
                reschedule._prepare_appointment_page(driver)

        self.assertEqual(wait_class.call_args_list[-1], call(driver, 30))
        select_class.return_value.select_by_value.assert_called_once()

    def test_final_date_control_wait_uses_30_seconds(self):
        driver = MagicMock()
        with (
            patch("reschedule.WebDriverWait") as wait_class,
            patch("reschedule._click_action_if_present", return_value=True),
        ):
            wait_class.return_value.until.side_effect = [
                "schedule", "schedule", "schedule", True
            ]
            reschedule._prepare_appointment_page(driver)

        self.assertEqual(
            wait_class.call_args_list,
            [call(driver, 10)] * 3 + [call(driver, 30)],
        )

    def test_query_interval_is_unchanged(self):
        self.assertEqual(reschedule.DATE_REQUEST_DELAY, 180)

    def test_login_keeps_10_second_timeout(self):
        driver = MagicMock()
        with (
            patch.object(reschedule, "TIMEOUT", 10),
            patch.object(reschedule, "USER_EMAIL", "user@example.com"),
            patch.object(reschedule, "USER_PASSWORD", "test-password"),
            patch("reschedule.WebDriverWait") as wait_class,
        ):
            reschedule.login(driver)

        self.assertEqual(wait_class.call_args_list, [call(driver, 10)] * 4)

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

    def test_smtp_starttls_notification(self):
        with (
            patch.object(reschedule, "SMTP_HOST", "smtp.example.com"),
            patch.object(reschedule, "SMTP_PORT", 587),
            patch.object(reschedule, "SMTP_SECURITY", "starttls"),
            patch.object(reschedule, "SMTP_USERNAME", "sender@example.com"),
            patch.object(reschedule, "SMTP_PASSWORD", "app-password"),
            patch.object(reschedule, "SMTP_FROM", "sender@example.com"),
            patch.object(reschedule, "SMTP_TO", "receiver@example.com"),
            patch("reschedule.smtplib.SMTP") as smtp_class,
        ):
            self.assertTrue(reschedule.send_smtp_notification("Subject", "Body"))

        smtp = smtp_class.return_value
        smtp.starttls.assert_called_once()
        smtp.login.assert_called_once_with("sender@example.com", "app-password")
        smtp.send_message.assert_called_once()

    def test_telegram_notification(self):
        with (
            patch.object(reschedule, "TELEGRAM_BOT_TOKEN", "bot-token"),
            patch.object(reschedule, "TELEGRAM_CHAT_ID", "chat-id"),
            patch("reschedule.requests.post") as post,
        ):
            self.assertTrue(reschedule.send_telegram_notification("Subject", "Body"))

        post.assert_called_once_with(
            "https://api.telegram.org/botbot-token/sendMessage",
            data={"chat_id": "chat-id", "text": "Subject\n\nBody"},
            timeout=20,
        )
        post.return_value.raise_for_status.assert_called_once()


if __name__ == "__main__":
    unittest.main()
