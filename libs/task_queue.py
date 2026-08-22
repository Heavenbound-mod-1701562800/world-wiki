"""出站任务队列：把函数丢进线程池跑，带最小启动间隔。"""

from __future__ import annotations

import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Callable, Iterable, Optional, TypeVar

import config

R = TypeVar("R")


class TaskQueue:
    """持久线程池：submit 投递任务，间隔在 worker 真正开跑前等待。"""

    def __init__(
        self,
        *,
        interval_sec: float,
        max_workers: int,
        name: str = "task",
    ) -> None:
        self.interval_sec = max(0.0, float(interval_sec))
        self.max_workers = max(1, int(max_workers))
        self._gate = threading.Lock()
        self._last_ts = 0.0
        self._pool = ThreadPoolExecutor(
            max_workers=self.max_workers,
            thread_name_prefix=name,
        )

    def _wait_turn(self) -> None:
        with self._gate:
            now = time.monotonic()
            wait = self.interval_sec - (now - self._last_ts)
            if wait > 0:
                time.sleep(wait)
            self._last_ts = time.monotonic()

    def submit(self, fn: Callable[..., R], /, *args, **kwargs) -> Future[R]:
        """把 fn(*args, **kwargs) 丢进线程池；返回 Future，可 .result() join。"""

        def _task() -> R:
            self._wait_turn()
            return fn(*args, **kwargs)

        return self._pool.submit(_task)

    def run(self, fn: Callable[..., R], /, *args, **kwargs) -> R:
        """submit 一个任务并立刻 join。"""
        return self.submit(fn, *args, **kwargs).result()

    @staticmethod
    def gather(futures: Iterable[Future[R]]) -> list[R]:
        """按给定顺序等待全部 Future 完成。"""
        return [future.result() for future in futures]

    def shutdown(self, wait: bool = True) -> None:
        """关闭线程池。"""
        self._pool.shutdown(wait=wait)


class _LazyQueue:
    """进程内单例 TaskQueue，第一次 get 时才创建。"""

    def __init__(self, factory: Callable[[], TaskQueue]) -> None:
        self._factory = factory
        self._queue: Optional[TaskQueue] = None
        self._lock = threading.Lock()

    def get(self) -> TaskQueue:
        """返回已创建的队列，必要时先 factory()。"""
        with self._lock:
            if self._queue is None:
                self._queue = self._factory()
            return self._queue


def crawler_queue() -> TaskQueue:
    """下载任务队列。"""
    return _CRAWLER.get()


def llm_queue() -> TaskQueue:
    """LLM 任务队列。"""
    return _LLM.get()


_CRAWLER = _LazyQueue(
    lambda: TaskQueue(
        interval_sec=config.CRAWLER_REQUEST_INTERVAL,
        max_workers=config.CRAWLER_MAX_WORKERS,
        name="crawler",
    )
)
_LLM = _LazyQueue(
    lambda: TaskQueue(
        interval_sec=config.LLM_REQUEST_INTERVAL,
        max_workers=config.LLM_MAX_WORKERS,
        name="llm",
    )
)
