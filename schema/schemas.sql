-- data/pages.sqlite 表现状

-- 抓取/总结状态。url 即主键。
CREATE TABLE IF NOT EXISTS pages (
    url TEXT NOT NULL PRIMARY KEY,
    title TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL,
    error TEXT NOT NULL DEFAULT '',
    chapter_total INTEGER NOT NULL DEFAULT 0,
    chapter_ok INTEGER NOT NULL DEFAULT 0,
    raw_path TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL
);

-- 中英专名。无主键。
-- source: 1 = Genshin Dictionary（官中），2 = 本地补录（zh 可空）
CREATE TABLE IF NOT EXISTS dictionary (
    en TEXT NOT NULL,
    zh TEXT NOT NULL DEFAULT '',
    source INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS dictionary_en ON dictionary (en);
CREATE INDEX IF NOT EXISTS dictionary_source ON dictionary (source);
