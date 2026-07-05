from __future__ import annotations

import argparse
import sqlite3
import traceback
from dataclasses import dataclass
from urllib.parse import urlparse

import feedparser
import httpx
import telebot

from .config import Config, FeedConfig, ProcessingOptions, load_config
from .history import connect_database, has_history, remember_entry, remember_feed, seen
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
    with httpx.Client(headers=headers, timeout=timeout, follow_redirects=True) as client:
        response = client.get(feed_cfg.url)
        response.raise_for_status()
        return response.content


def process_feed(context: FeedRunContext, feed_cfg: FeedConfig) -> None:
    print(f"checking: {feed_cfg.name} <{feed_cfg.url}>")

    parsed = urlparse(feed_cfg.url)
    if parsed.scheme not in ("http", "https"):
        print(f"skipping unsupported scheme ({parsed.scheme}): {feed_cfg.url}")
        return
    feed_content = fetch_feed_content(feed_cfg, context)
    feed = feedparser.parse(feed_content)
    if getattr(feed, "bozo", False):
        print(f"feed parse warning for {feed_cfg.url}: {getattr(feed, 'bozo_exception', '')}")
    if not getattr(feed, "entries", None):
        print(f"no entries: {feed_cfg.url}")
        return

    feed_has_history = has_history(context.conn, feed_cfg.url)
    entry_limit = context.options.limit_entries or context.config.app.max_entries_per_feed
    entries = list(reversed(feed.entries[:entry_limit]))

    if (
        not feed_has_history
        and not context.config.app.send_on_first_run
        and not context.options.force_first_run
    ):
        print(f"bootstrap only: {feed_cfg.name}")
        if not context.options.dry_run and not context.options.no_history:
            for entry in entries:
                item_id = entry_id(feed_cfg.url, entry)
                if item_id:
                    remember_entry(context.conn, feed_cfg.url, item_id)
            remember_feed(context.conn, feed_cfg.url)
        return

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
            send_message(context.bot, topic, context.config)

        if not context.options.dry_run and not context.options.no_history:
            remember_entry(context.conn, feed_cfg.url, item_id)

    if not feed_has_history and not context.options.dry_run and not context.options.no_history:
        remember_feed(context.conn, feed_cfg.url)


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
    if options.limit_feeds:
        feeds = feeds[: options.limit_feeds]
    database_path = ":memory:" if options.dry_run or options.no_history else config.app.database
    with connect_database(database_path) as conn:
        context = FeedRunContext(conn=conn, bot=bot, config=config, options=options)
        for feed_cfg in feeds:
            try:
                process_feed(context, feed_cfg)
            except Exception as exc:
                print(f"failed: {feed_cfg.name} <{feed_cfg.url}>: {exc}")
                traceback.print_exc()
