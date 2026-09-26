"""Export and import of problems, attempts, notes, and review state."""

import json
import sqlite3
from datetime import date
from pathlib import Path

import pytest
from typer.testing import CliRunner

from nctrack.cli import app
from nctrack.db import (
    ExportDataError,
    ExportFormatError,
    connect,
    connect_for_import,
    create_schema,
    dumps_csv_files,
    dumps_json,
    export_payload,
    get_attempts,
    get_notes,
    get_review_state,
    insert_problems,
    log_attempt,
    normalize_export_format,
    parse_export_json,
    restore_export,
    save_notes,
)
from nctrack.models import Problem

runner = CliRunner()


def _problem(problem_id, slug, title="Two Sum", difficulty="Easy", category="Arrays & Hashing") -> Problem:
    return Problem(
        id=problem_id,
        slug=slug,
        title=title,
        difficulty=difficulty,
        category=category,
        leetcode_url=f"https://leetcode.com/problems/{slug}/",
        list_name="neetcode150",
    )


def _database(path: Path) -> sqlite3.Connection:
    raw = sqlite3.connect(path)
    raw.row_factory = sqlite3.Row
    raw.execute("PRAGMA foreign_keys = ON")
    create_schema(raw)
    raw.commit()
    raw.close()
    return connect(path)


def _seed_progress(connection: sqlite3.Connection) -> Problem:
    problem = _problem(1, "two-sum")
    other = _problem(42, "trapping-rain-water", "Trapping Rain Water", "Hard", "Two Pointers")
    insert_problems(connection, [problem, other])
    connection.commit()
    log_attempt(
        connection,
        problem,
        outcome="cold",
        minutes=18,
        notes="hash map, then scan",
        attempt_date=date(2026, 9, 1),
    )
    save_notes(
        connection,
        problem.id,
        key_insight="store complements",
        time_complexity="O(n)",
        space_complexity="O(n)",
        gotchas="duplicates",
    )
    return problem


def test_json_round_trip_restores_progress_without_duplicating(tmp_path):
    source = _database(tmp_path / "source.db")
    problem = _seed_progress(source)
    payload = export_payload(source)
    source.close()

    text = dumps_json(payload)
    restored = json.loads(text)
    assert restored["version"] == 1
    assert restored["attempts"][0]["notes"] == "hash map, then scan"
    assert restored["notes"][0]["key_insight"] == "store complements"
    assert restored["review_state"][0]["next_review_date"] == "2026-09-02"

    target = connect_for_import(tmp_path / "restored.db")
    first = restore_export(target, restored)
    second = restore_export(target, parse_export_json(text))
    assert first == second
    assert len(get_attempts(target, problem.id)) == 1
    notes = get_notes(target, problem.id)
    assert notes.gotchas == "duplicates"
    review = get_review_state(target, problem.id)
    assert review.next_review_date == date(2026, 9, 2)
    assert review.repetitions == 1
    new_attempt, _ = log_attempt(
        target,
        problem,
        outcome="hint",
        minutes=12,
        notes=None,
        attempt_date=date(2026, 9, 2),
    )
    assert new_attempt.id == 2
    target.close()


def test_import_replaces_one_problem_and_keeps_the_other(tmp_path):
    source = _database(tmp_path / "source.db")
    problem = _seed_progress(source)
    payload = export_payload(source)
    payload["problems"] = [item for item in payload["problems"] if item["id"] == 1]
    payload["attempts"][0]["notes"] = "updated"
    source.close()

    target = _database(tmp_path / "target.db")
    local = _problem(99, "climbing-stairs", "Climbing Stairs", "Easy", "1-D Dynamic Programming")
    insert_problems(target, [problem, local])
    target.commit()
    log_attempt(
        target,
        problem,
        outcome="failed",
        minutes=30,
        notes="old",
        attempt_date=date(2026, 8, 1),
    )
    log_attempt(
        target,
        local,
        outcome="cold",
        minutes=6,
        notes="keep me",
        attempt_date=date(2026, 8, 2),
    )
    restore_export(target, payload)
    assert [item.notes for item in get_attempts(target, 1)] == ["updated"]
    assert [item.notes for item in get_attempts(target, 99)] == ["keep me"]
    target.close()


