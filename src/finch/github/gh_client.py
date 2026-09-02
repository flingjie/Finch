"""gh CLI 只读封装（spec 5.2）。"""

import subprocess


def _run(argv: list[str], timeout: float) -> dict:
    """子进程数组传参 + 超时，返回结构化结果。"""
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return {
            "ok": proc.returncode == 0,
            "exit_code": proc.returncode,
            "stdout": proc.stdout,
            "stderr": proc.stderr,
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "exit_code": None, "stdout": "", "stderr": "timeout"}
    except FileNotFoundError:
        return {"ok": False, "exit_code": None, "stdout": "", "stderr": "not found"}


class GhClient:
    def version(self) -> str:
        r = _run(["gh", "--version"], timeout=10.0)
        return r["stdout"].splitlines()[0] if r["ok"] else ""

    def auth_status(self) -> dict:
        r = _run(["gh", "auth", "status"], timeout=10.0)
        detail = (r["stderr"] or r["stdout"]).strip()
        return {"ok": r["ok"], "exit_code": r["exit_code"], "detail": detail}
