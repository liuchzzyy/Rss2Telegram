from __future__ import annotations

import hashlib
import html
import argparse
import random
import re
import sqlite3
import time
import traceback
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import feedparser
import requests
import telebot
import yaml
from bs4 import BeautifulSoup
from telebot import types


DEFAULT_ENV_FILE = ".env"
DEFAULT_OPML_FILE = "Subscriptions.opml"
DEFAULT_DATABASE = "rss2telegram.db"
DEFAULT_ROUTES_FILE = "feed_routes.yaml"
DEFAULT_MAX_ENTRIES_PER_FEED = 100
DEFAULT_MESSAGE_TEMPLATE = "<b>{TITLE}</b>\n{LINK}"
DEFAULT_USER_AGENT = "rss2telegram (+https://github.com/liuchzzyy/Rss2Telegram)"
EMPTY_CONFIG_VALUES = {"", "none", "null", "nil", "-"}
DEFAULT_OBSIDIAN_INBOX_DIR = "F:/ChengL1u/01_收件箱/0102_RSS"
DEFAULT_OBSIDIAN_DATE_FORMAT = "%Y%m%d"


@dataclass(frozen=True)
class FeedConfig:
    name: str
    url: str


@dataclass(frozen=True)
class AppConfig:
    opml_file: str
    database: str
    routes_file: str
    obsidian_inbox_dir: str
    obsidian_date_format: str
    max_entries_per_feed: int
    send_on_first_run: bool
    fetch_images: bool
    request_timeout: int
    sleep_between_messages: float
    user_agent: str


@dataclass(frozen=True)
class TelegramConfig:
    bot_token: str
    destinations: list[str]
    topic: int | None
    message_template: str
    button_text: str | None
    hide_button: bool
    parameters: str | None
    enable_telegraph: bool
    telegraph_token: str | None
    emojis: list[str]

    @property
    def use_telegraph(self) -> bool:
        return self.enable_telegraph and bool(self.telegraph_token)


@dataclass(frozen=True)
class Config:
    app: AppConfig
    telegram: TelegramConfig
    feeds: list[FeedConfig]

@dataclass(frozen=True)
class TierConfig:
    name: str
    label: str
    prefix: str
    archive_file: str | None
    description: str

@dataclass(frozen=True)
class FeedRoute:
    tier: str
    action: str
    reason: str

@dataclass(frozen=True)
class RouteConfig:
    tiers: dict[str, TierConfig]
    feeds: dict[str, FeedRoute]
    obsidian_inbox_dir: str
    obsidian_date_format: str

@dataclass(frozen=True)
class ProcessingOptions:
    dry_run: bool = False
    no_send: bool = False
    no_history: bool = False
    force_first_run: bool = False
    limit_feeds: int | None = None
    limit_entries: int | None = None
    no_archive: bool = False
    only_feeds: list[str] | None = None


def parse_env(path: str = DEFAULT_ENV_FILE) -> dict[str, str]:
    env_path = Path(path)
    if not env_path.exists():
        raise SystemExit(f"Missing personal config file: {env_path}")

    values: dict[str, str] = {}
    with env_path.open("r", encoding="utf-8") as fh:
        for line_number, raw_line in enumerate(fh, start=1):
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                print(f"Skipping malformed .env line {line_number}: {line}")
                continue

            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
                value = value[1:-1]
            values[key] = value.replace("\\n", "\n")
    return values


def env_first(values: dict[str, str], *keys: str, default: str | None = None) -> str | None:
    for key in keys:
        value = values.get(key)
        if value is None:
            continue
        value = value.strip()
        if value.lower() not in EMPTY_CONFIG_VALUES:
            return value
    return default


def parse_bool(value: Any, default: bool = False) -> bool:
    if value in (None, ""):
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def parse_list(value: str | None) -> list[str]:
    if not value or value.strip().lower() in EMPTY_CONFIG_VALUES:
        return []
    return [item.strip() for item in re.split(r"[,;]", value) if item.strip()]


