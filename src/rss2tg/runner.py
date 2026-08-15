from __future__ import annotations

import argparse
import sqlite3
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import feedparser
import httpx
import telebot

from .config import Config, FeedConfig, ProcessingOptions, load_config
from .history import (
    connect_database,
    entry_history_hash,
    has_history,
    remember_entry,
    remember_feed,
    remember_hashes,
    seen,
)
from .message import build_topic, entry_id, render_message, send_message


@dataclass(frozen=True, slots=True)
class FeedRunContext:
    conn: sqlite3.Connection
    bot: telebot.TeleBot | None
    config: Config
    options: ProcessingOptions


def fetch_feed_content(feed_cfg: FeedConfig, context: FeedRunContext) -> bytes:
    timeout = httpx.Timeout(45.0)
    headers = {"user-agent": context.config.app.user_agent}
    last_error: Exception | None = None
    for attempt in range(2):
        try:
            with httpx.Client(headers=headers, timeout=timeout, follow_redirects=True) as client:
                response = client.get(feed_cfg.url)
                response.raise_for_status()
                return response.content
        except httpx.HTTPError as exc:
            last_error = exc
            if attempt == 0:
                time.sleep(1.0)
    raise RuntimeError(f"fetch failed after retries: {feed_cfg.url}") from last_error


def process_feed(context: FeedRunContext, feed_cfg: FeedConfig) -> None:
    parsed = urlparse(feed_cfg.url)
    if parsed.scheme not in ("http", "https"):
        print(f"skipping unsupported scheme ({parsed.scheme}): {feed_cfg.url}")
        return
    feed_content = fetch_feed_content(feed_cfg, context)
    process_feed_content(context, feed_cfg, feed_content)


def process_feed_content(context: FeedRunContext, feed_cfg: FeedConfig, feed_content: bytes) -> None:
    print(f"checking: {feed_cfg.name} <{feed_cfg.url}>")

    feed = feedparser.parse(feed_content)
    if getattr(feed, "bozo", False):
        print(f"feed parse warning for {feed_cfg.url}: {getattr(feed, 'bozo_exception', '')}")
    if not getattr(feed, "entries", None):
        print(f"no entries: {feed_cfg.url}")
        return

    feed_has_history = has_history(context.conn, feed_cfg.url)
    entry_limit = (
        context.options.limit_entries
        if context.options.limit_entries is not None
        else context.config.app.max_entries_per_feed
    )
    entry_limit = max(0, entry_limit)
    entries = list(reversed(feed.entries[:entry_limit]))

    if (
        not feed_has_history
        and not context.config.app.send_on_first_run
        and not context.options.force_first_run
    ):
        print(f"bootstrap only: {feed_cfg.name}")
        if not context.options.dry_run and not context.options.no_history:
            hashes = [
                entry_history_hash(feed_cfg.url, item_id)
                for entry in entries
                if (item_id := entry_id(feed_cfg.url, entry))
            ]
            remember_hashes(context.conn, hashes)
            remember_feed(context.conn, feed_cfg.url)
        return

    if not feed_has_history and not context.options.dry_run and not context.options.no_history:
        remember_feed(context.conn, feed_cfg.url)

    for entry in entries:
        item_id = entry_id(feed_cfg.url, entry)
        if not item_id or seen(context.conn, feed_cfg.url, item_id):
            continue

        topic = build_topic(feed_cfg, feed, entry)
        if context.options.dry_run:
            rendered = render_message(topic)
            print(f"dry-run push: {rendered.splitlines()[0]} tags={topic['tags']}")
        elif context.options.no_send:
            rendered = render_message(topic)
            print(f"no-send push skipped: {rendered.splitlines()[0]} tags={topic['tags']}")
        else:
            if context.bot is None:
                raise RuntimeError("Telegram bot is not initialized")
            try:
                send_message(context.bot, topic, context.config)
            except Exception as exc:
                print(f"send failed for {feed_cfg.name}: {exc}")
                traceback.print_exc()
                continue

        if not context.options.dry_run and not context.options.no_history:
            remember_entry(context.conn, feed_cfg.url, item_id)


