import unittest

from threading import Event
from time import perf_counter
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.middleware import (
    AdmissionGate,
    ReliabilityMiddleware
)

from app.bounded_executor import (
    BoundedExecutor,
    ExecutorOverloaded,
    result_before
)


class TestServiceProtection(unittest.TestCase):

    def test_rate_limit(self):

        gate = AdmissionGate(
            max_concurrent=5,
            limits={"chat": 1}
        )

        with patch(
            "app.middleware.monotonic",
            return_value=10.0
        ):

            self.assertEqual(
                gate.enter("chat")[0],
                200
            )

            gate.leave()

            self.assertEqual(
                gate.enter("chat")[0],
                429
            )

    def test_concurrency_limit(self):

        gate = AdmissionGate(
            max_concurrent=1,
            limits={"chat": 100}
        )

        self.assertEqual(
            gate.enter("chat")[0],
            200
        )

        self.assertEqual(
            gate.enter("chat")[0],
            503
        )

        gate.leave()

        self.assertEqual(
            gate.enter("chat")[0],
            200
        )

        gate.leave()

    def test_request_id(self):

        app = FastAPI()

        @app.get("/api/v1/health")
        def health():

            return {"status": "ok"}

        app.add_middleware(
            ReliabilityMiddleware
        )

        with TestClient(app) as client:

            response = client.get(
                "/api/v1/health"
            )

        self.assertEqual(
            response.status_code,
            200
        )

        self.assertIn(
            "x-request-id",
            response.headers
        )

    def test_executor_overload(self):

        executor = BoundedExecutor(
            max_workers=1,
            max_pending=0
        )

        event = Event()

        try:

            future = executor.submit(
                event.wait
            )

            with self.assertRaises(
                ExecutorOverloaded
            ):

                executor.submit(lambda: 1)

            event.set()

            self.assertTrue(
                future.result(timeout=2)
            )

        finally:

            event.set()

            executor.shutdown()

    def test_executor_timeout(self):

        executor = BoundedExecutor(
            max_workers=1,
            max_pending=0
        )

        event = Event()

        try:

            future = executor.submit(
                event.wait
            )

            with self.assertRaises(
                TimeoutError
            ):

                result_before(
                    future,
                    perf_counter()
                )

        finally:

            event.set()

            executor.shutdown()


if __name__ == "__main__":

    unittest.main()