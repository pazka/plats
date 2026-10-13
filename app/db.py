"""SQLite access (WAL) and schema migrations, applied on start."""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from . import seed_data
from .text import moderation_norm

SCHEMA_V1 = """
CREATE TABLE categories (
    id INTEGER PRIMARY KEY,
    slug TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    color TEXT NOT NULL,
    emoji TEXT NOT NULL DEFAULT '',
    position INTEGER NOT NULL DEFAULT 0,
    active INTEGER NOT NULL DEFAULT 1,
    created_at INTEGER NOT NULL
);

CREATE TABLE dishes (
    id INTEGER PRIMARY KEY,
    slug TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    title_norm TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    region TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL CHECK (source IN ('wikidata', 'user', 'manual')),
    qid TEXT UNIQUE,
    wiki_url TEXT NOT NULL DEFAULT '',
    image_path TEXT NOT NULL DEFAULT '',
    image_author TEXT NOT NULL DEFAULT '',
    image_license TEXT NOT NULL DEFAULT '',
    image_license_url TEXT NOT NULL DEFAULT '',
    image_source_url TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'hidden', 'pending', 'flagged', 'rejected', 'merged')),
    flag_reason TEXT NOT NULL DEFAULT '',
    merged_into INTEGER REFERENCES dishes(id),
    admin_edited INTEGER NOT NULL DEFAULT 0,
    submitter_visitor TEXT,
    submitter_ip_hash TEXT,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);
CREATE INDEX dishes_status ON dishes(status);
CREATE INDEX dishes_source ON dishes(source);

CREATE TABLE votes (
    id INTEGER PRIMARY KEY,
    dish_id INTEGER NOT NULL REFERENCES dishes(id),
    visitor_id TEXT NOT NULL,
    category_id INTEGER REFERENCES categories(id),  -- NULL = "je ne connais pas" (skip)
    ip_hash TEXT NOT NULL DEFAULT '',
    created_at INTEGER NOT NULL,
    UNIQUE (visitor_id, dish_id)
);
CREATE INDEX votes_dish ON votes(dish_id, category_id);
CREATE INDEX votes_ip ON votes(ip_hash);
CREATE INDEX votes_created ON votes(created_at);

CREATE TABLE used_tokens (
    sig TEXT PRIMARY KEY,
    expires_at INTEGER NOT NULL
);

CREATE TABLE banned_words (
    id INTEGER PRIMARY KEY,
    word TEXT NOT NULL,
    word_norm TEXT NOT NULL,
    mode TEXT NOT NULL CHECK (mode IN ('word', 'substring')),
    created_at INTEGER NOT NULL,
    UNIQUE (word_norm, mode)
);

CREATE TABLE settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE audit_log (
    id INTEGER PRIMARY KEY,
    ts INTEGER NOT NULL,
    action TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '',
    ip_hash TEXT NOT NULL DEFAULT ''
);

CREATE TABLE admin_sessions (
    id_hash TEXT PRIMARY KEY,
    csrf TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL
);

CREATE TABLE login_attempts (
    id INTEGER PRIMARY KEY,
    ts INTEGER NOT NULL,
    ip_hash TEXT NOT NULL,
    success INTEGER NOT NULL
);
CREATE INDEX login_attempts_ts ON login_attempts(ts);

CREATE VIRTUAL TABLE dishes_fts USING fts5(
    title, description, region,
    content='dishes', content_rowid='id',
    tokenize='unicode61 remove_diacritics 2'
);
CREATE TRIGGER dishes_ai AFTER INSERT ON dishes BEGIN
    INSERT INTO dishes_fts(rowid, title, description, region)
    VALUES (new.id, new.title, new.description, new.region);
END;
CREATE TRIGGER dishes_ad AFTER DELETE ON dishes BEGIN
    INSERT INTO dishes_fts(dishes_fts, rowid, title, description, region)
    VALUES ('delete', old.id, old.title, old.description, old.region);
END;
CREATE TRIGGER dishes_au AFTER UPDATE OF title, description, region ON dishes BEGIN
    INSERT INTO dishes_fts(dishes_fts, rowid, title, description, region)
    VALUES ('delete', old.id, old.title, old.description, old.region);
    INSERT INTO dishes_fts(rowid, title, description, region)
    VALUES (new.id, new.title, new.description, new.region);
END;
"""


