"""Unit tests for category stats, the activity calendar, and mock weights."""

from datetime import date, timedelta
from random import Random

import pytest

from nctrack.models import AttemptSnapshot
from nctrack.stats import (
    activity_calendar,
    category_stats,
    choose_weighted,
    elapsed_minutes,
    mock_weight,
    sunday_on_or_before,
)


def _snap(category, difficulty, solved, outcome, minutes) -> AttemptSnapshot:
    return AttemptSnapshot(
        category=category,
        difficulty=difficulty,
        solved=solved,
        latest_outcome=outcome,
        latest_minutes=minutes,
    )


def test_stats_use_latest_attempt_only_for_cold_rate_and_minutes():
    rows = category_stats(
        [
            _snap("Arrays & Hashing", "Easy", True, "hint", 10),
            _snap("Arrays & Hashing", "Easy", True, "cold", 8),
            _snap("Arrays & Hashing", "Medium", False, None, None),
        ]
    )
    arrays = rows[0]
    assert arrays.solved == 2
    assert arrays.total == 3
    assert arrays.attempted == 2
    assert arrays.cold_solves == 1
    assert arrays.cold_solve_rate == pytest.approx(0.5)
    assert arrays.average_minutes == pytest.approx(9)
    assert arrays.target_minutes == pytest.approx(10)


def test_failed_latest_attempt_is_not_a_cold_solve_even_if_the_problem_was_solved():
    rows = category_stats([_snap("Stack", "Easy", True, "failed", 40)])
    assert rows[0].solved == 1
    assert rows[0].cold_solve_rate == pytest.approx(0)
    assert rows[0].average_minutes == pytest.approx(40)
    assert rows[0].target_minutes == pytest.approx(10)


def test_category_with_no_attempts_has_no_rate_or_average():
    rows = category_stats([_snap("Trees", "Hard", False, None, None)])
    assert rows[0].cold_solve_rate is None
    assert rows[0].average_minutes is None
    assert rows[0].target_minutes is None
    assert rows[0].weakness == pytest.approx(1)


def test_weakness_scores_and_highlights_three_categories():
    rows = category_stats(
        [
            _snap("Sliding Window", "Easy", True, "cold", 10),
            _snap("Arrays & Hashing", "Easy", False, None, None),
            _snap("Two Pointers", "Easy", True, "hint", 10),
            _snap("Stack", "Easy", True, "cold", 20),
        ]
    )
    by_name = {row.category: row for row in rows}
    assert by_name["Sliding Window"].weakness == pytest.approx(0)
    assert by_name["Arrays & Hashing"].weakness == pytest.approx(1)
    assert by_name["Two Pointers"].weakness == pytest.approx(1 / 3)
    assert by_name["Stack"].weakness == pytest.approx(1 - 2.5 / 3)
    weakest = {row.category for row in rows if row.weakest}
    assert weakest == {"Arrays & Hashing", "Two Pointers", "Stack"}
    assert by_name["Sliding Window"].weakest is False


def test_weakest_tie_prefers_more_unsolved_problems_then_roadmap_order():
    rows = category_stats(
        [
            _snap("Arrays & Hashing", "Easy", False, None, None),
            _snap("Two Pointers", "Easy", False, None, None),
            _snap("Sliding Window", "Easy", False, None, None),
            _snap("Stack", "Easy", False, None, None),
        ]
    )
    weakest = [row.category for row in rows if row.weakest]
    assert weakest == ["Arrays & Hashing", "Two Pointers", "Sliding Window"]

    rows = category_stats(
        [
            _snap("Arrays & Hashing", "Easy", False, None, None),
            _snap("Two Pointers", "Easy", True, "cold", 10),
            _snap("Sliding Window", "Easy", False, None, None),
            _snap("Stack", "Easy", False, None, None),
            _snap("Trees", "Easy", False, None, None),
            _snap("Trees", "Medium", False, None, None),
            _snap("Trees", "Hard", False, None, None),
        ]
    )
    weakest = {row.category for row in rows if row.weakest}
    assert weakest == {"Trees", "Arrays & Hashing", "Sliding Window"}
    assert all(row.weakest is False for row in rows if row.category in {"Two Pointers", "Stack"})


