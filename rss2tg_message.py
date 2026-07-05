from __future__ import annotations

import html
import time
from typing import Any, TypedDict
from urllib.parse import urlparse

import telebot

from rss2tg_config import Config, FeedConfig, feed_tags


class Topic(TypedDict):
    feed_name: str
    site_name: str
    title: str
    display_title: str
    summary: str
    link: str
    published: str
    tags: str


def render_message(topic: Topic) -> str:
    title = html.escape(topic.get("display_title", topic.get("title", "")))
    link = html.escape(topic.get("link", ""))
    tags = html.escape(topic.get("tags", ""))
    return f"<b>{title}</b>\n{link}\n\n{tags}"


def send_message(bot: telebot.TeleBot, topic: Topic, config: Config) -> bool:
    message = render_message(topic)
    for destination in config.telegram.destinations:
        bot.send_message(
            destination,
            message,
            parse_mode="HTML",
            disable_web_page_preview=True,
            message_thread_id=config.telegram.topic,
        )

    print(f"sent: {topic['title']}")
    time.sleep(config.app.sleep_between_messages)
    return True


def entry_link(entry: Any) -> str | None:
    if getattr(entry, "link", None):
        return str(entry.link)
    links = getattr(entry, "links", [])
    if links:
        return str(links[0].get("href"))
    return None


def entry_id(feed_url: str, entry: Any) -> str | None:
    link = entry_link(entry)
    if link:
        return link
    value = getattr(entry, "id", None) or getattr(entry, "guid", None)
    return str(value) if value else None


def feed_site_name(feed: Any, feed_url: str) -> str:
    title = getattr(feed.feed, "title", None)
    if title:
        return str(title)
    host = urlparse(feed_url).netloc
    return host or feed_url


def build_topic(feed_cfg: FeedConfig, feed: Any, entry: Any) -> Topic:
    link = entry_link(entry) or entry_id(feed_cfg.url, entry) or feed_cfg.url
    title = str(getattr(entry, "title", "Untitled")).strip()
    return {
        "feed_name": feed_cfg.name,
        "site_name": feed_site_name(feed, feed_cfg.url),
        "title": title,
        "display_title": title,
        "summary": str(getattr(entry, "summary", "")),
        "link": link,
        "published": str(getattr(entry, "published", getattr(entry, "updated", ""))),
        "tags": feed_tags(feed_cfg),
    }
