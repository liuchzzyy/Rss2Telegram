# Rss2Telegram

Send new RSS/Atom entries from an OPML subscription file to Telegram.

This version is configured by two local files:

- `Subscriptions.opml`: feed subscriptions
- `.env`: personal Telegram and runtime settings

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
MAX_ENTRIES_PER_FEED=100
SEND_ON_FIRST_RUN=false
FETCH_IMAGES=true
REQUEST_TIMEOUT=10
SLEEP_BETWEEN_MESSAGES=0.2
```

`DESTINATIONS` supports comma-separated or semicolon-separated chat IDs.

`ENABLE_TELEGRAPH=false` is the default. Telegraph is used only when this is set to `true` and `TELEGRAPH_TOKEN` is also set.

`SEND_ON_FIRST_RUN=false` means the first run records current feed entries but does not send them. This prevents old RSS items from flooding Telegram when the database is new.

Supported template variables:

- `{SITE_NAME}`
- `{FEED_NAME}`
- `{TITLE}`
- `{SUMMARY}`
- `{LINK}`
- `{EMOJI}`

## Feed List

Maintain feeds in `Subscriptions.opml`. The script reads every OPML outline node with an `xmlUrl` attribute and keeps the order from the file.

## GitHub Actions

The workflow in `.github/workflows/cron.yml` runs at `06:00`, `10:00`, `14:00`, `17:00`, and `20:00` in Asia/Shanghai.

The SQLite history database is saved as a workflow artifact and restored on the next run.

If this repository is public, do not commit a real `.env` with your bot token. Keep it local, or use a private repository.

## Local Run

```powershell
uv sync
uv run rss2telegram
```

## Filters

Optional `RULES.txt` rules are supported:

```text
ACCEPT:ALL
DROP:keyword
```

Rules are evaluated in order. `ACCEPT:ALL` allows all entries by default, then later `DROP:*` rules can block matching entries.
