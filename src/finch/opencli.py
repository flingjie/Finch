"""opencli 只读客户端共享助手。

opencli 的 ``cookie`` 策略命令共享同一登录浏览器会话；并发驱动同一会话会导致
``Navigation rejected`` 或 ``Detached while handling command``。这里提供一个进程级锁，
把同一个 Finch 进程内的 opencli 命令串行化，并对瞬态导航错误做有界指数退避重试。
"""

import threading
import time
from collections.abc import Callable

TRANSIENT_STDERR_MARKERS = (
    "navigation rejected",
    "detached while handling",
)

_BROWSER_LOCK = threading.Lock()


def is_transient_opencli_error(stderr: str) -> bool:
    """判定 opencli 错误是否为浏览器会话瞬态失败（可安全重试）。"""
    lowered = stderr.lower()
    return any(marker in lowered for marker in TRANSIENT_STDERR_MARKERS)


def run_opencli(
    argv: list[str],
    *,
    run_fn: Callable[[list[str], float], dict],
    timeout: float = 60.0,
    max_retries: int = 2,
    backoff_seconds: float = 1.5,
) -> dict:
    """串行执行 opencli 命令，并对瞬态浏览器错误做有界退避重试。

    ``run_fn`` 通过参数注入，便于客户端沿用各自模块内可被测试替换的 ``_run``。
    锁只覆盖单次子进程调用，退避等待期间释放锁，避免阻塞其他排队命令。
    """
    attempt = 0
    while True:
        with _BROWSER_LOCK:
            result = run_fn(argv, timeout)
        if result["ok"] or not is_transient_opencli_error(result["stderr"] or ""):
            return result
        if attempt >= max_retries:
            return result
        time.sleep(backoff_seconds * (2 ** attempt))
        attempt += 1
