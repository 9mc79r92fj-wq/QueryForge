import unittest

from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.middleware import (
    ReliabilityMiddleware,
    settings
)


class TestHTTPProtection(unittest.TestCase):

    def test_http_rate_limit(self):

        application = FastAPI()

        @application.post(
            settings.api_prefix + "/search"
        )
        def search():

            return {"status": "ok"}

        application.add_middleware(
            ReliabilityMiddleware
        )

        with patch.object(
            settings,
            "api_requests_per_minute",
            2
        ), patch(
            "app.middleware.monotonic",
            return_value=10.0
        ):

            with TestClient(application) as client:

                path = (
                    settings.api_prefix
                    + "/search"
                )

                first = client.post(path)

                second = client.post(path)

                third = client.post(path)

                self.assertEqual(
                    first.status_code,
                    200
                )

                self.assertEqual(
                    second.status_code,
                    200
                )

                self.assertEqual(
                    third.status_code,
                    429
                )

                self.assertIn(
                    "Retry-After",
                    third.headers
                )

    def test_middleware_registered(self):

        from app.main import app

        self.assertTrue(

            any(

                middleware.cls
                is ReliabilityMiddleware

                for middleware
                in app.user_middleware

            )
        )


if __name__ == "__main__":

    unittest.main()