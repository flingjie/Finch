"""文件工作区：确定性原子读写 + YAML / frontmatter-Markdown / JSONL 序列化。

替换旧的 SQLite 存储。单用户本地 CLI，原子写（临时文件 + ``os.replace``）是
唯一一致性机制；不设文件锁。领域模型仍用 Pydantic 校验读写。
"""

import json
import os
from pathlib import Path
from typing import TypeVar

import yaml
from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)

_FRONTMATTER = "---"


class Workspace:
    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)

    def ensure(self) -> None:
        """幂等创建根目录。"""
        self.root.mkdir(parents=True, exist_ok=True)

    def dir(self, name: str) -> Path:
        """创建并返回工作区子目录（支持 ``a/b`` 嵌套路径）。"""
        d = self.root / name
        d.mkdir(parents=True, exist_ok=True)
        return d

    @staticmethod
    def safe_filename(name: str) -> str:
        """文件名消毒：拒绝路径穿越，``:`` 映射为 ``_``（复合 id 兜底）。"""
        if ".." in name or "/" in name or "\x00" in name:
            raise ValueError(f"unsafe filename: {name!r}")
        return name.replace(":", "_")

    def atomic_write(self, path: Path, text: str) -> None:
        """写临时文件后 ``os.replace`` 原子替换（同目录保证原子性）。"""
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)

    def write_yaml(self, path: Path, model: BaseModel) -> None:
        """领域模型 → YAML（``mode="json"`` 保证 datetime/StrEnum/None 稳定）。"""
        self.atomic_write(
            path,
            yaml.safe_dump(model.model_dump(mode="json"), sort_keys=False, allow_unicode=True),
        )

    def read_yaml(self, path: Path, model_cls: type[T]) -> T | None:
        """YAML → 领域模型；缺文件返回 None；损坏抛 ValidationError / YAMLError。"""
        if not path.exists():
            return None
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        return model_cls.model_validate(data)

    def write_frontmatter(self, path: Path, meta: dict, body: str) -> None:
        """写 frontmatter Markdown：``---`` 元数据 ``---`` 正文（单文件单真相单原子写）。"""
        meta_yaml = yaml.safe_dump(meta, sort_keys=False, allow_unicode=True)
        self.atomic_write(path, f"{_FRONTMATTER}\n{meta_yaml}{_FRONTMATTER}\n{body}")

    def read_frontmatter(self, path: Path) -> tuple[dict, str]:
        """读 frontmatter Markdown → (元数据 dict, 正文 str)。

        只按前两个 ``---`` 行切分，正文中的 ``---``（如 Markdown 分隔线）不受影响；
        正文尾部换行在读时规范化（不保留）。
        """
        lines = path.read_text(encoding="utf-8").split("\n")
        close = next(i for i in range(1, len(lines)) if lines[i] == _FRONTMATTER)
        meta = yaml.safe_load("\n".join(lines[1:close])) or {}
        return meta, "\n".join(lines[close + 1 :])

    def append_jsonl(self, path: Path, obj: dict) -> None:
        """追加一行 JSONL（保序）。"""
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(obj, ensure_ascii=False, default=str) + "\n")

    def read_jsonl(self, path: Path) -> list[dict]:
        """读全部 JSONL 行；缺文件返回空列表。"""
        if not path.exists():
            return []
        out: list[dict] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                out.append(json.loads(line))
        return out
