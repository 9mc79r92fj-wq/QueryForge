from concurrent.futures import (
    ThreadPoolExecutor,
    Future
)

from threading import BoundedSemaphore

from time import perf_counter


class ExecutorOverloaded(RuntimeError):
    pass


class BoundedExecutor:

    def __init__(
        self,
        max_workers: int,
        max_pending: int
    ):

        if max_workers <= 0 or max_pending < 0:

            raise ValueError(
                "Invalid executor limits"
            )

        self.executor = ThreadPoolExecutor(

            max_workers=max_workers,

            thread_name_prefix="retrieval"
        )

        self.slots = BoundedSemaphore(
            max_workers + max_pending
        )

    def submit(
        self,
        function,
        *args,
        **kwargs
    ) -> Future:

        acquired = self.slots.acquire(
            blocking=False
        )

        if not acquired:

            raise ExecutorOverloaded(
                "Retrieval executor is overloaded"
            )

        try:

            future = self.executor.submit(

                function,

                *args,

                **kwargs
            )

        except BaseException:

            self.slots.release()

            raise

        future.add_done_callback(
            lambda _: self.slots.release()
        )

        return future

    def shutdown(self) -> None:

        self.executor.shutdown(
            wait=True,
            cancel_futures=True
        )


def result_before(
    future: Future,
    deadline: float
):

    remaining = max(
        0.0,
        deadline - perf_counter()
    )

    try:

        return future.result(
            timeout=remaining
        )

    except TimeoutError:

        future.cancel()

        raise