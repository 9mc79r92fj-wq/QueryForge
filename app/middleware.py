import json
import logging
import math

from threading import Lock
from time import monotonic, perf_counter
from uuid import uuid4

from starlette.responses import JSONResponse
from starlette.exceptions import HTTPException
from starlette.datastructures import Headers

from app.access import allow_admin
from app.config import get_settings


settings = get_settings()

logger = logging.getLogger("uvicorn.error")


class AdmissionGate:

    def __init__(
        self,
        max_concurrent: int,
        limits: dict[str, int]
    ):

        if max_concurrent <= 0:
            raise ValueError("Invalid concurrency limit")

        if any(value <= 0 for value in limits.values()):
            raise ValueError("Invalid rate limit")

        self.max_concurrent = max_concurrent

        self.limits = limits

        self.active = 0

        self.counters = {}

        self.lock = Lock()

    def enter(
        self,
        group: str
    ) -> tuple[int, int]:

        now = monotonic()

        window = int(now // 60)

        retry_after = max(
            1,
            math.ceil((window + 1) * 60 - now)
        )

        with self.lock:

            old_window, count = self.counters.get(
                group,
                (window, 0)
            )

            if old_window != window:
                count = 0

            if count >= self.limits[group]:

                return 429, retry_after

            if self.active >= self.max_concurrent:

                return 503, 1

            self.counters[group] = (
                window,
                count + 1
            )

            self.active += 1

            return 200, 0

    def leave(self) -> None:

        with self.lock:

            self.active -= 1


class ReliabilityMiddleware:

    def __init__(self, app):

        self.app = app

        self.gate = AdmissionGate(

            max_concurrent=(
                settings.max_concurrent_requests
            ),

            limits={

                "chat": settings.chat_requests_per_minute,

                "other": settings.api_requests_per_minute

            }
        )

    async def __call__(
        self,
        scope,
        receive,
        send
    ):

        if scope["type"] != "http":

            await self.app(
                scope,
                receive,
                send
            )

            return

        request_id = str(uuid4())

        started = perf_counter()

        path = scope.get("path", "")

        prefix = settings.api_prefix

        status = 500

        admitted = False

        response_started = False

        guarded = (

            path.startswith(prefix + "/")

            and path != prefix + "/health"
        )

        group = (
            "chat"
            if path.startswith(prefix + "/chat/")
            else "other"
        )
        limit = (
            settings.max_upload_mb * 1024 * 1024 + 65536
            if path == prefix + "/documents/upload" else 65536
        )
        received = 0

        async def limited_receive():
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    raise HTTPException(413, "Request body too large")
            return message

        async def tracked_send(message):

            nonlocal status, response_started

            if message["type"] == "http.response.start":

                status = message["status"]

                response_started = True

                headers = list(
                    message.get("headers", [])
                )

                headers.append(
                    (
                        b"x-request-id",
                        request_id.encode("ascii")
                    )
                )
                headers.append((b"x-content-type-options", b"nosniff"))
                if path.startswith(prefix + "/chat/"):
                    headers = [(name, value) for name, value in headers if name != b"cache-control"]
                    headers.append((b"cache-control", b"no-store"))

                message = {
                    **message,
                    "headers": headers
                }

            await send(message)

        try:

            if guarded:

                code, retry_after = (
                    self.gate.enter(group)
                )

                if code != 200:

                    error_code = (
                        "RATE_LIMIT_EXCEEDED"
                        if code == 429
                        else "SERVICE_OVERLOADED"
                    )

                    response = JSONResponse(

                        status_code=code,

                        content={

                            "code": error_code,

                            "request_id": request_id

                        },

                        headers={

                            "Retry-After": str(retry_after)

                        }
                    )

                    await response(
                        scope,
                        receive,
                        tracked_send
                    )

                    return

                admitted = True
                headers = Headers(scope=scope)
                if path == prefix + "/documents" or path.startswith(prefix + "/documents/"):
                    try:
                        allow_admin(headers.get("authorization"))
                    except HTTPException as exc:
                        await JSONResponse(
                            status_code=exc.status_code,
                            content={"detail": exc.detail, "request_id": request_id},
                            headers=exc.headers
                        )(scope, receive, tracked_send)
                        return
                length = headers.get("content-length", "")
                if length and (not length.isdigit() or len(length) > 12 or int(length) > limit):
                    await JSONResponse(
                        status_code=413,
                        content={"code": "REQUEST_TOO_LARGE", "request_id": request_id}
                    )(scope, receive, tracked_send)
                    return

            await self.app(
                scope,
                limited_receive if guarded else receive,
                tracked_send
            )

        except Exception as exc:

            logger.error(

                "Request failed: %s %s",

                request_id,

                type(exc).__name__
            )

            if response_started:
                raise

            response = JSONResponse(

                status_code=500,

                content={

                    "code": "INTERNAL_ERROR",

                    "request_id": request_id

                }
            )

            await response(
                scope,
                receive,
                tracked_send
            )

        finally:

            if admitted:

                self.gate.leave()

            route = getattr(
                scope.get("route"),
                "path",
                "unmatched"
            )

            logger.info(

                json.dumps({

                    "event": "http_request",

                    "request_id": request_id,

                    "method": scope.get("method"),

                    "route": route,

                    "status": status,

                    "latency_ms": round(

                        (perf_counter() - started) * 1000,

                        3
                    )

                })
            )
