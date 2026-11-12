"""rank 层：增速/争议度/大号权重打分、Top1+Top3 选择、快照差分。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from src.common.models import Metrics
from src.rank.scorer import RankConfig, rank_posts, score_post
from src.rank.snapshots import (
    PostSnapshot,
    hours_between,
    load_snapshots,
    prune_snapshots,
    save_snapshots,
)
from tests.conftest import FIXED_NOW, SEED_URL, make_post


def test_fallback_growth_uses_age_when_no_snapshot(sample_post):
    breakdown = score_post(sample_post, now=FIXED_NOW)
    assert breakdown.growth_basis == "fallback_avg"
    # 发布于 2026-11-11 02:02:48Z，FIXED_NOW 为 06:00Z → 约 3.95 小时
    assert 0 < breakdown.growth_per_hour < sample_post.metrics.engagement
    assert 0 <= breakdown.total <= 100


def test_snapshot_diff_computes_real_velocity(sample_post):
    earlier = PostSnapshot(
        key=sample_post.key,
        url=sample_post.url,
        author=sample_post.author,
        captured_at=FIXED_NOW - timedelta(hours=2),
        metrics=Metrics(likes=100, replies=20, reposts=5, quotes=2, views=10000),
    )
    breakdown = score_post(sample_post, snapshot=earlier, now=FIXED_NOW)
    assert breakdown.growth_basis == "snapshot_diff"
    expected_delta = (sample_post.metrics.engagement - earlier.metrics.engagement) / 2
    assert abs(breakdown.growth_per_hour - expected_delta) < 0.01


def test_controversy_prefers_replies_over_likes():
    controversial = make_post(likes=100, replies=80, quotes=40)
    agreeable = make_post(likes=4000, replies=2, quotes=0)
    cfg = RankConfig()
    contro_a = score_post(controversial, now=FIXED_NOW, cfg=cfg)
    contro_b = score_post(agreeable, now=FIXED_NOW, cfg=cfg)
    assert contro_a.controversy > contro_b.controversy


def test_influencer_weight_uses_followers():
    small = make_post(followers=50)
    big = make_post(post_id="2", url="https://x.com/big/status/2", followers=2_000_000)
    assert score_post(big, now=FIXED_NOW).influencer > score_post(small, now=FIXED_NOW).influencer


def test_rank_returns_top1_and_three_alternatives():
    posts = [
        make_post(post_id=str(i), url=f"https://x.com/a/status/{i}", views=1000 * i, likes=10 * i)
        for i in range(1, 7)
    ]
    ranked = rank_posts(posts, now=FIXED_NOW)
    assert ranked.top1 is not None
    assert len(ranked.top3) == 3
    assert ranked.top1.post.key not in {item.post.key for item in ranked.top3}
    assert ranked.scored[0].total >= ranked.scored[-1].total
    assert ranked.candidates == ranked.top3


def test_rank_dedupes_by_key_keeping_best():
    weak = make_post(views=100, likes=1, replies=0, quotes=0, reposts=0)
    strong = make_post(views=999999, likes=9000, replies=1200, quotes=300, reposts=800)
    ranked = rank_posts([weak, strong], now=FIXED_NOW)
    assert len(ranked.scored) == 1
    assert ranked.top1 is not None
    assert ranked.top1.post.metrics.views == 999999


def test_rank_skips_empty_text_and_respects_min_engagement():
    empty = make_post(post_id="9", url="https://x.com/a/status/9", text="   ")
    ranked = rank_posts([empty], now=FIXED_NOW)
    assert ranked.top1 is None

    quiet = make_post(likes=0, replies=0, reposts=0, quotes=0, views=0)
    ranked_quiet = rank_posts([quiet], now=FIXED_NOW, cfg=RankConfig(min_engagement=10))
    assert ranked_quiet.top1 is None


def test_rank_is_deterministic_for_same_input():
    posts = [
        make_post(post_id="1", url="https://x.com/a/status/1", views=5000),
        make_post(post_id="2", url="https://x.com/b/status/2", views=5000),
    ]
    first = rank_posts(posts, now=FIXED_NOW)
    second = rank_posts(list(reversed(posts)), now=FIXED_NOW)
    assert first.top1 is not None and second.top1 is not None
    assert first.top1.post.key == second.top1.post.key


def test_snapshot_roundtrip(tmp_path, sample_post):
    path = tmp_path / "snapshots.json"
    save_snapshots(path, [sample_post], now=FIXED_NOW)
    loaded = load_snapshots(path)
    assert sample_post.key in loaded
    snapshot = loaded[sample_post.key]
    assert snapshot.metrics.likes == sample_post.metrics.likes
    # 时间断言用偏移容差，不做毫秒级相等比较
    assert abs((snapshot.captured_at - FIXED_NOW).total_seconds()) < 1


def test_load_snapshots_tolerates_missing_and_broken_file(tmp_path):
    assert load_snapshots(tmp_path / "missing.json") == {}
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    assert load_snapshots(broken) == {}


def test_prune_snapshots_drops_old_entries():
    old = PostSnapshot(
        key="x:1",
        url="https://x.com/a/status/1",
        author="a",
        captured_at=datetime.now(tz=UTC) - timedelta(days=60),
        metrics=Metrics(),
    )
    fresh = PostSnapshot(
        key="x:2",
        url="https://x.com/a/status/2",
        author="a",
        captured_at=datetime.now(tz=UTC),
        metrics=Metrics(),
    )
    pruned = prune_snapshots({"x:1": old, "x:2": fresh})
    assert set(pruned) == {"x:2"}


def test_hours_between_never_negative():
    now = datetime(2026, 11, 11, tzinfo=UTC)
    assert hours_between(now, now) == 0
    assert hours_between(now, now - timedelta(hours=3)) == 0
    assert hours_between(now, now + timedelta(hours=3)) == 3


def test_rank_exclude_urls_drops_published_primary():
    from src.common.models import canonical_url

    published = make_post()
    fresh = make_post(
        post_id="1",
        url="https://example.com/posts/1",
        likes=0,
        replies=0,
        reposts=0,
        quotes=0,
        views=10,
        followers=0,
    )
    baseline = rank_posts([published, fresh], now=FIXED_NOW)
    assert baseline.top1 is not None
    assert baseline.top1.post.key == published.key

    ranked = rank_posts(
        [published, fresh],
        now=FIXED_NOW,
        exclude_urls={canonical_url(SEED_URL) + "?utm_source=old"},
    )
    assert ranked.top1 is not None
    assert ranked.top1.post.url == "https://example.com/posts/1"
    assert published.key not in {item.post.key for item in ranked.scored}


def test_rank_exclude_urls_default_is_empty(sample_post):
    ranked = rank_posts([sample_post], now=FIXED_NOW)
    assert ranked.top1 is not None
    assert ranked.top1.post.key == sample_post.key


def test_published_record_is_idempotent(tmp_path):
    from src.common.models import canonical_url
    from src.rank.published import load_published, record_published

    path = tmp_path / "published.json"
    assert record_published(path, SEED_URL, date="2026-11-10", now=FIXED_NOW) is True
    entries = load_published(path)
    assert set(entries) == {canonical_url(SEED_URL)}
    first_stamp = entries[canonical_url(SEED_URL)].first_published_at

    # 同一 URL（含跟踪参数）重复运行：不新增、不改首次发布时间
    assert (
        record_published(
            path,
            SEED_URL + "?utm_source=dup",
            date="2026-11-11",
            now=FIXED_NOW + timedelta(days=1),
        )
        is False
    )
    entries = load_published(path)
    assert len(entries) == 1
    entry = entries[canonical_url(SEED_URL)]
    assert entry.date == "2026-11-10"
    assert entry.first_published_at == first_stamp


def test_published_keeps_most_recent_entries(tmp_path):
    from src.rank.published import MAX_PUBLISHED, load_published, record_published

    path = tmp_path / "published.json"
    for index in range(MAX_PUBLISHED + 3):
        record_published(
            path,
            f"https://example.com/posts/{index}",
            date="2026-11-10",
            now=FIXED_NOW + timedelta(minutes=index),
        )
    entries = load_published(path)
    assert len(entries) == MAX_PUBLISHED
    assert "https://example.com/posts/0" not in entries
    assert f"https://example.com/posts/{MAX_PUBLISHED + 2}" in entries


def test_load_published_tolerates_missing_and_broken_file(tmp_path):
    from src.rank.published import load_published

    assert load_published(tmp_path / "missing.json") == {}
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    assert load_published(broken) == {}