def parse_args() -> ProcessingOptions:
    parser = argparse.ArgumentParser(description="Send OPML RSS entries to Telegram incrementally.")
    parser.add_argument("--dry-run", action="store_true", help="Do not send Telegram messages or write history.")
    parser.add_argument(
        "--no-send",
        action="store_true",
        help="Do not send Telegram messages, but still allow history writes unless disabled.",
    )
    parser.add_argument("--no-history", action="store_true", help="Do not update the SQLite history database.")
    parser.add_argument(
        "--force-first-run",
        action="store_true",
        help="Process entries even when a feed has no history yet.",
    )
    parser.add_argument("--limit-feeds", type=int, default=None, help="Process only the first N feeds from OPML.")
    parser.add_argument("--limit-entries", type=int, default=None, help="Inspect only the first N entries per feed.")
    parser.add_argument(
        "--only-feed",
        action="append",
        default=None,
        help="Process only a named feed. Can be provided multiple times.",
    )
    args = parser.parse_args()
    return ProcessingOptions(
        dry_run=args.dry_run,
        no_send=args.no_send,
        no_history=args.no_history,
        force_first_run=args.force_first_run,
        limit_feeds=args.limit_feeds,
        limit_entries=args.limit_entries,
        only_feeds=args.only_feed,
    )


def main() -> None:
    options = parse_args()
    config = load_config()
    bot = None if options.dry_run or options.no_send else telebot.TeleBot(config.telegram.bot_token)

    print(f"loaded feeds: {len(config.feeds)} from {config.app.opml_file}")
    feeds = config.feeds
    if options.only_feeds:
        wanted = set(options.only_feeds)
        feeds = [feed_cfg for feed_cfg in feeds if feed_cfg.name in wanted]
        missing = sorted(wanted - {feed_cfg.name for feed_cfg in feeds})
        if missing:
            print(f"missing requested feeds: {', '.join(missing)}")

    skipped_feeds = [feed_cfg for feed_cfg in feeds if urlparse(feed_cfg.url).scheme not in ("http", "https")]
    for feed_cfg in skipped_feeds:
        print(f"skipping unsupported scheme ({urlparse(feed_cfg.url).scheme}): {feed_cfg.url}")
    feeds = [feed_cfg for feed_cfg in feeds if urlparse(feed_cfg.url).scheme in ("http", "https")]

    if options.limit_feeds is not None:
        feeds = feeds[: max(0, options.limit_feeds)]

    if options.dry_run and not options.no_history:
        database_path = config.app.database if Path(config.app.database).exists() else ":memory:"
        readonly = True
    else:
        database_path = ":memory:" if options.dry_run or options.no_history else config.app.database
        readonly = False

    with closing(connect_database(database_path, readonly=readonly)) as conn:
        context = FeedRunContext(conn=conn, bot=bot, config=config, options=options)
        contents = prefetch_feeds(feeds, context)
        for feed_cfg in feeds:
            feed_content = contents.get(feed_cfg.url)
            if feed_content is None:
                continue
            try:
                process_feed_content(context, feed_cfg, feed_content)
            except Exception as exc:
                print(f"failed: {feed_cfg.name} <{feed_cfg.url}>: {exc}")
                traceback.print_exc()


def prefetch_feeds(feeds: list[FeedConfig], context: FeedRunContext) -> dict[str, bytes]:
    if not feeds:
        return {}
    contents: dict[str, bytes] = {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {
            pool.submit(fetch_feed_content, feed_cfg, context): feed_cfg
            for feed_cfg in feeds
        }
        for future in as_completed(futures):
            feed_cfg = futures[future]
            try:
                contents[feed_cfg.url] = future.result()
            except Exception as exc:
                print(f"fetch failed: {feed_cfg.name} <{feed_cfg.url}>: {exc}")
    return contents