def _seed_v1(conn: sqlite3.Connection) -> None:
    now = int(time.time())
    for pos, (slug, name, color, emoji) in enumerate(seed_data.CATEGORIES, start=1):
        conn.execute(
            "INSERT INTO categories(slug, name, color, emoji, position, active, created_at)"
            " VALUES (?,?,?,?,?,1,?)",
            (slug, name, color, emoji, pos, now),
        )
    for key, value in seed_data.DEFAULT_SETTINGS.items():
        conn.execute("INSERT INTO settings(key, value) VALUES (?, ?)", (key, value))
    for word, mode in seed_data.BANNED_WORDS:
        conn.execute(
            "INSERT OR IGNORE INTO banned_words(word, word_norm, mode, created_at) VALUES (?,?,?,?)",
            (word, moderation_norm(word), mode, now),
        )


def _add_saucisse_v2(conn: sqlite3.Connection) -> None:
    """Owner request after v0.1.1: a 10th category. Appended last; no-op if the admin already
    created a category with this slug."""
    if conn.execute("SELECT 1 FROM categories WHERE slug = 'saucisse'").fetchone():
        return
    position = conn.execute("SELECT COALESCE(MAX(position), 0) + 1 FROM categories").fetchone()[0]
    slug, name, color, emoji = seed_data.CATEGORY_SAUCISSE
    conn.execute(
        "INSERT INTO categories(slug, name, color, emoji, position, active, created_at) VALUES (?,?,?,?,?,1,?)",
        (slug, name, color, emoji, position, int(time.time())),
    )


DISHES_V3 = """
CREATE TABLE dishes_v3 (
    id INTEGER PRIMARY KEY,
    slug TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    title_norm TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    region TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL CHECK (source <> ''),
    qid TEXT UNIQUE,
    source_key TEXT UNIQUE,
    wiki_url TEXT NOT NULL DEFAULT '',
    recipe_url TEXT NOT NULL DEFAULT '',
    source_text TEXT NOT NULL DEFAULT '',
    image_path TEXT NOT NULL DEFAULT '',
    image_author TEXT NOT NULL DEFAULT '',
    image_license TEXT NOT NULL DEFAULT '',
    image_license_url TEXT NOT NULL DEFAULT '',
    image_source_url TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'hidden', 'pending', 'flagged', 'rejected', 'merged', 'review')),
    flag_reason TEXT NOT NULL DEFAULT '',
    merged_into INTEGER REFERENCES dishes(id),
    admin_edited INTEGER NOT NULL DEFAULT 0,
    submitter_visitor TEXT,
    submitter_ip_hash TEXT,
    review_kind TEXT NOT NULL DEFAULT '',
    review_verdict TEXT NOT NULL DEFAULT '',
    review_reason TEXT NOT NULL DEFAULT '',
    review_model TEXT NOT NULL DEFAULT '',
    reviewed_at INTEGER,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);
"""
_DISHES_V1_COLUMNS = (
    "id, slug, title, title_norm, description, region, source, qid, wiki_url, image_path, image_author,"
    " image_license, image_license_url, image_source_url, status, flag_reason, merged_into, admin_edited,"
    " submitter_visitor, submitter_ip_hash, created_at, updated_at"
)


