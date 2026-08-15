from __future__ import annotations

import hashlib
import sqlite3
import time
from pathlib import Path


def connect_database(path: str, *, readonly: bool = False) -> sqlite3.Connection:
    if readonly and path != ":memory:":
        conn = sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True)
        existing = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'history'"
        ).fetchone()
        if existing is None:
            conn.close()
            raise SystemExit(f"history table is missing in read-only database: {path}")
        return conn

    conn = sqlite3.connect(path)
    existing = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'history'"
    ).fetchone()
    if existing:
        columns = {
            row[1]
            for row in conn.execute("PRAGMA table_info(history)").fetchall()
        }
        if columns != {"hash"}:
            migrate_history_to_hashes(conn, columns)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS history ("
        "hash TEXT NOT NULL PRIMARY KEY"
        ")"
    )
    conn.commit()
    drop_legacy_history_tables(conn)
    return conn


def history_hash(*parts: str) -> str:
    payload = "\0".join(parts).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def feed_history_hash(feed_url: str) -> str:
    return history_hash("feed", feed_url)


def entry_history_hash(feed_url: str, item_id: str) -> str:
    return history_hash("entry", feed_url, item_id)


def migrate_history_to_hashes(conn: sqlite3.Connection, columns: set[str]) -> None:
    legacy_name = f"history_legacy_{int(time.time())}"
    conn.execute(f"ALTER TABLE history RENAME TO {quote_identifier(legacy_name)}")
    conn.execute(
        "CREATE TABLE history ("
        "hash TEXT NOT NULL PRIMARY KEY"
        ")"
    )

    if "hash" in columns:
        conn.execute(
            f"INSERT OR IGNORE INTO history (hash) "
            f"SELECT hash FROM {legacy_name} "
            "WHERE hash IS NOT NULL AND trim(hash) != ''"
        )

    if {"feed_url", "link"}.issubset(columns):
        conn.create_function("rss2tg_feed_hash", 1, lambda value: feed_history_hash(str(value)))
        conn.create_function(
            "rss2tg_entry_hash",
            2,
            lambda feed_url, item_id: entry_history_hash(str(feed_url), str(item_id)),
        )
        conn.execute(
            "INSERT OR IGNORE INTO history (hash) "
            "SELECT rss2tg_feed_hash(feed_url) "
            f"FROM {legacy_name} "
            "WHERE feed_url IS NOT NULL AND trim(feed_url) != ''"
        )
        conn.execute(
            "INSERT OR IGNORE INTO history (hash) "
            "SELECT rss2tg_entry_hash(feed_url, link) "
            f"FROM {legacy_name} "
            "WHERE feed_url IS NOT NULL "
            "AND trim(feed_url) != '' "
            "AND link IS NOT NULL "
            "AND trim(link) != ''"
        )

    conn.execute(f"DROP TABLE {legacy_name}")
    conn.commit()
    conn.execute("VACUUM")


def quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def drop_legacy_history_tables(conn: sqlite3.Connection) -> None:
    legacy_tables = [
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name LIKE 'history_legacy_%'"
        )
    ]
    if not legacy_tables:
        return

    for table in legacy_tables:
        conn.execute(f"DROP TABLE {quote_identifier(table)}")
    conn.commit()
    conn.execute("VACUUM")


def hash_seen(conn: sqlite3.Connection, value: str) -> bool:
    row = conn.execute("SELECT 1 FROM history WHERE hash = ? LIMIT 1", (value,)).fetchone()
    return row is not None


def has_history(conn: sqlite3.Connection, feed_url: str) -> bool:
    return hash_seen(conn, feed_history_hash(feed_url))


def seen(conn: sqlite3.Connection, feed_url: str, item_id: str) -> bool:
    return hash_seen(conn, entry_history_hash(feed_url, item_id))


def remember_hash(conn: sqlite3.Connection, value: str) -> None:
    conn.execute("INSERT OR IGNORE INTO history (hash) VALUES (?)", (value,))
    conn.commit()


def remember_hashes(conn: sqlite3.Connection, values: list[str]) -> None:
    if not values:
        return
    conn.executemany(
        "INSERT OR IGNORE INTO history (hash) VALUES (?)",
        [(value,) for value in values],
    )
    conn.commit()


def remember_feed(conn: sqlite3.Connection, feed_url: str) -> None:
    remember_hash(conn, feed_history_hash(feed_url))


def remember_entry(conn: sqlite3.Connection, feed_url: str, item_id: str) -> None:
    remember_hash(conn, entry_history_hash(feed_url, item_id))
