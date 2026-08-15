from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal, assert_never

DEFAULT_ENV_FILE = ".env"
DEFAULT_OPML_FILE = "Subscriptions.opml"
DEFAULT_DATABASE = "rss2telegram.db"
DEFAULT_MAX_ENTRIES_PER_FEED = 100
DEFAULT_USER_AGENT = "rss2telegram (+https://github.com/liuchzzyy/Rss2Telegram)"
EMPTY_CONFIG_VALUES: Final = {"", "none", "null", "nil", "-"}
JOURNAL_GROUP_TITLE: Final = "📚 学术期刊"
LIFE_TAG_LABEL: Final = "生活"
JOURNAL_TAG_LABEL: Final = "期刊"
JOURNAL_STOPWORDS: Final = {"a", "an", "and", "for", "in", "of", "on", "part", "the"}
JOURNAL_TAG_OVERRIDES: Final = {
    "Journal of the American Chemical Society": "JACS",
    "The Journal of Physical Chemistry A": "JPCA",
    "The Journal of Physical Chemistry B": "JPCB",
    "The Journal of Physical Chemistry C": "JPCC",
    "The Journal of Physical Chemistry Letters": "JPCL",
    "Physical Review Letters": "PRL",
    "Reviews of Modern Physics": "RMP",
}
JOURNAL_WORD_TAGS: Final = {
    "acs": "ACS",
    "advanced": "Adv",
    "applied": "Appl",
    "catalysis": "Catal",
    "cell": "Cell",
    "chem": "Chem",
    "chemical": "Chem",
    "chemistry": "Chem",
    "communications": "Commun",
    "energy": "Energy",
    "letters": "Lett",
    "macro": "Macro",
    "materials": "Mater",
    "methods": "Methods",
    "nano": "Nano",
    "nanotechnology": "Nano",
    "nature": "Nat",
    "networks": "Net",
    "neural": "Neural",
    "physics": "Phys",
    "protocols": "Protoc",
    "recognition": "Recog",
    "reports": "Rep",
    "review": "Rev",
    "reviews": "Rev",
    "science": "Sci",
    "spectroscopy": "Spectrosc",
}
JOURNAL_WORD_TAG_PREFIXES: Final = {
    "acs",
    "advanced",
    "applied",
    "cell",
    "chem",
    "chemical",
    "chemistry",
    "communications",
    "nano",
    "nature",
    "neural",
    "pattern",
}
FeedKind = Literal["life", "journal"]


@dataclass(frozen=True, slots=True)
class FeedConfig:
    name: str
    url: str
    feed_kind: FeedKind = "life"
    tag_name: str | None = None


@dataclass(frozen=True, slots=True)
class AppConfig:
    opml_file: str
    database: str
    max_entries_per_feed: int
    send_on_first_run: bool
    sleep_between_messages: float
    user_agent: str


@dataclass(frozen=True, slots=True)
class TelegramConfig:
    bot_token: str
    destinations: list[str]
    topic: int | None


@dataclass(frozen=True, slots=True)
class Config:
    app: AppConfig
    telegram: TelegramConfig
    feeds: list[FeedConfig]


@dataclass(frozen=True, slots=True)
class ProcessingOptions:
    dry_run: bool = False
    no_send: bool = False
    no_history: bool = False
    force_first_run: bool = False
    limit_feeds: int | None = None
    limit_entries: int | None = None
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


def parse_bool(value: str | bool | None, default: bool = False) -> bool:
    if value in (None, ""):
        return default
    if isinstance(value, bool):
        return value
    return value.strip().lower() in {"1", "true", "yes", "on"}


def parse_list(value: str | None) -> list[str]:
    if not value or value.strip().lower() in EMPTY_CONFIG_VALUES:
        return []
    return [item.strip() for item in re.split(r"[,;]", value) if item.strip()]


def parse_int_env(value: str | None, name: str, default: int) -> int:
    if not value:
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise SystemExit(f"Invalid integer value for {name}: {value!r}") from exc


def parse_float_env(value: str | None, name: str, default: float) -> float:
    if not value:
        return default
    try:
        return float(value)
    except ValueError as exc:
        raise SystemExit(f"Invalid numeric value for {name}: {value!r}") from exc


def parse_optional_int(value: str | None, name: str) -> int | None:
    if not value:
        return None
    try:
        return int(value)
    except ValueError as exc:
        raise SystemExit(f"Invalid integer value for {name}: {value!r}") from exc