def test_csv_files_include_notes_with_commas(tmp_path):
    connection = _database(tmp_path / "csv.db")
    _seed_progress(connection)
    files = dumps_csv_files(export_payload(connection))
    connection.close()
    assert "id,slug,title,difficulty,category,leetcode_url,list_name" in files["problems.csv"]
    assert "two-sum" in files["problems.csv"]
    assert "hash map, then scan" in files["attempts.csv"]
    assert "store complements" in files["notes.csv"]
    assert "next_review_date" in files["review_state.csv"]


def test_export_rejects_invalid_values():
    with pytest.raises(ExportFormatError, match="Expected one of: csv, json"):
        normalize_export_format("xml")
    with pytest.raises(ExportDataError, match="Could not read JSON export"):
        parse_export_json("{")
    payload = {
        "version": 1,
        "problems": [
            {
                "id": 1,
                "slug": "two-sum",
                "title": "Two Sum",
                "difficulty": "Easy",
                "category": "Arrays & Hashing",
                "leetcode_url": "https://leetcode.com/problems/two-sum/",
                "list_name": "neetcode150",
            }
        ],
        "attempts": [
            {
                "id": 1,
                "problem_id": 1,
                "date": "2026-09-01",
                "minutes": 10,
                "outcome": "nope",
                "notes": None,
            }
        ],
        "notes": [],
        "review_state": [],
    }
    with pytest.raises(ExportDataError, match="invalid outcome 'nope'"):
        restore_export(_database_in_memory(), payload)


def test_export_rejects_an_attempt_for_a_missing_problem():
    payload = {
        "version": 1,
        "problems": [],
        "attempts": [
            {
                "id": 1,
                "problem_id": 5,
                "date": "2026-09-01",
                "minutes": 10,
                "outcome": "cold",
                "notes": None,
            }
        ],
        "notes": [],
        "review_state": [],
    }
    with pytest.raises(ExportDataError, match="unknown problem 5"):
        restore_export(_database_in_memory(), payload)


def _database_in_memory() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    create_schema(connection)
    return connection


def test_cli_export_and_import(tmp_path):
    source = tmp_path / "source.db"
    backup = tmp_path / "backup.json"
    restored = tmp_path / "restored.db"
    assert runner.invoke(app, ["init", "--db", str(source)]).exit_code == 0
    logged = runner.invoke(
        app,
        [
            "log",
            "two-sum",
            "--outcome",
            "cold",
            "--minutes",
            "18",
            "--notes",
            "hash map",
            "--db",
            str(source),
        ],
    )
    assert logged.exit_code == 0
    noted = runner.invoke(
        app,
        ["note", "two-sum", "--db", str(source)],
        input="complements\nO(n)\nO(n)\nduplicates\n",
    )
    assert noted.exit_code == 0

    exported = runner.invoke(
        app,
        ["export", "--format", "json", "--out", str(backup), "--db", str(source)],
    )
    assert exported.exit_code == 0
    assert backup.is_file()

    imported = runner.invoke(app, ["import", str(backup), "--db", str(restored)])
    assert imported.exit_code == 0, imported.stdout
    shown = runner.invoke(app, ["show", "two-sum", "--db", str(restored)])
    assert shown.exit_code == 0
    assert "hash map" in shown.stdout
    assert "complements" in shown.stdout

    csv_dir = tmp_path / "csv"
    csv_export = runner.invoke(
        app,
        ["export", "--format", "csv", "--out", str(csv_dir), "--db", str(source)],
    )
    assert csv_export.exit_code == 0
    assert (csv_dir / "problems.csv").is_file()
    assert (csv_dir / "attempts.csv").is_file()
    assert (csv_dir / "notes.csv").is_file()

    rejected = runner.invoke(app, ["import", str(csv_dir / "problems.csv"), "--db", str(restored)])
    assert rejected.exit_code == 1
    assert "JSON export" in rejected.stdout

    bad_format = runner.invoke(app, ["export", "--format", "xml", "--db", str(source)])
    assert bad_format.exit_code == 1
    assert "Expected one of: csv, json" in bad_format.stdout
