import unittest

from controller.main import load_settings


class SettingsTests(unittest.TestCase):
    def test_defaults(self):
        self.assertEqual(load_settings({}), {
            "FAILURE_THRESHOLD": 3,
            "POLL_INTERVAL_SECONDS": 5,
            "RECOVERY_TIMEOUT_SECONDS": 120,
            "RECOVERY_SUCCESS_THRESHOLD": 5,
            "RECOVERY_CHECK_INTERVAL_SECONDS": 2,
        })

    def test_custom_values(self):
        environment = {
            "FAILURE_THRESHOLD": "6",
            "POLL_INTERVAL_SECONDS": "10",
            "RECOVERY_TIMEOUT_SECONDS": "240",
            "RECOVERY_SUCCESS_THRESHOLD": "8",
            "RECOVERY_CHECK_INTERVAL_SECONDS": "4",
        }

        settings = load_settings(environment)

        for name, value in environment.items():
            with self.subTest(name=name):
                self.assertEqual(settings[name], int(value))

    def test_invalid_values_are_rejected(self):
        for name in load_settings({}):
            for value in ("0", "-1", "abc", "1.5", ""):
                with self.subTest(name=name, value=value):
                    with self.assertRaisesRegex(ValueError, name):
                        load_settings({name: value})


if __name__ == "__main__":
    unittest.main()