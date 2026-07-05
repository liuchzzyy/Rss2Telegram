from __future__ import annotations

from rss2tg_config import (
    AppConfig,
    Config,
    FeedConfig,
    ProcessingOptions,
    TelegramConfig,
    env_first,
    feed_tags,
    journal_tag_name,
    load_config,
    parse_bool,
    parse_env,
    parse_list,
    parse_opml,
    tag_slug,
)
from rss2tg_history import (
    connect_database,
    entry_history_hash,
    feed_history_hash,
    has_history,
    history_hash,
    remember_entry,
    remember_feed,
    seen,
)
from rss2tg_message import Topic, build_topic, entry_id, entry_link, render_message, send_message
from rss2tg_runner import FeedRunContext, main, parse_args, process_feed

__all__ = [
    "AppConfig",
    "Config",
    "FeedConfig",
    "FeedRunContext",
    "ProcessingOptions",
    "TelegramConfig",
    "Topic",
    "build_topic",
    "connect_database",
    "entry_history_hash",
    "entry_id",
    "entry_link",
    "env_first",
    "feed_history_hash",
    "feed_tags",
    "has_history",
    "history_hash",
    "journal_tag_name",
    "load_config",
    "main",
    "parse_args",
    "parse_bool",
    "parse_env",
    "parse_list",
    "parse_opml",
    "process_feed",
    "remember_entry",
    "remember_feed",
    "render_message",
    "seen",
    "send_message",
    "tag_slug",
]


if __name__ == "__main__":
    main()
