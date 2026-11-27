import json
import os
import random
import math

from pathlib import Path
from time import perf_counter
from uuid import uuid4
from collections import Counter, defaultdict

from locust import HttpUser, task, between, events
from locust.runners import LocalRunner


QUERIES = [

    "Redis AOF",

    "Redis如何实现数据持久化",

    "Celery如何处理异步任务",

    "Qdrant向量检索原理",

    "QueryForge缓存失效机制"
]


statistics = defaultdict(
    lambda: {
        "status_codes": Counter(),
        "successful_latencies": [],
        "total": 0,
        "cache_hits": 0,
        "valid_responses": 0,
        "degraded": 0,
        "rate_limit_without_retry_after": 0
    }
)

started = 0.0


class QueryForgeUser(HttpUser):

    wait_time = between(0.5, 1.0)

    def execute_search(self, query, name):

        with self.client.post(

            "/api/v1/search",

            json={

                "query": query,

                "mode": "hybrid",

                "top_k": 5,

                "rerank": False
            },

            name=name,

            timeout=50,

            catch_response=True

        ) as response:

            if response.status_code != 200:

                response.failure(
                    f"HTTP {response.status_code}"
                )

                return

            try:

                data = response.json()

                required = {

                    "hits",

                    "cache_hit",

                    "degraded",

                    "index_revision"
                }

                if not required.issubset(data):

                    response.failure(
                        "Invalid response schema"
                    )

                    return

                record = statistics[name]

                record["valid_responses"] += 1

                if data["cache_hit"]:
                    record["cache_hits"] += 1

                if data["degraded"]:

                    record["degraded"] += 1

                    response.failure(
                        "Degraded retrieval"
                    )

                    return

                response.success()

            except (ValueError, TypeError):

                response.failure(
                    "Invalid JSON response"
                )

    @task(4)
    def hot_search(self):

        self.execute_search(

            random.choice(QUERIES),

            "search_hot"
        )

    @task(1)
    def cold_search(self):

        query = (

            random.choice(QUERIES)

            + " [loadtest:"

            + uuid4().hex

            + "]"
        )

        self.execute_search(
            query,
            "search_cold"
        )


@events.test_start.add_listener
def on_test_start(environment, **kwargs):

    global started

    statistics.clear()

    started = perf_counter()


@events.request.add_listener
def on_request(

    request_type,

    name,

    response_time,

    response,

    exception,

    **kwargs
):

    record = statistics[name]

    record["total"] += 1

    status = (
        response.status_code
        if response is not None
        else 0
    )

    record["status_codes"][str(status)] += 1
    if status == 429 and not response.headers.get("Retry-After", "").isdigit():
        record["rate_limit_without_retry_after"] += 1

    if exception is None and status == 200:

        record["successful_latencies"].append(
            response_time
        )


def percentile(values, p):

    if not values:
        return None

    ordered = sorted(values)

    index = max(
        0,
        math.ceil(len(ordered) * p / 100) - 1
    )

    return round(ordered[index], 3)


@events.test_stop.add_listener
def on_test_stop(environment, **kwargs):

    if not isinstance(environment.runner, LocalRunner):
        # The counters in this module belong to one load generator.
        return

    elapsed = max(
        perf_counter() - started,
        0.001
    )

    report = {

        "elapsed_seconds": round(elapsed, 3),
        "configuration": json.loads(os.environ.get("LOAD_CONFIGURATION", "{}")),
        "scenario": {
            "mode": "hybrid", "top_k": 5, "rerank": False,
            "hot_task_weight": 4, "cold_task_weight": 1,
            "wait_seconds": [0.5, 1.0], "llm": False
        },

        "workloads": {}
    }

    for name, record in statistics.items():

        values = record["successful_latencies"]

        total = record["total"]

        success = len(values)

        report["workloads"][name] = {

            "requests": total,

            "successful_requests": success,

            "success_rate": round(
                success / max(total, 1), 4
            ),

            "successful_qps": round(
                success / elapsed, 3
            ),

            "p50_success_ms": percentile(values, 50),

            "p95_success_ms": percentile(values, 95),

            "p99_success_ms": percentile(values, 99),

            "cache_hit_rate": round(

                record["cache_hits"]

                / max(record["valid_responses"], 1),

                4
            ),

            "degraded": record["degraded"],
            "rate_limit_without_retry_after": record["rate_limit_without_retry_after"],

            "status_codes": dict(
                record["status_codes"]
            )
        }

    output = Path(

        os.environ.get(

            "LOAD_REPORT",

            "docs/loadtest/report.json"
        )
    )

    output.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    with output.open("x", encoding="utf-8") as file:
        json.dump(report, file, indent=2)
        file.write("\n")

    print("\nLoad test report:")

    print(
        json.dumps(report, indent=2)
    )
