"""每个用例用临时 sqlite，避免碰到 data/pages.sqlite。"""

from __future__ import annotations

import pytest

from libs.db import connect, database
from libs.page_store import Page
from models.dictionary import Dictionary


@pytest.fixture(autouse=True)
def tmp_sqlite(tmp_path):
    db_path = tmp_path / "pages.sqlite"
    connect(db_path)
    database.create_tables([Page, Dictionary])
    Dictionary.delete().execute()
    Page.delete().execute()
    yield db_path
