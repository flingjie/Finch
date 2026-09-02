"""opencli 只读封装（spec 5.3）。"""

from ..github.gh_client import _run


class OpenCliClient:
    def version(self) -> str:
        r = _run(["opencli", "--version"], timeout=10.0)
        return r["stdout"].strip() if r["ok"] else ""

    def doctor(self) -> dict:
        r = _run(["opencli", "doctor"], timeout=30.0)
        detail = (r["stderr"] or r["stdout"]).strip()
        return {"ok": r["ok"], "exit_code": r["exit_code"], "detail": detail}
