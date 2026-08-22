"""页面目录：peewee Page → data/pages.sqlite。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from peewee import IntegerField, TextField

from libs.db import BaseModel, connect, database


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Page(BaseModel):
    """一条已提交抓取的 Wiki 页面及其总结状态。"""

    url = TextField(primary_key=True)
    title = TextField(default="")
    status = TextField()
    error = TextField(default="")
    chapter_total = IntegerField(default=0)
    chapter_ok = IntegerField(default=0)
    raw_path = TextField(default="")
    updated_at = TextField()

    class Meta:
        """pages 表。"""

        table_name = "pages"

    def __repr__(self) -> str:
        return f"<Page url='{self.url}', status='{self.status}'>"

    def to_dict(self) -> dict[str, Any]:
        """给 /pages API 用的字典。"""
        return {
            "url": self.url,
            "title": self.title,
            "status": self.status,
            "error": self.error,
            "chapter_total": self.chapter_total,
            "chapter_ok": self.chapter_ok,
            "raw_path": self.raw_path,
            "updated_at": self.updated_at,
        }

    @classmethod
    def upsert(
        cls,
        url: str,
        *,
        status: str,
        title: Optional[str] = None,
        error: Optional[str] = None,
        chapter_total: Optional[int] = None,
        chapter_ok: Optional[int] = None,
        raw_path: Optional[str] = None,
    ) -> Page:
        """按 url 插入或更新状态字段。"""
        url = url.strip()
        if not url:
            raise ValueError("url 不能为空")
        stamp = _now()
        page, created = cls.get_or_create(
            url=url,
            defaults={
                "title": title or "",
                "status": status,
                "error": error or "",
                "chapter_total": chapter_total if chapter_total is not None else 0,
                "chapter_ok": chapter_ok if chapter_ok is not None else 0,
                "raw_path": raw_path or "",
                "updated_at": stamp,
            },
        )
        if created:
            return page
        page.status = status
        page.updated_at = stamp
        if title is not None:
            page.title = title
        if error is not None:
            page.error = error
        if chapter_total is not None:
            page.chapter_total = chapter_total
        if chapter_ok is not None:
            page.chapter_ok = chapter_ok
        if raw_path is not None:
            page.raw_path = raw_path
        page.save()
        return page

    @classmethod
    def list_pages(cls) -> list[dict[str, Any]]:
        """全部页面，新更新的在前。"""
        query = cls.select().order_by(cls.updated_at.desc(), cls.url.asc())
        return [row.to_dict() for row in query]  # pylint: disable=not-an-iterable


def _ensure_table() -> None:
    connect()
    database.create_tables([Page])


_ensure_table()