def parse_opml(path: str) -> list[FeedConfig]:
    opml_path = Path(path)
    if not opml_path.exists():
        raise SystemExit(f"Missing OPML feed file: {opml_path}")

    root = ET.parse(opml_path).getroot()
    feeds: list[FeedConfig] = []
    seen_urls: set[str] = set()
    for node in root.iter():
        url = node.attrib.get("xmlUrl")
        if not url:
            continue
        url = url.strip()
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)
        name = node.attrib.get("text") or node.attrib.get("title") or url
        feeds.append(FeedConfig(name=name.strip(), url=url))

    if not feeds:
        raise SystemExit(f"No feeds found in OPML file: {opml_path}")
    return feeds


def load_config() -> Config:
    values = parse_env(DEFAULT_ENV_FILE)

    opml_file = env_first(values, "OPML_FILE", default=DEFAULT_OPML_FILE)
    assert opml_file is not None

    bot_token = env_first(values, "BOT_TOKEN")
    if not bot_token:
        raise SystemExit("Missing BOT_TOKEN in .env")

    destinations = parse_list(env_first(values, "DESTINATIONS", "DESTINATION"))
    if not destinations:
        raise SystemExit("Missing DESTINATIONS in .env")

    emojis = parse_list(env_first(values, "EMOJIS"))
    topic = env_first(values, "TOPIC")

    app = AppConfig(
        opml_file=opml_file,
        database=env_first(values, "DATABASE", default=DEFAULT_DATABASE) or DEFAULT_DATABASE,
        routes_file=env_first(values, "ROUTES_FILE", default=DEFAULT_ROUTES_FILE) or DEFAULT_ROUTES_FILE,
        obsidian_inbox_dir=env_first(
            values,
            "OBSIDIAN_INBOX_DIR",
            default=DEFAULT_OBSIDIAN_INBOX_DIR,
        )
        or DEFAULT_OBSIDIAN_INBOX_DIR,
        obsidian_date_format=env_first(
            values,
            "OBSIDIAN_DATE_FORMAT",
            default=DEFAULT_OBSIDIAN_DATE_FORMAT,
        )
        or DEFAULT_OBSIDIAN_DATE_FORMAT,
        max_entries_per_feed=int(
            env_first(values, "MAX_ENTRIES_PER_FEED", default=str(DEFAULT_MAX_ENTRIES_PER_FEED))
            or DEFAULT_MAX_ENTRIES_PER_FEED
        ),
        send_on_first_run=parse_bool(env_first(values, "SEND_ON_FIRST_RUN"), default=False),
        fetch_images=parse_bool(env_first(values, "FETCH_IMAGES"), default=True),
        request_timeout=int(env_first(values, "REQUEST_TIMEOUT", default="10") or 10),
        sleep_between_messages=float(env_first(values, "SLEEP_BETWEEN_MESSAGES", default="0.2") or 0.2),
        user_agent=env_first(values, "USER_AGENT", default=DEFAULT_USER_AGENT) or DEFAULT_USER_AGENT,
    )
    telegram = TelegramConfig(
        bot_token=bot_token,
        destinations=destinations,
        topic=int(topic) if topic else None,
        message_template=env_first(values, "MESSAGE_TEMPLATE", default=DEFAULT_MESSAGE_TEMPLATE)
        or DEFAULT_MESSAGE_TEMPLATE,
        button_text=env_first(values, "BUTTON_TEXT"),
        hide_button=parse_bool(env_first(values, "HIDE_BUTTON"), default=False),
        parameters=env_first(values, "PARAMETERS"),
        enable_telegraph=parse_bool(env_first(values, "ENABLE_TELEGRAPH"), default=False),
        telegraph_token=env_first(values, "TELEGRAPH_TOKEN"),
        emojis=emojis,
    )
    return Config(app=app, telegram=telegram, feeds=parse_opml(app.opml_file))