def outline_name(node: ET.Element) -> str:
    return (node.attrib.get("text") or node.attrib.get("title") or "").strip()


def tag_slug(value: str) -> str:
    return re.sub(r"\W+", "", value, flags=re.UNICODE)


def journal_tag_name(feed_name: str) -> str:
    override = JOURNAL_TAG_OVERRIDES.get(feed_name)
    if override:
        return override
    tokens = re.findall(r"[A-Za-z0-9]+", feed_name)
    if len(tokens) <= 1:
        return tag_slug(feed_name)
    if tokens[0].lower() == "arxiv":
        return tag_slug("arXiv" + "".join(tokens[1:]))
    if tokens[0].lower() in JOURNAL_WORD_TAG_PREFIXES:
        return "".join(
            JOURNAL_WORD_TAGS.get(token.lower(), token[:1].upper() + token[1:])
            for token in tokens
            if token.lower() not in JOURNAL_STOPWORDS
        )
    initials = "".join(token[0] for token in tokens if token.lower() not in JOURNAL_STOPWORDS)
    return initials or tag_slug(feed_name)


def parse_opml(path: str) -> list[FeedConfig]:
    opml_path = Path(path)
    if not opml_path.exists():
        raise SystemExit(f"Missing OPML feed file: {opml_path}")

    root = ET.parse(opml_path).getroot()
    feeds: list[FeedConfig] = []
    seen_urls: set[str] = set()

    def collect(node: ET.Element, feed_kind: FeedKind) -> None:
        current_kind: FeedKind = "journal" if outline_name(node) == JOURNAL_GROUP_TITLE else feed_kind
        url = node.attrib.get("xmlUrl")
        if url:
            url = url.strip()
            if url and url not in seen_urls:
                seen_urls.add(url)
                name = outline_name(node) or url
                tag_name = node.attrib.get("tagName") or node.attrib.get("journalAbbr")
                feeds.append(
                    FeedConfig(
                        name=name,
                        url=url,
                        feed_kind=current_kind,
                        tag_name=(tag_name.strip() if tag_name else None),
                    )
                )
        for child in node:
            collect(child, current_kind)

    collect(root, "life")

    if not feeds:
        raise SystemExit(f"No feeds found in OPML file: {opml_path}")
    return feeds


def feed_tags(feed_cfg: FeedConfig) -> str:
    match feed_cfg.feed_kind:
        case "life":
            kind_tag = LIFE_TAG_LABEL
            source_tag = feed_cfg.tag_name or feed_cfg.name
        case "journal":
            kind_tag = JOURNAL_TAG_LABEL
            source_tag = feed_cfg.tag_name or journal_tag_name(feed_cfg.name)
        case unreachable:
            assert_never(unreachable)
    tags = ["RSS", kind_tag, source_tag]
    seen: set[str] = set()
    cleaned: list[str] = []
    for tag in tags:
        slug = tag_slug(tag)
        if not slug or slug in seen:
            continue
        seen.add(slug)
        cleaned.append(f"#{slug}")
    return " ".join(cleaned)


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

    topic = env_first(values, "TOPIC")

    app = AppConfig(
        opml_file=opml_file,
        database=env_first(values, "DATABASE", default=DEFAULT_DATABASE) or DEFAULT_DATABASE,
        max_entries_per_feed=parse_int_env(
            env_first(values, "MAX_ENTRIES_PER_FEED"),
            "MAX_ENTRIES_PER_FEED",
            DEFAULT_MAX_ENTRIES_PER_FEED,
        ),
        send_on_first_run=parse_bool(env_first(values, "SEND_ON_FIRST_RUN"), default=False),
        sleep_between_messages=parse_float_env(
            env_first(values, "SLEEP_BETWEEN_MESSAGES"),
            "SLEEP_BETWEEN_MESSAGES",
            0.2,
        ),
        user_agent=env_first(values, "USER_AGENT", default=DEFAULT_USER_AGENT) or DEFAULT_USER_AGENT,
    )

    telegram = TelegramConfig(
        bot_token=bot_token,
        destinations=destinations,
        topic=parse_optional_int(topic, "TOPIC"),
    )
    return Config(app=app, telegram=telegram, feeds=parse_opml(app.opml_file))
