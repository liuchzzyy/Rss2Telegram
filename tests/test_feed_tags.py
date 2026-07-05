from pathlib import Path
from types import SimpleNamespace

from rss2telegram import FeedConfig, build_topic, feed_tags, journal_tag_name, parse_opml


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

    topic = build_topic(feed_cfg, feed, entry, SimpleNamespace(), include_image=False)

    assert topic["display_title"] == "新文章"
    assert topic["tags"] == "#RSS #生活 #理论派"
