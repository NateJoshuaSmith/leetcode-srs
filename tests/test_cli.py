"""CLI coverage for the Phase 1 commands."""

from datetime import date, timedelta

from typer.testing import CliRunner

from nctrack.cli import app

runner = CliRunner()


def test_commands_require_init(tmp_path):
    db = tmp_path / "missing.db"
    result = runner.invoke(app, ["list", "--db", str(db)])
    assert result.exit_code == 1
    assert "nctrack init" in result.stdout


def test_init_list_log_show_note_and_due(tmp_path, monkeypatch):
    monkeypatch.delenv("NCTRACK_DB", raising=False)
    db = tmp_path / "cli.db"
    args = ["--db", str(db)]

    init = runner.invoke(app, ["init", *args])
    assert init.exit_code == 0
    assert "150" in init.stdout

    again = runner.invoke(app, ["init", *args])
    assert again.exit_code == 0
    assert "No new problems" in again.stdout

    listed = runner.invoke(
        app,
        ["list", "--category", "Arrays & Hashing", "--difficulty", "easy", "--status", "unsolved", *args],
    )
    assert listed.exit_code == 0
    assert "Two Sum" in listed.stdout
    assert "Trapping Rain Water" not in listed.stdout

    logged = runner.invoke(
        app,
        ["log", "two-sum", "--outcome", "cold", "--minutes", "18", "--notes", "hash map", *args],
    )
    assert logged.exit_code == 0
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    assert "Two Sum" in logged.stdout
    assert tomorrow in logged.stdout

    shown = runner.invoke(app, ["show", "1", *args])
    assert shown.exit_code == 0
    assert "hash map" in shown.stdout
    assert "Slug: two-sum" in shown.stdout
    assert tomorrow in shown.stdout

    noted = runner.invoke(
        app,
        ["note", "two-sum", *args],
        input="complement map\nO(n)\nO(n)\nwatch duplicates\n",
    )
    assert noted.exit_code == 0
    shown_again = runner.invoke(app, ["show", "two-sum", *args])
    assert "complement map" in shown_again.stdout
    assert "watch duplicates" in shown_again.stdout

    interactive = runner.invoke(
        app,
        ["log", "trapping rain", *args],
        input="hint\n30\nused two pointers\n",
    )
    assert interactive.exit_code == 0
    assert "Trapping Rain Water" in interactive.stdout

    due = runner.invoke(app, ["due", *args])
    assert due.exit_code == 0
    assert "No problems are due." in due.stdout


def test_unknown_problem_and_invalid_outcome(tmp_path):
    db = tmp_path / "cli.db"
    args = ["--db", str(db)]
    assert runner.invoke(app, ["init", *args]).exit_code == 0

    missing = runner.invoke(app, ["show", "not-a-problem", *args])
    assert missing.exit_code == 1
    assert "No problem matches" in missing.stdout

    bad = runner.invoke(app, ["log", "two-sum", "--outcome", "nope", "--minutes", "5", *args])
    assert bad.exit_code == 1
    assert "Invalid outcome 'nope'" in bad.stdout


def test_ambiguous_title_prompts(tmp_path):
    db = tmp_path / "cli.db"
    args = ["--db", str(db)]
    assert runner.invoke(app, ["init", *args]).exit_code == 0
    result = runner.invoke(app, ["show", "sum", *args], input="1\n")
    assert result.exit_code == 0
    assert "Multiple problems match" in result.stdout
    assert "Slug: two-sum" in result.stdout


def test_db_flag_overrides_env(tmp_path, monkeypatch):
    env_db = tmp_path / "env.db"
    flag_db = tmp_path / "flag.db"
    monkeypatch.setenv("NCTRACK_DB", str(env_db))
    result = runner.invoke(app, ["init", "--db", str(flag_db)])
    assert result.exit_code == 0
    assert flag_db.exists()
    assert not env_db.exists()
    env_init = runner.invoke(app, ["init"])
    assert env_init.exit_code == 0
    assert env_db.exists()