def _sources_reviews_v3(conn: sqlite3.Connection) -> None:
    """More dish sources (cuisine-facile, Wikipédia, Elle…), a recipe link per dish, the
    'review' status (scraped, waiting for the reviewers) and the reviewers' verdict.
    SQLite cannot change a CHECK constraint in place: the table is rebuilt (foreign keys are
    off during migrations, `foreign_key_check` runs before the commit)."""
    for statement in _split_sql(DISHES_V3):
        conn.execute(statement)
    conn.execute(f"INSERT INTO dishes_v3 ({_DISHES_V1_COLUMNS}) SELECT {_DISHES_V1_COLUMNS} FROM dishes")
    conn.execute("DROP TABLE dishes")  # also drops its indexes and the FTS triggers
    conn.execute("ALTER TABLE dishes_v3 RENAME TO dishes")
    for statement in _split_sql(DISHES_INDEXES_AND_TRIGGERS):
        conn.execute(statement)
    conn.execute("INSERT INTO dishes_fts(dishes_fts) VALUES ('rebuild')")


DISHES_INDEXES_AND_TRIGGERS = """
CREATE INDEX dishes_status ON dishes(status);
CREATE INDEX dishes_source ON dishes(source);
CREATE TRIGGER dishes_ai AFTER INSERT ON dishes BEGIN
    INSERT INTO dishes_fts(rowid, title, description, region)
    VALUES (new.id, new.title, new.description, new.region);
END;
CREATE TRIGGER dishes_ad AFTER DELETE ON dishes BEGIN
    INSERT INTO dishes_fts(dishes_fts, rowid, title, description, region)
    VALUES ('delete', old.id, old.title, old.description, old.region);
END;
CREATE TRIGGER dishes_au AFTER UPDATE OF title, description, region ON dishes BEGIN
    INSERT INTO dishes_fts(dishes_fts, rowid, title, description, region)
    VALUES ('delete', old.id, old.title, old.description, old.region);
    INSERT INTO dishes_fts(rowid, title, description, region)
    VALUES (new.id, new.title, new.description, new.region);
END;
"""


# Each migration: (version, sql, python hook or None). Never edit a released migration.
MIGRATIONS = [
    (1, SCHEMA_V1, _seed_v1),
    (2, "", _add_saucisse_v2),
    (3, "", _sources_reviews_v3),
]


def connect(db_path: Path | str) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=10, check_same_thread=False, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 10000")
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


def migrate(db_path: Path | str) -> int:
    conn = connect(db_path)
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        # Off for table rebuilds (the documented SQLite procedure); checked before each commit.
        conn.execute("PRAGMA foreign_keys = OFF")
        current = conn.execute("PRAGMA user_version").fetchone()[0]
        for version, sql, hook in MIGRATIONS:
            if version <= current:
                continue
            conn.execute("BEGIN IMMEDIATE")
            try:
                for statement in _split_sql(sql):
                    conn.execute(statement)
                if hook:
                    hook(conn)
                if conn.execute("PRAGMA foreign_key_check").fetchone():
                    raise RuntimeError(f"migration {version}: foreign key check failed")
                conn.execute(f"PRAGMA user_version = {int(version)}")
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
            current = version
        return current
    finally:
        conn.close()


def _split_sql(sql: str):
    """Split a script on ';' while keeping trigger bodies (BEGIN … END;) intact."""
    buf = ""
    for line in sql.splitlines(keepends=True):
        buf += line
        if sqlite3.complete_statement(buf):
            if buf.strip():
                yield buf.strip()
            buf = ""
    if buf.strip():
        yield buf.strip()


class Tx:
    """`with Tx(conn):` — BEGIN IMMEDIATE … COMMIT/ROLLBACK (connection is in autocommit mode)."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def __enter__(self):
        self.conn.execute("BEGIN IMMEDIATE")
        return self.conn

    def __exit__(self, exc_type, exc, tb):
        self.conn.execute("ROLLBACK" if exc_type else "COMMIT")
        return False


def get_setting(conn: sqlite3.Connection, key: str, default: str = "") -> str:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    if row is None:
        return seed_data.DEFAULT_SETTINGS.get(key, default)
    return row["value"]


def set_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO settings(key, value) VALUES (?, ?)"
        " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


def audit(conn: sqlite3.Connection, action: str, detail: str = "", ip_hash: str = "") -> None:
    conn.execute(
        "INSERT INTO audit_log(ts, action, detail, ip_hash) VALUES (?,?,?,?)",
        (int(time.time()), action, detail[:2000], ip_hash),
    )