def connect_database(path: str) -> sqlite3.Connection:
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
        """
        CREATE TABLE IF NOT EXISTS history (
            hash TEXT NOT NULL PRIMARY KEY
        )
        """
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
        """
        CREATE TABLE history (
            hash TEXT NOT NULL PRIMARY KEY
        )
        """
    )

    if "hash" in columns:
        conn.execute(
            f"""
            INSERT OR IGNORE INTO history (hash)
            SELECT hash FROM {legacy_name}
            WHERE hash IS NOT NULL AND trim(hash) != ''
            """
        )

    if {"feed_url", "link"}.issubset(columns):
        conn.create_function("rss2tg_feed_hash", 1, lambda value: feed_history_hash(str(value)))
        conn.create_function(
            "rss2tg_entry_hash",
            2,
            lambda feed_url, item_id: entry_history_hash(str(feed_url), str(item_id)),
        )
        conn.execute(
            f"""
            INSERT OR IGNORE INTO history (hash)
            SELECT rss2tg_feed_hash(feed_url)
            FROM {legacy_name}
            WHERE feed_url IS NOT NULL AND trim(feed_url) != ''
            """
        )
        conn.execute(
            f"""
            INSERT OR IGNORE INTO history (hash)
            SELECT rss2tg_entry_hash(feed_url, link)
            FROM {legacy_name}
            WHERE feed_url IS NOT NULL
              AND trim(feed_url) != ''
              AND link IS NOT NULL
              AND trim(link) != ''
            """
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
            """
            SELECT name FROM sqlite_master
            WHERE type = 'table' AND name LIKE 'history_legacy_%'
            """
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
    conn.execute(
        """
        INSERT OR IGNORE INTO history (hash)
        VALUES (?)
        """,
        (value,),
    )
    conn.commit()


def remember_feed(conn: sqlite3.Connection, feed_url: str) -> None:
    remember_hash(conn, feed_history_hash(feed_url))


def remember_entry(conn: sqlite3.Connection, feed_url: str, item_id: str) -> None:
    remember_hash(conn, entry_history_hash(feed_url, item_id))


def load_rules(path: str = "RULES.txt") -> list[tuple[str, str]]:
    rules_path = Path(path)
    if not rules_path.exists():
        return []

    rules: list[tuple[str, str]] = []
    with rules_path.open("r", encoding="utf-8") as fh:
        for raw_line in fh:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if ":" not in line:
                print(f"Skipping malformed rule: {line}")
                continue
            action, value = line.split(":", 1)
            rules.append((action.strip().upper(), value.strip()))
    return rules


def allowed_by_rules(text: str, rules: list[tuple[str, str]]) -> bool:
    if not rules:
        return True

    result = True
    lower_text = text.lower()
    for action, value in rules:
        lower_value = value.lower()
        matched = value == "ALL" or lower_value in lower_text
        if not matched:
            continue
        if action == "DROP":
            result = False
        elif action == "ACCEPT":
            result = True
    return result


def load_routes(config: AppConfig) -> RouteConfig:
    routes_path = Path(config.routes_file)
    raw: dict[str, Any] = {}
    if routes_path.exists():
        loaded = yaml.safe_load(routes_path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            raw = loaded
    else:
        print(f"route config not found, using defaults: {routes_path}")

    obsidian = raw.get("obsidian") if isinstance(raw.get("obsidian"), dict) else {}
    obsidian_inbox_dir = str(
        obsidian.get("inbox_dir") or config.obsidian_inbox_dir or DEFAULT_OBSIDIAN_INBOX_DIR
    )
    obsidian_date_format = str(
        obsidian.get("date_format") or config.obsidian_date_format or DEFAULT_OBSIDIAN_DATE_FORMAT
    )

    tiers_raw = raw.get("tiers") if isinstance(raw.get("tiers"), dict) else {}
    default_tiers = {
        "deep": {"label": "精读", "prefix": "🧠【精读】", "archive_file": "精读.md"},
        "watch": {"label": "重点扫读", "prefix": "👀【重点】", "archive_file": "重点扫读.md"},
        "research": {"label": "科研工具", "prefix": "🧪【科研】", "archive_file": "科研工具.md"},
        "stream": {"label": "背景流", "prefix": "🌊【背景】", "archive_file": None},
        "noise": {"label": "低优先级", "prefix": "🕳️【低优先】", "archive_file": None},
    }
    tiers: dict[str, TierConfig] = {}
    for tier_name, defaults in default_tiers.items():
        data = tiers_raw.get(tier_name) if isinstance(tiers_raw.get(tier_name), dict) else {}
        tiers[tier_name] = TierConfig(
            name=tier_name,
            label=str(data.get("label") or defaults["label"]),
            prefix=str(data.get("prefix") or defaults["prefix"]),
            archive_file=data.get("archive_file", defaults["archive_file"]),
            description=str(data.get("description") or ""),
        )

    feeds_raw = raw.get("feeds") if isinstance(raw.get("feeds"), dict) else {}
    feeds: dict[str, FeedRoute] = {}
    for feed_name, data in feeds_raw.items():
        if not isinstance(data, dict):
            continue
        tier = str(data.get("tier") or "stream")
        feeds[str(feed_name)] = FeedRoute(
            tier=tier if tier in tiers else "stream",
            action=str(data.get("action") or "push"),
            reason=str(data.get("reason") or ""),
        )
    return RouteConfig(
        tiers=tiers,
        feeds=feeds,
        obsidian_inbox_dir=obsidian_inbox_dir,
        obsidian_date_format=obsidian_date_format,
    )


def route_for_feed(feed_cfg: FeedConfig, routes: RouteConfig) -> tuple[FeedRoute, TierConfig]:
    route = routes.feeds.get(feed_cfg.name)
    if route is None:
        route = FeedRoute(tier="stream", action="push", reason="未显式分类，默认背景流。")
    tier = routes.tiers.get(route.tier) or routes.tiers["stream"]
    return route, tier


def action_allows_push(action: str) -> bool:
    return action in {"push", "push_and_archive"}


def action_allows_archive(action: str) -> bool:
    return action in {"archive_only", "push_and_archive"}


def action_drops(action: str) -> bool:
    return action in {"drop", "digest_only"}


def markdown_escape(value: str) -> str:
    return value.replace("\n", " ").strip()


def append_obsidian_entry(topic: dict[str, str], tier: TierConfig, routes: RouteConfig) -> Path | None:
    if not tier.archive_file:
        return None
    date_dir = time.strftime(routes.obsidian_date_format)
    archive_dir = Path(routes.obsidian_inbox_dir) / date_dir
    archive_dir.mkdir(parents=True, exist_ok=True)
    archive_path = archive_dir / tier.archive_file
    if not archive_path.exists():
        archive_path.write_text(
            "---\n"
            f"title: RSS {tier.label}队列 {date_dir}\n"
            "tags:\n"
            "  - rss\n"
            f"  - rss/{tier.name}\n"
            f"date: {date_dir}\n"
            "---\n\n"
            f"# RSS {tier.label}队列｜{date_dir}\n\n",
            encoding="utf-8",
        )
    elif f"  - 链接：{topic['link']}" in archive_path.read_text(encoding="utf-8"):
        return archive_path
    block = (
        f"- [ ] {tier.prefix} {markdown_escape(topic['feed_name'])}｜{markdown_escape(topic['title'])}\n"
        f"  - 链接：{topic['link']}\n"
        f"  - 来源：{markdown_escape(topic['site_name'])}\n"
        f"  - 发布时间：{markdown_escape(topic.get('published', ''))}\n"
        f"  - 分层理由：{markdown_escape(topic.get('route_reason', ''))}\n"
        "  - 初读问题：\n"
        "  - 可沉淀方向：科研判断 / 写作 / 工具系统 / 生活秩序 / 其他\n\n"
    )
    with archive_path.open("a", encoding="utf-8") as fh:
        fh.write(block)
    return archive_path


def append_parameters(link: str, parameters: str | None) -> str:
    if not parameters:
        return link
    separator = "&" if "?" in link else "?"
    return f"{link}{separator}{parameters}"


def clean_summary(summary: str) -> str:
    return re.sub("<[^<]+?>", "", summary or "").strip()


def render_template(template: str, topic: dict[str, str], cfg: TelegramConfig) -> str:
    values = {
        "SITE_NAME": html.escape(topic.get("site_name", "")),
        "FEED_NAME": html.escape(topic.get("feed_name", "")),
        "TITLE": html.escape(topic.get("display_title", topic.get("title", ""))),
        "SUMMARY": html.escape(clean_summary(topic.get("summary", ""))),
        "LINK": append_parameters(topic.get("link", ""), cfg.parameters),
        "EMOJI": html.escape(random.choice(cfg.emojis)) if cfg.emojis else "",
        "TIER": html.escape(topic.get("tier", "")),
        "TIER_LABEL": html.escape(topic.get("tier_label", "")),
        "TIER_PREFIX": html.escape(topic.get("tier_prefix", "")),
        "ACTION": html.escape(topic.get("route_action", "")),
    }

    rendered = template
    for key, value in values.items():
        rendered = rendered.replace(f"{{{key}}}", value)
    return rendered.replace("\\n", "\n")


def get_image_url(url: str, cfg: AppConfig) -> str | None:
    if not cfg.fetch_images:
        return None

    try:
        response = requests.get(
            url,
            headers={"User-Agent": cfg.user_agent},
            timeout=cfg.request_timeout,
        )
        response.raise_for_status()
        soup = BeautifulSoup(response.content, "html.parser")
        image = soup.find("meta", {"property": "og:image"})
        if not image:
            return None
        content = image.get("content")
        return str(content) if content else None
    except requests.RequestException as exc:
        print(f"Image lookup failed for {url}: {exc}")
        return None
    except (AttributeError, TypeError):
        return None


def create_telegraph_post(topic: dict[str, str], token: str) -> str:
    import telegraph

    telegraph_auth = telegraph.Telegraph(access_token=token)
    summary = html.escape(clean_summary(topic.get("summary", "")))
    original_link = html.escape(topic["link"])
    site_name = html.escape(topic["site_name"])
    response = telegraph_auth.create_page(
        topic["title"],
        html_content=(
            f"<p>{summary}</p>"
            f'<p><a href="{original_link}">Original ({site_name})</a></p>'
        ),
        author_name=topic["site_name"],
    )
    return response["url"]


def button_markup(button_text: str | None, topic: dict[str, str], cfg: TelegramConfig) -> Any:
    if not button_text:
        return None
    markup = types.InlineKeyboardMarkup()
    markup.row(types.InlineKeyboardButton(render_template(button_text, topic, cfg), url=topic["link"]))
    return markup


def send_message(bot: telebot.TeleBot, topic: dict[str, str], config: Config) -> bool:
    message = render_template(config.telegram.message_template, topic, config.telegram)
    if config.telegram.use_telegraph:
        try:
            token = config.telegram.telegraph_token
            assert token is not None
            iv_link = create_telegraph_post(topic, token)
            message = f'<a href="{html.escape(iv_link)}"></a>{message}'
        except Exception as exc:
            print(f"Telegraph page creation failed, falling back to normal message: {exc}")
            traceback.print_exc()

    markup = None
    if not config.telegram.hide_button and not config.telegram.use_telegraph:
        markup = button_markup(config.telegram.button_text, topic, config.telegram)

    for destination in config.telegram.destinations:
        if topic.get("photo") and not config.telegram.use_telegraph:
            try:
                response = requests.get(
                    topic["photo"],
                    headers={"User-Agent": config.app.user_agent},
                    timeout=config.app.request_timeout,
                )
                response.raise_for_status()
                image_file = BytesIO(response.content)
                image_file.name = "image"
                bot.send_photo(
                    destination,
                    image_file,
                    caption=message,
                    parse_mode="HTML",
                    reply_markup=markup,
                    message_thread_id=config.telegram.topic,
                )
                continue
            except Exception as exc:
                print(f"Photo send failed, falling back to text: {exc}")
                traceback.print_exc()

        bot.send_message(
            destination,
            message,
            parse_mode="HTML",
            reply_markup=markup,
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


def build_topic(
    feed_cfg: FeedConfig,
    feed: Any,
    entry: Any,
    config: Config,
    tier: TierConfig,
    route: FeedRoute,
    include_image: bool = True,
) -> dict[str, str]:
    link = entry_link(entry) or entry_id(feed_cfg.url, entry) or feed_cfg.url
    title = str(getattr(entry, "title", "Untitled")).strip()
    return {
        "feed_name": feed_cfg.name,
        "site_name": feed_site_name(feed, feed_cfg.url),
        "title": title,
        "display_title": f"{tier.prefix} {title}",
        "summary": str(getattr(entry, "summary", "")),
        "link": link,
        "published": str(getattr(entry, "published", getattr(entry, "updated", ""))),
        "photo": (get_image_url(link, config.app) if include_image else None) or "",
        "tier": tier.name,
        "tier_label": tier.label,
        "tier_prefix": tier.prefix,
        "route_action": route.action,
        "route_reason": route.reason,
    }


def process_feed(
    conn: sqlite3.Connection,
    bot: telebot.TeleBot | None,
    feed_cfg: FeedConfig,
    config: Config,
    rules: list[tuple[str, str]],
    routes: RouteConfig,
    options: ProcessingOptions,
) -> None:
    route, tier = route_for_feed(feed_cfg, routes)
    print(
        f"checking: {feed_cfg.name} <{feed_cfg.url}> "
        f"tier={tier.name}/{tier.label} action={route.action}"
    )
    if action_drops(route.action):
        print(f"routed away: {feed_cfg.name} action={route.action} reason={route.reason}")
        return

    parsed = urlparse(feed_cfg.url)
    if parsed.scheme not in ("http", "https"):
        print(f"skipping unsupported scheme ({parsed.scheme}): {feed_cfg.url}")
        return
    feed = feedparser.parse(feed_cfg.url, request_headers={"User-Agent": config.app.user_agent})
    if getattr(feed, "bozo", False):
        print(f"feed parse warning for {feed_cfg.url}: {getattr(feed, 'bozo_exception', '')}")
    if not getattr(feed, "entries", None):
        print(f"no entries: {feed_cfg.url}")
        return

    feed_has_history = has_history(conn, feed_cfg.url)
    entry_limit = options.limit_entries or config.app.max_entries_per_feed
    entries = list(reversed(feed.entries[:entry_limit]))

    if not feed_has_history and not config.app.send_on_first_run and not options.force_first_run:
        print(f"bootstrap only: {feed_cfg.name}")
        if not options.dry_run and not options.no_history:
            for entry in entries:
                item_id = entry_id(feed_cfg.url, entry)
                if item_id:
                    remember_entry(conn, feed_cfg.url, item_id)
            remember_feed(conn, feed_cfg.url)
        return

    for entry in entries:
        item_id = entry_id(feed_cfg.url, entry)
        if not item_id or seen(conn, feed_cfg.url, item_id):
            continue

        topic = build_topic(
            feed_cfg,
            feed,
            entry,
            config,
            tier=tier,
            route=route,
            include_image=not options.dry_run,
        )
        if not allowed_by_rules(str(topic), rules):
            print(f"filtered: {topic['title']}")
            if not options.dry_run and not options.no_history:
                remember_entry(conn, feed_cfg.url, item_id)
            continue

        archive_path = None
        if action_allows_archive(route.action) and not options.no_archive:
            if options.dry_run:
                archive_path = Path(routes.obsidian_inbox_dir) / time.strftime(routes.obsidian_date_format) / (tier.archive_file or "")
            else:
                archive_path = append_obsidian_entry(topic, tier, routes)

        if action_allows_push(route.action):
            if options.dry_run:
                rendered = render_template(config.telegram.message_template, topic, config.telegram)
                print(f"dry-run push: {rendered.splitlines()[0]} -> archive={archive_path or '-'}")
            elif options.no_send:
                rendered = render_template(config.telegram.message_template, topic, config.telegram)
                print(f"no-send push skipped: {rendered.splitlines()[0]} -> archive={archive_path or '-'}")
            else:
                if bot is None:
                    raise RuntimeError("Telegram bot is not initialized")
                send_message(bot, topic, config)
        elif archive_path:
            print(f"archived: {topic['title']} -> {archive_path}")

        if not options.dry_run and not options.no_history:
            remember_entry(conn, feed_cfg.url, item_id)

    if not feed_has_history and not options.dry_run and not options.no_history:
        remember_feed(conn, feed_cfg.url)


def parse_args() -> ProcessingOptions:
    parser = argparse.ArgumentParser(description="Send OPML RSS entries to Telegram with route tiers.")
    parser.add_argument("--dry-run", action="store_true", help="Do not send Telegram messages or write history/archive files.")
    parser.add_argument("--no-send", action="store_true", help="Do not send Telegram messages, but still allow archive/history writes unless disabled.")
    parser.add_argument("--no-history", action="store_true", help="Do not update the SQLite history database.")
    parser.add_argument("--force-first-run", action="store_true", help="Process entries even when a feed has no history yet.")
    parser.add_argument("--limit-feeds", type=int, default=None, help="Process only the first N feeds from OPML.")
    parser.add_argument("--limit-entries", type=int, default=None, help="Inspect only the first N entries per feed.")
    parser.add_argument("--only-feed", action="append", default=None, help="Process only a named feed. Can be provided multiple times.")
    parser.add_argument("--no-archive", action="store_true", help="Skip Obsidian archive writes.")
    args = parser.parse_args()
    return ProcessingOptions(
        dry_run=args.dry_run,
        no_send=args.no_send,
        no_history=args.no_history,
        force_first_run=args.force_first_run,
        limit_feeds=args.limit_feeds,
        limit_entries=args.limit_entries,
        no_archive=args.no_archive,
        only_feeds=args.only_feed,
    )


def print_route_summary(config: Config, routes: RouteConfig) -> None:
    counts: dict[str, int] = {}
    actions: dict[str, int] = {}
    for feed_cfg in config.feeds:
        route, tier = route_for_feed(feed_cfg, routes)
        counts[tier.name] = counts.get(tier.name, 0) + 1
        actions[route.action] = actions.get(route.action, 0) + 1
    print("route tier summary: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    print("route action summary: " + ", ".join(f"{k}={v}" for k, v in sorted(actions.items())))


def main() -> None:
    options = parse_args()
    config = load_config()
    routes = load_routes(config.app)
    rules = load_rules()
    bot = None if options.dry_run or options.no_send else telebot.TeleBot(config.telegram.bot_token)

    if config.telegram.telegraph_token and not config.telegram.enable_telegraph:
        print("Telegraph token is configured but ENABLE_TELEGRAPH is false; using normal messages")
    elif config.telegram.enable_telegraph and not config.telegram.telegraph_token:
        print("ENABLE_TELEGRAPH is true but TELEGRAPH_TOKEN is missing; using normal messages")

    print(f"loaded feeds: {len(config.feeds)} from {config.app.opml_file}")
    print(f"loaded routes: {len(routes.feeds)} from {config.app.routes_file}")
    print(f"obsidian inbox: {routes.obsidian_inbox_dir}/{time.strftime(routes.obsidian_date_format)}")
    print_route_summary(config, routes)
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
        for feed_cfg in feeds:
            try:
                process_feed(conn, bot, feed_cfg, config, rules, routes, options)
            except Exception as exc:
                print(f"failed: {feed_cfg.name} <{feed_cfg.url}>: {exc}")
                traceback.print_exc()


if __name__ == "__main__":
    main()
