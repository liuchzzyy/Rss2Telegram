from __future__ import annotations

import html
import re
import time
from typing import Any, TypedDict
from urllib.parse import urlparse

import telebot

from .config import Config, FeedConfig, feed_tags

DOI_RE = re.compile(
    r"""
    \b
    (10\.\d{4,9}/[-._;()/:A-Z0-9]+)
    """,
    re.IGNORECASE | re.VERBOSE,
)
DOI_PREFIX_RE = re.compile(r"^(?:doi:\s*|https?://(?:dx\.)?doi\.org/)", re.IGNORECASE)


class Topic(TypedDict):
    feed_name: str
    site_name: str
    title: str
    display_title: str
    summary: str
    link: str
    published: str
    doi: str
    tags: str


def render_message(topic: Topic) -> str:
    title = html.escape(topic.get("display_title", topic.get("title", "")))
    link = html.escape(topic.get("link", ""))
    doi = html.escape(topic.get("doi", ""))
    tags = html.escape(topic.get("tags", ""))
    return f"<b>{title}</b>\n{link}\nDOI: {doi}\n\n{tags}"


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


def normalize_doi(value: str | None) -> str | None:
    if not value:
        return None
    value = html.unescape(str(value)).strip()
    value = DOI_PREFIX_RE.sub("", value)
    match = DOI_RE.search(value)
    if not match:
        return None
    value = match.group(1)
    value = value.strip().strip("<>[](){} \t\r\n")
    value = value.rstrip(".,;:")
    return value.lower() or None


def iter_doi_candidates(value: Any):
    if value is None:
        return
    if isinstance(value, str):
        yield value
        return
    if isinstance(value, dict):
        for key in ("value", "href", "content", "text", "title"):
            if key in value:
                yield from iter_doi_candidates(value[key])
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            yield from iter_doi_candidates(item)
        return
    yield str(value)


def entry_value(entry: Any, name: str) -> Any:
    if isinstance(entry, dict):
        return entry.get(name)
    return getattr(entry, name, None)


def extract_doi(entry: Any) -> str | None:
    candidate_fields = (
        "doi",
        "prism_doi",
        "dc_identifier",
        "dc:identifier",
        "id",
        "guid",
        "link",
        "summary",
        "description",
        "content",
    )
    for field in candidate_fields:
        for candidate in iter_doi_candidates(entry_value(entry, field)):
            doi = normalize_doi(candidate)
            if doi:
                return doi
    return None


def build_topic(feed_cfg: FeedConfig, feed: Any, entry: Any) -> Topic:
    link = entry_link(entry) or entry_id(feed_cfg.url, entry) or feed_cfg.url
    title = str(getattr(entry, "title", "Untitled")).strip()
    doi = extract_doi(entry) if feed_cfg.feed_kind == "journal" else None
    return {
        "feed_name": feed_cfg.name,
        "site_name": feed_site_name(feed, feed_cfg.url),
        "title": title,
        "display_title": title,
        "summary": str(getattr(entry, "summary", "")),
        "link": link,
        "published": str(getattr(entry, "published", getattr(entry, "updated", ""))),
        "doi": doi or "",
        "tags": feed_tags(feed_cfg),
    }
