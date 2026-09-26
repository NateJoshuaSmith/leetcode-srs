"""Database behavior for init, lookup, logging, list filters, and the due queue."""

from datetime import date

import pytest

from nctrack.db import (
    AmbiguousProblemError,
    InvalidDifficultyError,
    InvalidMinutesError,
    InvalidStatusError,
    ProblemNotFoundError,
    UnknownCategoryError,
    connect,
    create_schema,
    due_problems,
    get_attempts,
    get_notes,
    get_problem_detail,
    initialize,
    insert_problems,
    list_problems,
    log_attempt,
    lookup_problem,
    resolve_db_path,
    save_notes,
)
from nctrack.models import Problem
from nctrack.scheduler import InvalidOutcomeError


def _problem(problem_id, slug, title, difficulty, category) -> Problem:
    return Problem(
        id=problem_id,
        slug=slug,
        title=title,
        difficulty=difficulty,
        category=category,
        leetcode_url=f"https://leetcode.com/problems/{slug}/",
        list_name="neetcode150",
    )


SAMPLES = [
    _problem(1, "two-sum", "Two Sum", "Easy", "Arrays & Hashing"),
    _problem(167, "two-sum-ii-input-array-is-sorted", "Two Sum II - Input Array Is Sorted", "Medium", "Two Pointers"),
    _problem(15, "3sum", "3Sum", "Medium", "Two Pointers"),
    _problem(42, "trapping-rain-water", "Trapping Rain Water", "Hard", "Two Pointers"),
    _problem(121, "best-time-to-buy-and-sell-stock", "Best Time to Buy and Sell Stock", "Easy", "Sliding Window"),
]


@pytest.fixture
def conn(tmp_path):
    path = tmp_path / "nctrack.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    import sqlite3

    raw = sqlite3.connect(path)
    raw.row_factory = sqlite3.Row
    raw.execute("PRAGMA foreign_keys = ON")
    create_schema(raw)
    insert_problems(raw, SAMPLES)
    raw.commit()
    raw.close()
    connection = connect(path)
    yield connection
    connection.close()


def test_init_loads_seed_once(tmp_path):
    path = tmp_path / "seed.db"
    first = initialize(path)
    assert first.added == 150
    assert first.total == 150
    second = initialize(path)
    assert second.added == 0
    assert second.total == 150

    connection = connect(path)
    counts = dict(
        connection.execute(
            "SELECT difficulty, COUNT(*) FROM problems GROUP BY difficulty"
        ).fetchall()
    )
    assert counts == {"Easy": 28, "Medium": 101, "Hard": 21}
    two_sum = lookup_problem(connection, "1")
    assert two_sum.slug == "two-sum"
    assert two_sum.category == "Arrays & Hashing"
    assert two_sum.difficulty == "Easy"
    assert two_sum.list_name == "neetcode150"
    climbing = lookup_problem(connection, "climbing-stairs")
    assert climbing.category == "1-D Dynamic Programming"
    connection.close()


def test_reinit_keeps_attempts(tmp_path):
    path = tmp_path / "seed.db"
    initialize(path)
    connection = connect(path)
    problem = lookup_problem(connection, "two-sum")
    log_attempt(
        connection,
        problem,
        outcome="cold",
        minutes=8,
        notes="hash map",
        attempt_date=date(2026, 1, 1),
    )
    connection.close()
    initialize(path)
    connection = connect(path)
    assert len(get_attempts(connection, problem.id)) == 1
    assert connection.execute("SELECT COUNT(*) FROM problems").fetchone()[0] == 150
    connection.close()


def test_lookup_by_id_slug_title_and_partial(conn):
    assert lookup_problem(conn, "1").slug == "two-sum"
    assert lookup_problem(conn, "two-sum").id == 1
    assert lookup_problem(conn, "Two Sum").id == 1
    assert lookup_problem(conn, "trapping rain").id == 42
    assert lookup_problem(conn, "TWO-SUM-II").id == 167


def test_lookup_errors(conn):
    with pytest.raises(ProblemNotFoundError, match="No problem matches 'missing'"):
        lookup_problem(conn, "missing")
    with pytest.raises(ProblemNotFoundError, match="9999"):
        lookup_problem(conn, "9999")
    with pytest.raises(AmbiguousProblemError) as caught:
        lookup_problem(conn, "sum")
    assert [problem.id for problem in caught.value.matches] == [1, 15, 167]


def test_log_updates_schedule_across_attempts(conn):
    problem = lookup_problem(conn, "two-sum")
    _, first = log_attempt(
        conn,
        problem,
        outcome="cold",
        minutes=10,
        notes="  map  ",
        attempt_date=date(2026, 1, 1),
    )
    assert first.repetitions == 1
    assert first.interval_days == 1
    assert first.ease_factor == pytest.approx(2.6)
    assert first.next_review_date == date(2026, 1, 2)
    attempts = get_attempts(conn, problem.id)
    assert attempts[0].notes == "map"
    assert attempts[0].outcome == "cold"

    _, second = log_attempt(
        conn,
        problem,
        outcome="cold",
        minutes=9,
        notes=None,
        attempt_date=date(2026, 1, 2),
    )
    assert second.repetitions == 2
    assert second.interval_days == 3
    assert second.ease_factor == pytest.approx(2.7)
    assert second.next_review_date == date(2026, 1, 5)


