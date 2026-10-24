import unittest

from app.meta_rate_limit import (
    DEFAULT_COOLDOWN_SECONDS,
    format_rate_limit_message,
    is_meta_rate_limit_error,
    make_rate_limit_state,
    seconds_remaining,
)


class MetaRateLimitTests(unittest.TestCase):
    def setUp(self):
        self.now = 1_800_000_000
        self.error = {
            "error": {
                "message": "Application request limit reached",
                "type": "OAuthException",
                "code": 4,
                "error_subcode": 1349210,
                "is_transient": True,
                "fbtrace_id": "trace-safe-test",
            }
        }

    def test_recognizes_the_reported_meta_error(self):
        self.assertTrue(is_meta_rate_limit_error(self.error))

    def test_does_not_treat_an_unrelated_forbidden_error_as_rate_limit(self):
        payload = {"error": {"message": "Permission denied", "code": 10}}
        self.assertFalse(is_meta_rate_limit_error(payload))

    def test_respects_retry_after_seconds(self):
        state = make_rate_limit_state(
            self.error, {"Retry-After": "120"}, now_epoch=self.now
        )
        self.assertEqual(state["blocked_until"], self.now + 120)
        self.assertEqual(state["cooldown_source"], "retry-after")

    def test_uses_business_use_case_reset_estimate(self):
        headers = {
            "X-Business-Use-Case-Usage": (
                '[{"instagram":{"estimated_time_to_regain_access":25}}]'
            )
        }
        state = make_rate_limit_state(self.error, headers, now_epoch=self.now)
        self.assertEqual(state["blocked_until"], self.now + 25 * 60)
        self.assertEqual(state["cooldown_source"], "business-use-case-usage")

    def test_falls_back_to_conservative_cooldown_if_meta_provides_no_reset(self):
        state = make_rate_limit_state(self.error, {}, now_epoch=self.now)
        self.assertEqual(state["blocked_until"], self.now + DEFAULT_COOLDOWN_SECONDS)
        self.assertEqual(state["cooldown_source"], "conservative-fallback")

    def test_clamps_long_retry_header_to_24_hours(self):
        state = make_rate_limit_state(
            self.error, {"Retry-After": str(100 * 60 * 60)}, now_epoch=self.now
        )
        self.assertEqual(state["cooldown_seconds"], 24 * 60 * 60)

    def test_expired_cooldown_does_not_block(self):
        state = {"blocked_until": self.now + 100}
        self.assertEqual(seconds_remaining(state, self.now + 100), 0)
        self.assertGreater(seconds_remaining(state, self.now), 0)

    def test_message_explains_wait_and_no_publication(self):
        state = make_rate_limit_state(self.error, {}, now_epoch=self.now)
        message = format_rate_limit_message(state, now_epoch=self.now)
        self.assertIn("A Meta limitou temporariamente", message)
        self.assertIn("horário de Brasília", message)
        self.assertIn("Nenhuma nova publicação foi feita", message)


if __name__ == "__main__":
    unittest.main()
