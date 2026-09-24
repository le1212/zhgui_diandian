"""usage_stats 守护：连击结算、里程碑只触发一次、省时估算与持久化往返。"""

from pathlib import Path

from usage_stats import MANUAL_CLICK_MS, UsageStats


def test_load_roundtrip(tmp_path: Path):
    path = tmp_path / "stats.json"
    stats = UsageStats(total_clicks=120, total_runs=3, saved_ms=4567, day_streak=4,
                       today="2026-09-24", today_clicks=20, last_seen="2026-09-24",
                       milestones=["clicks:1000"])
    assert stats.save(path)
    loaded = UsageStats.load(path)
    assert loaded.to_dict() == stats.to_dict()


def test_load_corrupt_file_falls_back_to_defaults(tmp_path: Path):
    path = tmp_path / "stats.json"
    path.write_text("{ 不是 json", encoding="utf-8")
    assert UsageStats.load(path).total_clicks == 0


def test_load_rejects_wrong_field_types(tmp_path: Path):
    """畸形字段（错型标量/非列表里程碑）不得阻断启动或静默腐化。"""
    path = tmp_path / "stats.json"
    path.write_text(
        '{"stats": {"total_clicks": "1000", "today_clicks": 5, "saved_ms": true,'
        ' "milestones": 7, "day_streak": "3"}}',
        encoding="utf-8",
    )
    stats = UsageStats.load(path)
    assert stats.total_clicks == 0 and stats.today_clicks == 5
    assert stats.saved_ms == 0 and stats.day_streak == 0
    assert stats.milestones == []


def test_session_start_same_day_is_idempotent():
    stats = UsageStats(last_seen="2026-09-24", day_streak=5, today="2026-09-24", today_clicks=9)
    fired, absent = stats.note_session_start("2026-09-24", "2026-09-23")
    assert fired == [] and absent == 0
    assert stats.day_streak == 5  # 同日重复启动不动连击


def test_session_start_consecutive_day_extends_streak():
    stats = UsageStats(last_seen="2026-09-23", day_streak=6)
    fired, absent = stats.note_session_start("2026-09-24", "2026-09-23")
    assert stats.day_streak == 7
    assert "streak:7" in fired
    assert absent == 1


def test_session_start_gap_resets_streak():
    stats = UsageStats(last_seen="2026-09-20", day_streak=9)
    fired, absent = stats.note_session_start("2026-09-24", "2026-09-23")
    assert stats.day_streak == 1
    assert fired == []  # 断档后不再补发旧的连击里程碑
    assert absent == 4


def test_note_run_accumulates_and_fires_each_milestone_once():
    stats = UsageStats(today="2026-09-24")
    fired = stats.note_run(clicks=1000, run_ms=60_000, interval_ms=500, today="2026-09-24")
    assert "clicks:1000" in fired and "runs:10" not in fired
    assert stats.today_clicks == 1000
    assert stats.saved_ms == 1000 * (MANUAL_CLICK_MS - 500)
    again = stats.note_run(clicks=500, run_ms=30_000, interval_ms=0, today="2026-09-24")
    assert "clicks:1000" not in again  # 里程碑只发一次
    assert stats.total_clicks == 1500 and stats.total_runs == 2


def test_note_run_rolls_over_today_counter():
    stats = UsageStats(today="2026-09-23", today_clicks=7)
    stats.note_run(clicks=50, run_ms=1000, interval_ms=250, today="2026-09-24")
    assert stats.today_clicks == 50  # 跨天：切到新一天重新起算


def test_saved_time_never_negative_on_slow_intervals():
    stats = UsageStats()
    stats.note_run(clicks=100, run_ms=1000, interval_ms=5000, today="2026-09-24")
    assert stats.saved_ms == 0  # 间隔比人工还慢时不产生"负省时"