def test_failed_attempt_is_unsolved_and_due_the_next_day(conn):
    problem = lookup_problem(conn, "two-sum")
    log_attempt(
        conn,
        problem,
        outcome="failed",
        minutes=25,
        notes=None,
        attempt_date=date(2026, 3, 1),
    )
    today = date(2026, 3, 1)
    listed = list_problems(conn, today=today, status="unsolved")
    assert [item.id for item in listed if item.id == 1] == [1]
    assert due_problems(conn, today=today) == []
    due = due_problems(conn, today=date(2026, 3, 2))
    assert [item.id for item in due] == [1]
    assert due[0].status == "due"
    assert due[0].attempt_count == 1
    assert due[0].solved is False


def test_solved_problem_is_due_on_review_date(conn):
    problem = lookup_problem(conn, "two-sum")
    log_attempt(
        conn,
        problem,
        outcome="Cold",
        minutes=18,
        notes=None,
        attempt_date=date(2026, 3, 1),
    )
    on_attempt_day = list_problems(conn, today=date(2026, 3, 1))
    two_sum = next(item for item in on_attempt_day if item.id == 1)
    assert two_sum.status == "solved"
    assert two_sum.attempt_count == 1
    assert two_sum.next_review_date == date(2026, 3, 2)

    solved_ids = [item.id for item in list_problems(conn, today=date(2026, 3, 2), status="solved")]
    due_ids = [item.id for item in list_problems(conn, today=date(2026, 3, 2), status="due")]
    unsolved_ids = [item.id for item in list_problems(conn, today=date(2026, 3, 2), status="unsolved")]
    assert 1 in solved_ids
    assert 1 in due_ids
    assert 1 not in unsolved_ids


def test_list_filters_combine(conn):
    rows = list_problems(
        conn,
        today=date(2026, 1, 1),
        category="two pointers",
        difficulty="hard",
    )
    assert [item.slug for item in rows] == ["trapping-rain-water"]


def test_list_rejects_unknown_filters(conn):
    with pytest.raises(UnknownCategoryError, match="Unknown category"):
        list_problems(conn, today=date(2026, 1, 1), category="Sorting")
    with pytest.raises(InvalidDifficultyError, match="Invalid difficulty"):
        list_problems(conn, today=date(2026, 1, 1), difficulty="Impossible")
    with pytest.raises(InvalidStatusError, match="Invalid status"):
        list_problems(conn, today=date(2026, 1, 1), status="maybe")


def test_due_queue_is_oldest_first(conn):
    rain = lookup_problem(conn, "trapping-rain-water")
    stock = lookup_problem(conn, "best-time-to-buy-and-sell-stock")
    two_sum = lookup_problem(conn, "two-sum")
    log_attempt(conn, rain, outcome="hint", minutes=20, notes=None, attempt_date=date(2026, 1, 1))
    log_attempt(conn, stock, outcome="hint", minutes=5, notes=None, attempt_date=date(2026, 1, 3))
    log_attempt(conn, two_sum, outcome="solution", minutes=12, notes=None, attempt_date=date(2026, 1, 1))
    due = due_problems(conn, today=date(2026, 1, 10))
    assert [item.slug for item in due] == [
        "trapping-rain-water",
        "two-sum",
        "best-time-to-buy-and-sell-stock",
    ]


def test_invalid_log_does_not_write(conn):
    problem = lookup_problem(conn, "two-sum")
    with pytest.raises(InvalidOutcomeError):
        log_attempt(
            conn,
            problem,
            outcome="almost",
            minutes=10,
            notes=None,
            attempt_date=date(2026, 1, 1),
        )
    with pytest.raises(InvalidMinutesError):
        log_attempt(
            conn,
            problem,
            outcome="cold",
            minutes=-1,
            notes=None,
            attempt_date=date(2026, 1, 1),
        )
    assert get_attempts(conn, problem.id) == []


def test_notes_round_trip(conn):
    problem = lookup_problem(conn, "two-sum")
    save_notes(
        conn,
        problem.id,
        key_insight=" store complements ",
        time_complexity="O(n)",
        space_complexity="O(n)",
        gotchas="",
    )
    notes = get_notes(conn, problem.id)
    assert notes.key_insight == "store complements"
    assert notes.gotchas is None
    detail = get_problem_detail(conn, problem, today=date(2026, 1, 1))
    assert detail.notes.time_complexity == "O(n)"
    assert detail.attempts == []
    assert detail.review is None
    assert detail.status == "unsolved"


def test_resolve_db_path_precedence(tmp_path, monkeypatch):
    monkeypatch.setenv("NCTRACK_DB", str(tmp_path / "env.db"))
    assert resolve_db_path(tmp_path / "flag.db") == tmp_path / "flag.db"
    assert resolve_db_path(None) == tmp_path / "env.db"
    monkeypatch.delenv("NCTRACK_DB")
    monkeypatch.setattr("nctrack.db.Path.home", classmethod(lambda cls: tmp_path))
    assert resolve_db_path(None) == tmp_path / ".nctrack" / "nctrack.db"
