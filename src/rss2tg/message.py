from __future__ import annotations

import html
import re
import time
from typing import Any, TypedDict

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
    title: str
    display_title: str
    link: str
    doi: str
    tags: str


TELEGRAM_MAX_MESSAGE_LENGTH = 4096


def _truncate_html_text(text: str, limit: int) -> str:
    cut = text[: max(1, limit - 1)] + "…"
    amp = cut.rfind("&")
    semi = cut.rfind(";")
    if amp > semi:
        cut = cut[:amp]
    return cut


def render_message(topic: Topic) -> str:
    title = html.escape(str(topic.get("display_title", topic.get("title", ""))))
    link = html.escape(topic.get("link", ""))
    doi = html.escape(topic.get("doi", ""))
    tags = html.escape(topic.get("tags", ""))
    suffix = f"\n{link}\nDOI: {doi}\n\n{tags}"
    title_budget = TELEGRAM_MAX_MESSAGE_LENGTH - len(suffix) - len("<b>") - len("</b>")
    if len(title) > title_budget:
        title = _truncate_html_text(title, title_budget)
    return f"<b>{title}</b>{suffix}"


def send_message(bot: telebot.TeleBot, topic: Topic, config: Config) -> bool:
    message = render_message(topic)
    destinations = config.telegram.destinations
    failures: list[str] = []
    for destination in destinations:
        try:
            bot.send_message(
                destination,
                message,
                parse_mode="HTML",
                disable_web_page_preview=True,
                message_thread_id=config.telegram.topic,
            )
        except Exception as exc:
            failures.append(f"{destination}: {exc}")

    if failures:
        print(f"partial send failure for {topic['title']}: {'; '.join(failures)}")
    if destinations and len(failures) == len(destinations):
        raise RuntimeError(f"all destinations failed for {topic['title']}")

    print(f"sent: {topic['title']}")
    time.sleep(config.app.sleep_between_messages)
    return True


def entry_link(entry: Any) -> str | None:
    link = entry_value(entry, "link")
    if link:
        return str(link)

    links = entry_value(entry, "links") or []
    if links:
        for link_item in links:
            href = (
                link_item.get("href")
                if isinstance(link_item, dict)
                else getattr(link_item, "href", None)
            )
            if href:
                return str(href)
    return None


def entry_id(feed_url: str, entry: Any) -> str | None:
    link = entry_link(entry)
    if link:
        return link
    value = entry_value(entry, "id") or entry_value(entry, "guid")
    return str(value) if value else None


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
    title = str(entry_value(entry, "title") or "Untitled").strip()
    doi = extract_doi(entry) if feed_cfg.feed_kind == "journal" else None
    return {
        "title": title,
        "display_title": title,
        "link": link,
        "doi": doi or "",
        "tags": feed_tags(feed_cfg),
    }
