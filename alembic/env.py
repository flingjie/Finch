"""Alembic 迁移环境。

绑定 ``SQLModel.metadata`` 并导入全部 Record 模型（database + repositories），
这样 autogenerate 能看到完整 schema。迁移是 Store.init 里 ``create_all`` 的正式补充路径，
二者并存：``create_all`` 仍为运行时兜底，``alembic upgrade head`` 用于正式 schema 演进。

数据库 URL 解析优先级：
1. 命令行 ``-x db_url=...``（``context.get_x_argument``）
2. 编程式 ``Config.attributes["db_url"]``（测试/内嵌调用）
3. 环境变量 ``FINCH_DB_URL``
4. ``alembic.ini`` 里的 ``sqlalchemy.url``（默认 var/finch.db）
"""

import os

from alembic import context
from sqlalchemy import engine_from_config, pool
from sqlmodel import SQLModel

# 注册全部 Record 模型，供 autogenerate 与 SQLModel.metadata 看到它们。
import finch.storage.database  # noqa: F401  (RunRecord, NodeRecord)
import finch.storage.repositories  # noqa: F401  (EvidenceCard/Draft/Review/... Records)

config = context.config

x_args = context.get_x_argument(as_dictionary=True)
url = (
    x_args.get("db_url")
    or config.attributes.get("db_url")
    or os.environ.get("FINCH_DB_URL")
)
if url is None:
    url = config.get_main_option("sqlalchemy.url")
if url:
    config.set_main_option("sqlalchemy.url", url)

target_metadata = SQLModel.metadata


def run_migrations_offline() -> None:
    """离线模式：只生成 SQL，不连数据库。"""
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """在线模式：连接数据库执行迁移。"""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
