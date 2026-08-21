"""共用 peewee 连接：data/pages.sqlite。"""

from __future__ import annotations

from pathlib import Path

from peewee import Model, SqliteDatabase

import config

config.PAGES_DB.parent.mkdir(parents=True, exist_ok=True)

database = SqliteDatabase(
    str(config.PAGES_DB),
    pragmas={"journal_mode": "wal"},
    check_same_thread=False,
)


class BaseModel(Model):
    class Meta:
        database = database


def connect(path: str | Path | None = None) -> SqliteDatabase:
    target = Path(path or config.PAGES_DB)
    target.parent.mkdir(parents=True, exist_ok=True)
    resolved = str(target)
    if database.database != resolved:
        if not database.is_closed():
            database.close()
        database.init(
            resolved,
            pragmas={"journal_mode": "wal"},
            check_same_thread=False,
        )
    if database.is_closed():
        database.connect()
    return database