def test_elapsed_minutes_rounds_and_keeps_a_one_minute_floor():
    assert elapsed_minutes(0) == 0
    assert elapsed_minutes(-5) == 0
    assert elapsed_minutes(30) == 1
    assert elapsed_minutes(60) == 1
    assert elapsed_minutes(89) == 1
    assert elapsed_minutes(90) == 2


def test_mock_weight_prefers_weak_unsolved_and_due_problems():
    strong = mock_weight(
        category="Arrays & Hashing",
        solved=True,
        due=False,
        weakness_by_category={"Arrays & Hashing": 0},
    )
    unsolved = mock_weight(
        category="Arrays & Hashing",
        solved=False,
        due=False,
        weakness_by_category={"Arrays & Hashing": 0},
    )
    due = mock_weight(
        category="Arrays & Hashing",
        solved=False,
        due=True,
        weakness_by_category={"Arrays & Hashing": 0},
    )
    weak = mock_weight(
        category="Graphs",
        solved=False,
        due=False,
        weakness_by_category={"Graphs": 1},
    )
    assert unsolved > strong
    assert due > unsolved
    assert weak > unsolved


def test_choose_weighted_follows_the_heavier_problem():
    rng = Random(1)
    picks = [choose_weighted([1, 2], [1, 40], rng=rng) for _ in range(200)]
    assert picks.count(2) > picks.count(1)


def test_activity_calendar_covers_twelve_sunday_weeks():
    today = date(2026, 9, 25)
    weeks = activity_calendar([today, today, date(2020, 1, 1)], today=today)
    assert len(weeks) == 12
    assert all(len(week.days) == 7 for week in weeks)
    assert weeks[0].start == sunday_on_or_before(today) - timedelta(days=77)
    assert weeks[-1].start == date(2026, 9, 20)
    friday = weeks[-1].days[5]
    assert friday.day == today
    assert friday.count == 2
    assert weeks[-1].days[6].day == date(2026, 9, 26)
    assert weeks[-1].days[6].count is None
    assert all(day.day != date(2020, 1, 1) for week in weeks for day in week.days)


def test_calendar_counts_a_quiet_day_as_zero():
    today = date(2026, 9, 20)
    weeks = activity_calendar([], today=today)
    assert weeks[-1].days[0].day == today
    assert weeks[-1].days[0].count == 0


def test_database_latest_attempt_is_the_newest_by_date_then_id(tmp_path):
    import sqlite3

    from nctrack.db import connect, create_schema, insert_problems, log_attempt, load_practice_records
    from nctrack.models import Problem

    path = tmp_path / "latest.db"
    raw = sqlite3.connect(path)
    raw.row_factory = sqlite3.Row
    raw.execute("PRAGMA foreign_keys = ON")
    create_schema(raw)
    raw.commit()
    raw.close()
    problem = Problem(
        1,
        "two-sum",
        "Two Sum",
        "Easy",
        "Arrays & Hashing",
        "https://leetcode.com/problems/two-sum/",
        "neetcode150",
    )
    connection = connect(path)
    insert_problems(connection, [problem])
    connection.commit()
    log_attempt(
        connection,
        problem,
        outcome="cold",
        minutes=40,
        notes=None,
        attempt_date=date(2026, 1, 1),
    )
    log_attempt(
        connection,
        problem,
        outcome="hint",
        minutes=12,
        notes=None,
        attempt_date=date(2026, 1, 1),
    )
    log_attempt(
        connection,
        problem,
        outcome="failed",
        minutes=7,
        notes=None,
        attempt_date=date(2026, 1, 3),
    )
    record = load_practice_records(connection, today=date(2026, 1, 3))[0]
    assert record.solved is True
    assert record.latest_outcome == "failed"
    assert record.latest_minutes == 7
    assert record.next_review_date == date(2026, 1, 4)
    assert record.due is False
    due_tomorrow = load_practice_records(connection, today=date(2026, 1, 4))[0]
    connection.close()
    assert due_tomorrow.due is True
