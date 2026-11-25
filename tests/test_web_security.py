import unittest

from unittest.mock import patch

from app import system_routes


class TestWebCsp(unittest.TestCase):

    def _settings(self, ancestors: str):

        return type(
            "S",
            (),
            {
                "api_prefix": "/api/v1",
                "csp_frame_ancestors": ancestors,
            }
        )()

    def test_default_blocks_framing(self):

        with patch.object(
            system_routes,
            "get_settings",
            return_value=self._settings("'none'")
        ):
            response = system_routes.web_home()

        policy = response.headers["content-security-policy"]

        self.assertIn("frame-ancestors 'none'", policy)
        self.assertEqual(response.headers["referrer-policy"], "no-referrer")

    def test_platform_origin_allowed(self):

        with patch.object(
            system_routes,
            "get_settings",
            return_value=self._settings(
                "https://www.modelscope.cn https://modelscope.cn"
            )
        ):
            response = system_routes.web_home()

        policy = response.headers["content-security-policy"]

        self.assertIn("https://www.modelscope.cn", policy)
        self.assertNotIn("frame-ancestors 'none'", policy)


if __name__ == "__main__":
    unittest.main()
