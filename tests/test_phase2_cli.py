"""CLI coverage for the Phase 2 commands."""

import sqlite3
from datetime import date, timedelta

from typer.testing import CliRunner

from nctrack.cli import app
from nctrack.db import connect, create_schema, insert_problems, log_attempt, save_notes
from nctrack.models import Problem

runner = CliRunner()


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


def _empty_db(path):
    raw = sqlite3.connect(path)
    raw.row_factory = sqlite3.Row
    raw.execute("PRAGMA foreign_keys = ON")
    create_schema(raw)
    raw.commit()
    raw.close()


def test_stats_start_mock_plan_and_calendar(tmp_path):
    db = tmp_path / "phase2.db"
    _empty_db(db)
    connection = connect(db)
    insert_problems(
        connection,
        [
            _problem(1, "two-sum", "Two Sum", "Easy", "Arrays & Hashing"),
            _problem(42, "trapping-rain-water", "Trapping Rain Water", "Hard", "Two Pointers"),
        ],
    )
    connection.commit()
    save_notes(
        connection,
        1,
        key_insight="SECRET INSIGHT",
        time_complexity="O(n)",
        space_complexity="O(n)",
        gotchas="duplicates",
    )
    log_attempt(
        connection,
        _problem(42, "trapping-rain-water", "Trapping Rain Water", "Hard", "Two Pointers"),
        outcome="cold",
        minutes=30,
        notes=None,
        attempt_date=date.today(),
    )
    connection.close()
    args = ["--db", str(db)]

    summarized = runner.invoke(app, ["stats", *args])
    assert summarized.exit_code == 0
    assert "Arrays & Hashing" in summarized.stdout
    assert "0/1" in summarized.stdout
    assert "1/1" in summarized.stdout
    assert "Weakest:" in summarized.stdout

    started = runner.invoke(app, ["start", "two-sum", *args], input="\ncold\n")
    assert started.exit_code == 0, started.stdout
    assert "Solving" in started.stdout
    assert "Logged cold" in started.stdout
    assert "Two Sum" in started.stdout

    mocked = runner.invoke(
        app,
        ["mock", "--minutes", "15", *args],
        input="\nO(n)\nO(1)\nhint\n",
    )
    assert mocked.exit_code == 0, mocked.stdout
    assert "Notes are hidden." in mocked.stdout
    assert "SECRET INSIGHT" not in mocked.stdout
    assert "Time limit: 15 min" in mocked.stdout
    assert "Saved complexity: O(n) time, O(1) space." in mocked.stdout

    shown = runner.invoke(app, ["show", "1", *args])
    assert "SECRET INSIGHT" in shown.stdout

    interview = (date.today() + timedelta(days=10)).isoformat()
    planned = runner.invoke(app, ["plan", "--date", interview, *args])
    assert planned.exit_code == 0, planned.stdout
    assert "New problems per day:" in planned.stdout
    assert "Projected reviews per day:" in planned.stdout

    heatmap = runner.invoke(app, ["calendar", *args])
    assert heatmap.exit_code == 0
    assert "Last 12 weeks" in heatmap.stdout
    assert "Attempts in view:" in heatmap.stdout


def test_plan_rejects_bad_and_past_dates(tmp_path):
    db = tmp_path / "phase2.db"
    _empty_db(db)
    connection = connect(db)
    insert_problems(connection, [_problem(1, "two-sum", "Two Sum", "Easy", "Arrays & Hashing")])
    connection.commit()
    connection.close()
    args = ["--db", str(db)]

    bad = runner.invoke(app, ["plan", "--date", "next-friday", *args])
    assert bad.exit_code == 1
    assert "Expected YYYY-MM-DD" in bad.stdout

    past = runner.invoke(app, ["plan", "--date", date.today().isoformat(), *args])
    assert past.exit_code == 1
    assert "must be after today" in past.stdout


def test_mock_rejects_non_positive_limit(tmp_path):
    db = tmp_path / "phase2.db"
    _empty_db(db)
    result = runner.invoke(app, ["mock", "--minutes", "0", "--db", str(db)])
    assert result.exit_code == 1
    assert "greater than zero" in result.stdout
