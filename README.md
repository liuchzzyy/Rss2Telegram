# Rss2Telegram

Send new RSS/Atom entries from an OPML subscription file to Telegram.

This version is configured by three local files:

- `Subscriptions.opml`: feed subscriptions
- `.env`: personal Telegram and runtime settings
- `feed_routes.yaml`: source-level routing, priority tiers, and Obsidian archive settings

The script does not read personal settings from system environment variables.

## uv Project

Dependencies are managed by `uv` through `pyproject.toml` and `uv.lock`.

```powershell
uv sync
uv run rss2telegram
```

## Personal Config

Edit `.env`:

```text
BOT_TOKEN=123456:telegram-token
DESTINATIONS=-1001234567890
TOPIC=none

MESSAGE_TEMPLATE={EMOJI} <b>{TITLE}</b>\n{LINK}
BUTTON_TEXT={SITE_NAME}
HIDE_BUTTON=false
PARAMETERS=none
ENABLE_TELEGRAPH=false
TELEGRAPH_TOKEN=none
EMOJIS=🗞️,📰,📡,📬,📌,🔖,🔗,📝,📋,📚,💡,⚙️,🧠,🚀,✨,🌐,📊,🎧,🎬,🧪

OPML_FILE=Subscriptions.opml
DATABASE=rss2telegram.db
ROUTES_FILE=feed_routes.yaml
OBSIDIAN_INBOX_DIR=F:/ChengL1u/01_收件箱/0102_RSS
OBSIDIAN_DATE_FORMAT=%Y%m%d
MAX_ENTRIES_PER_FEED=100
SEND_ON_FIRST_RUN=false
FETCH_IMAGES=true
REQUEST_TIMEOUT=10
SLEEP_BETWEEN_MESSAGES=0.2
```

`DESTINATIONS` supports comma-separated or semicolon-separated chat IDs.

`ENABLE_TELEGRAPH=false` is the default. Telegraph is used only when this is set to `true` and `TELEGRAPH_TOKEN` is also set.

`SEND_ON_FIRST_RUN=false` means the first run records current feed entries but does not send them. This prevents old RSS items from flooding Telegram when the database is new.

The SQLite history database stores only SHA-256 hashes for feed and entry identifiers. It is used only for incremental comparison and does not keep RSS entry titles, links, summaries, or publish dates.

Supported template variables:

- `{SITE_NAME}`
- `{FEED_NAME}`
- `{TITLE}`
- `{SUMMARY}`
- `{LINK}`
- `{EMOJI}`
- `{TIER}`
- `{TIER_LABEL}`
- `{TIER_PREFIX}`
- `{ACTION}`
- `{TAGS}`

If `MESSAGE_TEMPLATE` does not include `{TAGS}`, the script automatically appends an empty line plus `{TAGS}` at the end of each Telegram message. This makes Telegram search/filter easier without requiring every existing secret template to be changed.

Default Telegram tags are generated from the route tier and feed name, for example:

```text
#RSS #精读 #理论派
#RSS #重点扫读 #阮一峰
#RSS #科研工具 #Zotero
#RSS #低优先级 #IT之家
```

## Feed List

Maintain feeds in `Subscriptions.opml`. The script reads every OPML outline node with an `xmlUrl` attribute and keeps the order from the file.

## GitHub Actions

The workflow in `.github/workflows/cron.yml` runs at `06:00`, `10:00`, `14:00`, `17:00`, and `20:00` in Asia/Shanghai.

The SQLite hash-only history database is saved as a workflow artifact and restored on the next run.

If this repository is public, do not commit a real `.env` with your bot token. Keep it local, or use a private repository.

## Local Run

```powershell
uv sync
uv run rss2telegram
```

Non-sending verification run:

```powershell
uv run rss2telegram --dry-run --force-first-run --limit-entries 1
```

`--dry-run` does not send Telegram messages, does not update the SQLite history database, and does not write Obsidian archive files. It prints the planned Telegram title and planned archive path.

Obsidian archive smoke test without sending Telegram or updating history:

```powershell
uv run rss2telegram --no-send --no-history --force-first-run --only-feed 理论派 --limit-entries 1
```

Useful safety flags:

- `--no-send`: skip Telegram sending while still allowing archive writes.
- `--no-history`: use an in-memory history database and do not update `rss2telegram.db`.
- `--only-feed <name>`: process one named feed; repeat it to process multiple named feeds.
- `--no-archive`: skip Obsidian archive writes.

## RSS Routing and Obsidian Archive

`feed_routes.yaml` classifies every OPML source into a tier and action.

Default tiers:

- `deep`: 精读，高思想密度或必须认真学习的来源。
- `watch`: 重点扫读，值得注意但不逐条深读。
- `research`: 科研工具，与 Zotero、Obsidian、Logseq、科研软件、数据处理直接相关。
- `stream`: 背景流，保持感知即可。
- `noise`: 低优先级，高频或容易打断的内容。

Supported actions:

- `push_and_archive`: Telegram 推送，同时追加到 Obsidian 日期目录。
- `push`: 仅 Telegram 推送。
- `archive_only`: 仅追加到 Obsidian，不推送。
- `digest_only`: 当前不即时推送，保留给后续 digest 流程。
- `drop`: 跳过。

Obsidian archive root defaults to:

```text
F:/ChengL1u/01_收件箱/0102_RSS/{yyyymmdd}/
```

For example, entries on 2026-07-02 are planned under:

```text
F:/ChengL1u/01_收件箱/0102_RSS/20260702/精读.md
F:/ChengL1u/01_收件箱/0102_RSS/20260702/重点扫读.md
F:/ChengL1u/01_收件箱/0102_RSS/20260702/科研工具.md
```

## Filters

Optional `RULES.txt` rules are supported:

```text
ACCEPT:ALL
DROP:keyword
```

Rules are evaluated in order. `ACCEPT:ALL` allows all entries by default, then later `DROP:*` rules can block matching entries.
