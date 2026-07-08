from pathlib import Path
from types import SimpleNamespace

from rss2tg import FeedConfig, build_topic, extract_doi, feed_tags, journal_tag_name, parse_opml, render_message
from rss2tg.runner import FeedRunContext, fetch_feed_content, process_feed


def test_feed_tags_use_life_feed_name_and_journal_abbreviation(tmp_path: Path) -> None:
    opml_path = tmp_path / "subscriptions.opml"
    opml_path.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<opml version="1.1">
  <body>
    <outline text="日常">
      <outline text="理论派" type="rss" xmlUrl="https://example.com/life.xml"/>
    </outline>
    <outline text="📚 学术期刊">
      <outline text="chemistry">
        <outline text="ACS">
          <outline text="Journal of the American Chemical Society" type="rss" xmlUrl="https://example.com/jacs.xml" tagName="JACS"/>
        </outline>
      </outline>
    </outline>
  </body>
</opml>
""",
        encoding="utf-8",
    )

    feeds = parse_opml(str(opml_path))

    assert feed_tags(feeds[0]) == "#RSS #生活 #理论派"
    assert feed_tags(feeds[1]) == "#RSS #期刊 #JACS"


def test_journal_tag_name_uses_recognizable_short_names_for_common_collisions() -> None:
    assert journal_tag_name("Nature Chemistry") == "NatChem"
    assert journal_tag_name("Nature Communications") == "NatCommun"
    assert journal_tag_name("ACS Catalysis") == "ACSCatal"
    assert journal_tag_name("Analytical Chemistry") == "AC"


def test_build_topic_uses_plain_title_without_extra_configuration() -> None:
    feed_cfg = FeedConfig(name="理论派", url="https://example.com/feed.xml")
    feed = SimpleNamespace(feed=SimpleNamespace(title="理论派"))
    entry = SimpleNamespace(
        title="新文章",
        link="https://example.com/post",
        summary="摘要",
        published="2026-07-05",
    )

    topic = build_topic(feed_cfg, feed, entry)

    assert topic["display_title"] == "新文章"
    assert topic["tags"] == "#RSS #生活 #理论派"
    assert topic["doi"] == ""


def test_build_topic_supports_dict_entries_from_feedparser() -> None:
    feed_cfg = FeedConfig(name="理论派", url="https://example.com/feed.xml")
    feed = SimpleNamespace(feed=SimpleNamespace(title="理论派"))
    entry = {
        "title": "字典文章",
        "links": [{"rel": "alternate"}, {"href": "https://example.com/dict-post"}],
        "summary": "摘要",
        "updated": "2026-07-06",
    }

    topic = build_topic(feed_cfg, feed, entry)

    assert topic["display_title"] == "字典文章"
    assert topic["link"] == "https://example.com/dict-post"
    assert topic["published"] == "2026-07-06"


def test_build_topic_uses_dict_entry_id_when_link_is_missing() -> None:
    feed_cfg = FeedConfig(name="理论派", url="https://example.com/feed.xml")
    feed = SimpleNamespace(feed=SimpleNamespace(title="理论派"))
    entry = {
        "title": "无链接文章",
        "id": "https://example.com/dict-id",
    }

    topic = build_topic(feed_cfg, feed, entry)

    assert topic["link"] == "https://example.com/dict-id"


def test_extract_doi_finds_and_normalizes_common_journal_fields() -> None:
    entry = SimpleNamespace(
        title="Paper",
        prism_doi="https://doi.org/10.1021/acsenergylett.6b00000.",
        summary="ignored",
    )

    assert extract_doi(entry) == "10.1021/acsenergylett.6b00000"


def test_extract_doi_falls_back_to_summary_content_and_link() -> None:
    entry = SimpleNamespace(
        title="Paper",
        summary="Read at DOI: 10.1038/s41560-026-00000-1.",
        content=[{"value": "content without doi"}],
        link="https://example.com/paper",
    )

    assert extract_doi(entry) == "10.1038/s41560-026-00000-1"


def test_render_message_includes_empty_doi_line() -> None:
    topic = {
        "display_title": "新文章",
        "link": "https://example.com/post",
        "doi": "",
        "tags": "#RSS #生活 #理论派",
    }

    message = render_message(topic)

    assert message == "<b>新文章</b>\nhttps://example.com/post\nDOI: \n\n#RSS #生活 #理论派"


def test_journal_topic_includes_extracted_doi_in_message() -> None:
    feed_cfg = FeedConfig(
        name="Journal of the American Chemical Society",
        url="https://example.com/jacs.xml",
        feed_kind="journal",
        tag_name="JACS",
    )
    feed = SimpleNamespace(feed=SimpleNamespace(title="JACS"))
    entry = SimpleNamespace(
        title="论文",
        link="https://example.com/paper",
        summary="摘要 DOI: 10.1021/jacs.6c00000",
        published="2026-07-05",
    )

    topic = build_topic(feed_cfg, feed, entry)

    assert topic["doi"] == "10.1021/jacs.6c00000"
    assert render_message(topic) == (
        "<b>论文</b>\n"
        "https://example.com/paper\n"
        "DOI: 10.1021/jacs.6c00000\n\n"
        "#RSS #期刊 #JACS"
    )


def test_life_topic_leaves_doi_blank_even_if_entry_contains_doi() -> None:
    feed_cfg = FeedConfig(name="理论派", url="https://example.com/feed.xml", feed_kind="life")
    feed = SimpleNamespace(feed=SimpleNamespace(title="理论派"))
    entry = SimpleNamespace(
        title="生活文章",
        link="https://example.com/post",
        summary="DOI: 10.1234/ignored",
        published="2026-07-05",
    )

    topic = build_topic(feed_cfg, feed, entry)

    assert topic["doi"] == ""


def test_process_feed_fetches_content_before_parsing(monkeypatch) -> None:
    feed_cfg = FeedConfig(name="理论派", url="https://example.com/feed.xml")
    parsed_feed = SimpleNamespace(
        feed=SimpleNamespace(title="理论派"),
        entries=[
            SimpleNamespace(
                title="新文章",
                link="https://example.com/post",
                summary="摘要",
                published="2026-07-05",
            )
        ],
        bozo=False,
    )
    calls = {}

    def fake_fetch_feed_content(feed, context):
        calls["feed_url"] = feed.url
        return b"<rss></rss>"

    def fake_parse(feed_content):
        calls["feed_content"] = feed_content
        return parsed_feed

    monkeypatch.setattr("rss2tg.runner.fetch_feed_content", fake_fetch_feed_content)
    monkeypatch.setattr("rss2tg.runner.feedparser.parse", fake_parse)
    monkeypatch.setattr("rss2tg.runner.has_history", lambda conn, feed_url: True)
    monkeypatch.setattr("rss2tg.runner.seen", lambda conn, feed_url, item_id: False)
    monkeypatch.setattr("rss2tg.runner.remember_entry", lambda conn, feed_url, item_id: None)

    context = FeedRunContext(
        conn=SimpleNamespace(),
        bot=None,
        config=SimpleNamespace(
            app=SimpleNamespace(
                user_agent="rss2telegram-test",
                max_entries_per_feed=100,
                send_on_first_run=False,
            )
        ),
        options=SimpleNamespace(
            limit_entries=1,
            force_first_run=False,
            dry_run=True,
            no_send=False,
            no_history=True,
        ),
    )

    process_feed(context, feed_cfg)

    assert calls == {
        "feed_url": "https://example.com/feed.xml",
        "feed_content": b"<rss></rss>",
    }


def test_fetch_feed_content_uses_httpx_client_with_user_agent(monkeypatch) -> None:
    feed_cfg = FeedConfig(name="理论派", url="https://example.com/feed.xml")
    calls = {}

    class FakeResponse:
        content = b"<rss></rss>"

        def raise_for_status(self) -> None:
            calls["raised"] = True

    class FakeClient:
        def __init__(self, *, headers, timeout, follow_redirects):
            calls["headers"] = headers
            calls["timeout"] = timeout
            calls["follow_redirects"] = follow_redirects

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def get(self, url):
            calls["url"] = url
            return FakeResponse()

    monkeypatch.setattr("rss2tg.runner.httpx.Client", FakeClient)
    context = FeedRunContext(
        conn=SimpleNamespace(),
        bot=None,
        config=SimpleNamespace(app=SimpleNamespace(user_agent="rss2telegram-test")),
        options=SimpleNamespace(),
    )

    content = fetch_feed_content(feed_cfg, context)

    assert content == b"<rss></rss>"
    assert calls["headers"] == {"user-agent": "rss2telegram-test"}
    assert calls["follow_redirects"] is True
    assert calls["url"] == "https://example.com/feed.xml"
    assert calls["raised"] is True
