"""SQLite storage for problems, attempts, notes, and review state."""

from __future__ import annotations

import csv
import io
import json
import os
import sqlite3
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from nctrack.models import (
    CATEGORY_ORDER,
    DEFAULT_EASE_FACTOR,
    DIFFICULTIES,
    SOLVED_OUTCOMES,
    STATUSES,
    Attempt,
    InitResult,
    PracticeRecord,
    Problem,
    ProblemDetail,
    ProblemNotes,
    ProblemSummary,
    ReviewState,
)
from nctrack.scheduler import schedule

SCHEMA = """
CREATE TABLE IF NOT EXISTS problems (
    id INTEGER PRIMARY KEY,
    slug TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    difficulty TEXT NOT NULL CHECK (difficulty IN ('Easy', 'Medium', 'Hard')),
    category TEXT NOT NULL,
    leetcode_url TEXT NOT NULL,
    list_name TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    problem_id INTEGER NOT NULL REFERENCES problems(id),
    date TEXT NOT NULL,
    minutes INTEGER NOT NULL CHECK (minutes >= 0),
    outcome TEXT NOT NULL CHECK (outcome IN ('cold', 'hint', 'solution', 'failed')),
    notes TEXT
);

CREATE TABLE IF NOT EXISTS review_state (
    problem_id INTEGER PRIMARY KEY REFERENCES problems(id),
    ease_factor REAL NOT NULL DEFAULT 2.5,
    interval_days INTEGER NOT NULL,
    repetitions INTEGER NOT NULL,
    next_review_date TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS problem_notes (
    problem_id INTEGER PRIMARY KEY REFERENCES problems(id),
    key_insight TEXT,
    time_complexity TEXT,
    space_complexity TEXT,
    gotchas TEXT
);
"""


class DatabaseNotInitializedError(Exception):
    def __init__(self, path: Path) -> None:
        self.path = path
        super().__init__(
            f"Database not found at {path}. Run `nctrack init` first."
        )


class ProblemNotFoundError(Exception):
    def __init__(self, query: str) -> None:
        self.query = query
        super().__init__(f"No problem matches {query!r}.")


class AmbiguousProblemError(Exception):
    def __init__(self, query: str, matches: list[Problem]) -> None:
        self.query = query
        self.matches = matches
        super().__init__(f"Multiple problems match {query!r}.")


class UnknownCategoryError(Exception):
    def __init__(self, category: str, known: list[str]) -> None:
        self.category = category
        self.known = known
        joined = ", ".join(known)
        super().__init__(f"Unknown category {category!r}. Known categories: {joined}.")


class InvalidDifficultyError(Exception):
    def __init__(self, difficulty: str) -> None:
        self.difficulty = difficulty
        joined = ", ".join(DIFFICULTIES)
        super().__init__(
            f"Invalid difficulty {difficulty!r}. Expected one of: {joined}."
        )


class InvalidStatusError(Exception):
    def __init__(self, status: str) -> None:
        self.status = status
        joined = ", ".join(STATUSES)
        super().__init__(f"Invalid status {status!r}. Expected one of: {joined}.")


class InvalidMinutesError(Exception):
    def __init__(self, minutes: int) -> None:
        self.minutes = minutes
        super().__init__(f"Minutes must be zero or greater, got {minutes}.")


class ExportFormatError(ValueError):
    def __init__(self, fmt: str) -> None:
        self.format = fmt
        super().__init__(f"Invalid format {fmt!r}. Expected one of: csv, json.")


class ExportDataError(ValueError):
    """The JSON export is missing data or contains a value the database cannot store."""


@dataclass(frozen=True)
class ImportResult:
    problems: int
    attempts: int
    notes: int
    reviews: int


EXPORT_VERSION = 1
CSV_FILES = {
    "problems": "problems.csv",
    "attempts": "attempts.csv",
    "notes": "notes.csv",
    "review_state": "review_state.csv",
}
_PROBLEM_FIELDS = (
    "id",
    "slug",
    "title",
    "difficulty",
    "category",
    "leetcode_url",
    "list_name",
)
_ATTEMPT_FIELDS = ("id", "problem_id", "date", "minutes", "outcome", "notes")
_NOTE_FIELDS = (
    "problem_id",
    "key_insight",
    "time_complexity",
    "space_complexity",
    "gotchas",
)
_REVIEW_FIELDS = (
    "problem_id",
    "ease_factor",
    "interval_days",
    "repetitions",
    "next_review_date",
)


def resolve_db_path(explicit: Path | None = None) -> Path:
    """Flag, then NCTRACK_DB, then ~/.nctrack/nctrack.db."""
    if explicit is not None:
        return Path(explicit).expanduser()
    env = os.environ.get("NCTRACK_DB")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".nctrack" / "nctrack.db"


def seed_data_path() -> Path:
    """Locate data/problems.json next to the repository checkout."""
    repo_path = Path(__file__).resolve().parents[1] / "data" / "problems.json"
    if repo_path.is_file():
        return repo_path
    raise FileNotFoundError(
        f"Seed file not found at {repo_path}. Expected data/problems.json."
    )


def connect(path: Path) -> sqlite3.Connection:
    if not path.exists():
        raise DatabaseNotInitializedError(path)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'problems'"
    ).fetchone()
    if row is None:
        conn.close()
        raise DatabaseNotInitializedError(path)
    return conn


def create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)


def load_seed(path: Path | None = None) -> list[Problem]:
    seed_path = path or seed_data_path()
    raw = json.loads(seed_path.read_text(encoding="utf-8"))
    problems: list[Problem] = []
    for item in raw:
        difficulty = item["difficulty"]
        if difficulty not in DIFFICULTIES:
            raise ValueError(
                f"Seed problem {item.get('slug')!r} has invalid difficulty {difficulty!r}."
            )
        problems.append(
            Problem(
                id=int(item["id"]),
                slug=item["slug"],
                title=item["title"],
                difficulty=difficulty,
                category=item["category"],
                leetcode_url=item["leetcode_url"],
                list_name=item["list_name"],
            )
        )
    return problems


def insert_problems(conn: sqlite3.Connection, problems: list[Problem]) -> int:
    """Insert problems, skipping ones already stored. Returns how many were added."""
    before = conn.execute("SELECT COUNT(*) FROM problems").fetchone()[0]
    conn.executemany(
        """
        INSERT OR IGNORE INTO problems (
            id, slug, title, difficulty, category, leetcode_url, list_name
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                problem.id,
                problem.slug,
                problem.title,
                problem.difficulty,
                problem.category,
                problem.leetcode_url,
                problem.list_name,
            )
            for problem in problems
        ],
    )
    after = conn.execute("SELECT COUNT(*) FROM problems").fetchone()[0]
    return after - before


def initialize(path: Path, seed_path: Path | None = None) -> InitResult:
    """Create the database if needed and load seed problems. Safe to re-run."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        create_schema(conn)
        added = insert_problems(conn, load_seed(seed_path))
        total = conn.execute("SELECT COUNT(*) FROM problems").fetchone()[0]
        conn.commit()
    finally:
        conn.close()
    return InitResult(path=str(path), added=added, total=total)


def list_problems(
    conn: sqlite3.Connection,
    *,
    today: date,
    category: str | None = None,
    status: str | None = None,
    difficulty: str | None = None,
) -> list[ProblemSummary]:
    difficulty_filter = _canonical_difficulty(difficulty) if difficulty else None
    status_filter = _canonical_status(status) if status else None
    known_categories = _known_categories(conn)
    category_filter = _match_category(category, known_categories) if category else None

    solved_placeholders = ", ".join("?" for _ in SOLVED_OUTCOMES)
    rows = conn.execute(
        f"""
        SELECT
            p.id,
            p.slug,
            p.title,
            p.difficulty,
            p.category,
            (
                SELECT COUNT(*) FROM attempts a WHERE a.problem_id = p.id
            ) AS attempt_count,
            (
                SELECT COUNT(*) FROM attempts a
                WHERE a.problem_id = p.id AND a.outcome IN ({solved_placeholders})
            ) AS solved_count,
            rs.next_review_date
        FROM problems p
        LEFT JOIN review_state rs ON rs.problem_id = p.id
        """,
        SOLVED_OUTCOMES,
    ).fetchall()

    summaries: list[ProblemSummary] = []
    for row in rows:
        if category_filter and row["category"] != category_filter:
            continue
        if difficulty_filter and row["difficulty"] != difficulty_filter:
            continue
        next_review = _parse_date(row["next_review_date"])
        solved = row["solved_count"] > 0
        due = next_review is not None and next_review <= today
        label = _display_status(solved, due)
        if status_filter == "unsolved" and solved:
            continue
        if status_filter == "solved" and not solved:
            continue
        if status_filter == "due" and not due:
            continue
        summaries.append(
            ProblemSummary(
                id=row["id"],
                slug=row["slug"],
                title=row["title"],
                difficulty=row["difficulty"],
                category=row["category"],
                attempt_count=row["attempt_count"],
                status=label,
                solved=solved,
                due=due,
                next_review_date=next_review,
            )
        )

    order = {name: index for index, name in enumerate(CATEGORY_ORDER)}
    summaries.sort(key=lambda item: (order.get(item.category, len(order)), item.title.casefold(), item.id))
    return summaries


def due_problems(conn: sqlite3.Connection, *, today: date) -> list[ProblemSummary]:
    """Problems whose next review date is today or earlier, oldest first."""
    due = [item for item in list_problems(conn, today=today, status="due")]
    due.sort(key=lambda item: (item.next_review_date or today, item.title.casefold(), item.id))
    return due


def lookup_problem(conn: sqlite3.Connection, query: str) -> Problem:
    """Resolve an id, exact slug, exact title, or a unique partial title/slug."""
    text = query.strip()
    if not text:
        raise ProblemNotFoundError(query)

    if text.isdigit():
        row = conn.execute("SELECT * FROM problems WHERE id = ?", (int(text),)).fetchone()
        if row is None:
            raise ProblemNotFoundError(query)
        return _problem_from_row(row)

    problems = [
        _problem_from_row(row) for row in conn.execute("SELECT * FROM problems").fetchall()
    ]
    exact_slug = [problem for problem in problems if problem.slug.casefold() == text.casefold()]
    if len(exact_slug) == 1:
        return exact_slug[0]
    if len(exact_slug) > 1:
        raise AmbiguousProblemError(query, sorted(exact_slug, key=lambda problem: problem.id))

    exact_title = [
        problem for problem in problems if _normalize(problem.title) == _normalize(text)
    ]
    if len(exact_title) == 1:
        return exact_title[0]
    if len(exact_title) > 1:
        raise AmbiguousProblemError(query, sorted(exact_title, key=lambda problem: problem.id))

    needle = _normalize(text)
    needle_slug = needle.replace(" ", "-")
    partial = [
        problem
        for problem in problems
        if needle in _normalize(problem.title) or needle_slug in problem.slug.casefold()
    ]
    if len(partial) == 1:
        return partial[0]
    if not partial:
        raise ProblemNotFoundError(query)
    raise AmbiguousProblemError(query, sorted(partial, key=lambda problem: problem.id))


def log_attempt(
    conn: sqlite3.Connection,
    problem: Problem,
    *,
    outcome: str,
    minutes: int,
    notes: str | None,
    attempt_date: date,
) -> tuple[Attempt, ReviewState]:
    """Record an attempt and advance the review schedule in one transaction."""
    if minutes < 0:
        raise InvalidMinutesError(minutes)
    cleaned_notes = notes.strip() if notes and notes.strip() else None
    state = get_review_state(conn, problem.id)
    update = schedule(
        outcome=outcome,
        minutes=minutes,
        difficulty=problem.difficulty,
        attempt_date=attempt_date,
        ease_factor=state.ease_factor if state else DEFAULT_EASE_FACTOR,
        interval_days=state.interval_days if state else 0,
        repetitions=state.repetitions if state else 0,
    )
    stored_outcome = outcome.strip().lower()
    with conn:
        cursor = conn.execute(
            """
            INSERT INTO attempts (problem_id, date, minutes, outcome, notes)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                problem.id,
                attempt_date.isoformat(),
                minutes,
                stored_outcome,
                cleaned_notes,
            ),
        )
        attempt_id = int(cursor.lastrowid)
        conn.execute(
            """
            INSERT INTO review_state (
                problem_id, ease_factor, interval_days, repetitions, next_review_date
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(problem_id) DO UPDATE SET
                ease_factor = excluded.ease_factor,
                interval_days = excluded.interval_days,
                repetitions = excluded.repetitions,
                next_review_date = excluded.next_review_date
            """,
            (
                problem.id,
                update.ease_factor,
                update.interval_days,
                update.repetitions,
                update.next_review_date.isoformat(),
            ),
        )
    attempt = Attempt(
        id=attempt_id,
        problem_id=problem.id,
        date=attempt_date,
        minutes=minutes,
        outcome=stored_outcome,
        notes=cleaned_notes,
    )
    review = ReviewState(
        problem_id=problem.id,
        ease_factor=update.ease_factor,
        interval_days=update.interval_days,
        repetitions=update.repetitions,
        next_review_date=update.next_review_date,
    )
    return attempt, review


def get_review_state(conn: sqlite3.Connection, problem_id: int) -> ReviewState | None:
    row = conn.execute(
        "SELECT * FROM review_state WHERE problem_id = ?", (problem_id,)
    ).fetchone()
    if row is None:
        return None
    return ReviewState(
        problem_id=row["problem_id"],
        ease_factor=row["ease_factor"],
        interval_days=row["interval_days"],
        repetitions=row["repetitions"],
        next_review_date=date.fromisoformat(row["next_review_date"]),
    )


def get_notes(conn: sqlite3.Connection, problem_id: int) -> ProblemNotes | None:
    row = conn.execute(
        "SELECT * FROM problem_notes WHERE problem_id = ?", (problem_id,)
    ).fetchone()
    if row is None:
        return None
    return _notes_from_row(row)


def save_notes(
    conn: sqlite3.Connection,
    problem_id: int,
    *,
    key_insight: str | None,
    time_complexity: str | None,
    space_complexity: str | None,
    gotchas: str | None,
) -> ProblemNotes:
    notes = ProblemNotes(
        problem_id=problem_id,
        key_insight=_blank_to_none(key_insight),
        time_complexity=_blank_to_none(time_complexity),
        space_complexity=_blank_to_none(space_complexity),
        gotchas=_blank_to_none(gotchas),
    )
    with conn:
        conn.execute(
            """
            INSERT INTO problem_notes (
                problem_id, key_insight, time_complexity, space_complexity, gotchas
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(problem_id) DO UPDATE SET
                key_insight = excluded.key_insight,
                time_complexity = excluded.time_complexity,
                space_complexity = excluded.space_complexity,
                gotchas = excluded.gotchas
            """,
            (
                notes.problem_id,
                notes.key_insight,
                notes.time_complexity,
                notes.space_complexity,
                notes.gotchas,
            ),
        )
    return notes


def get_attempts(conn: sqlite3.Connection, problem_id: int) -> list[Attempt]:
    rows = conn.execute(
        """
        SELECT * FROM attempts
        WHERE problem_id = ?
        ORDER BY date ASC, id ASC
        """,
        (problem_id,),
    ).fetchall()
    return [
        Attempt(
            id=row["id"],
            problem_id=row["problem_id"],
            date=date.fromisoformat(row["date"]),
            minutes=row["minutes"],
            outcome=row["outcome"],
            notes=row["notes"],
        )
        for row in rows
    ]


def load_practice_records(conn: sqlite3.Connection, *, today: date) -> list[PracticeRecord]:
    """Every problem with solved/due flags and its latest attempt, if any."""
    solved_placeholders = ", ".join("?" for _ in SOLVED_OUTCOMES)
    solved_ids = {
        row["problem_id"]
        for row in conn.execute(
            f"""
            SELECT DISTINCT problem_id FROM attempts
            WHERE outcome IN ({solved_placeholders})
            """,
            SOLVED_OUTCOMES,
        )
    }
    latest: dict[int, sqlite3.Row] = {}
    for row in conn.execute("SELECT * FROM attempts ORDER BY date DESC, id DESC"):
        latest.setdefault(row["problem_id"], row)
    reviews = {
        row["problem_id"]: date.fromisoformat(row["next_review_date"])
        for row in conn.execute("SELECT problem_id, next_review_date FROM review_state")
    }
    records: list[PracticeRecord] = []
    for row in conn.execute("SELECT * FROM problems"):
        problem = _problem_from_row(row)
        attempt = latest.get(problem.id)
        next_review = reviews.get(problem.id)
        records.append(
            PracticeRecord(
                problem=problem,
                solved=problem.id in solved_ids,
                due=next_review is not None and next_review <= today,
                latest_outcome=attempt["outcome"] if attempt is not None else None,
                latest_minutes=attempt["minutes"] if attempt is not None else None,
                next_review_date=next_review,
            )
        )
    return records


def list_attempt_dates(conn: sqlite3.Connection) -> list[date]:
    rows = conn.execute("SELECT date FROM attempts").fetchall()
    return [date.fromisoformat(row["date"]) for row in rows]


def list_review_dates(conn: sqlite3.Connection) -> list[date]:
    rows = conn.execute("SELECT next_review_date FROM review_state").fetchall()
    return [date.fromisoformat(row["next_review_date"]) for row in rows]


def get_problem_detail(
    conn: sqlite3.Connection, problem: Problem, *, today: date
) -> ProblemDetail:
    attempts = get_attempts(conn, problem.id)
    review = get_review_state(conn, problem.id)
    solved = any(attempt.outcome in SOLVED_OUTCOMES for attempt in attempts)
    due = review is not None and review.next_review_date <= today
    return ProblemDetail(
        problem=problem,
        notes=get_notes(conn, problem.id),
        attempts=attempts,
        review=review,
        status=_display_status(solved, due),
    )


def export_payload(conn: sqlite3.Connection) -> dict:
    """Snapshot problems, attempts, notes, and review state for export."""
    problems = [
        {
            "id": row["id"],
            "slug": row["slug"],
            "title": row["title"],
            "difficulty": row["difficulty"],
            "category": row["category"],
            "leetcode_url": row["leetcode_url"],
            "list_name": row["list_name"],
        }
        for row in conn.execute("SELECT * FROM problems ORDER BY id")
    ]
    attempts = [
        {
            "id": row["id"],
            "problem_id": row["problem_id"],
            "date": row["date"],
            "minutes": row["minutes"],
            "outcome": row["outcome"],
            "notes": row["notes"],
        }
        for row in conn.execute("SELECT * FROM attempts ORDER BY id")
    ]
    notes = [
        {
            "problem_id": row["problem_id"],
            "key_insight": row["key_insight"],
            "time_complexity": row["time_complexity"],
            "space_complexity": row["space_complexity"],
            "gotchas": row["gotchas"],
        }
        for row in conn.execute("SELECT * FROM problem_notes ORDER BY problem_id")
    ]
    reviews = [
        {
            "problem_id": row["problem_id"],
            "ease_factor": row["ease_factor"],
            "interval_days": row["interval_days"],
            "repetitions": row["repetitions"],
            "next_review_date": row["next_review_date"],
        }
        for row in conn.execute("SELECT * FROM review_state ORDER BY problem_id")
    ]
    return {
        "version": EXPORT_VERSION,
        "problems": problems,
        "attempts": attempts,
        "notes": notes,
        "review_state": reviews,
    }


def dumps_json(payload: dict) -> str:
    return json.dumps(payload, indent=2) + "\n"


def dumps_csv_files(payload: dict) -> dict[str, str]:
    """Return CSV text keyed by file name."""
    data = _require_export_lists(payload)
    return {
        CSV_FILES["problems"]: _csv_text(_PROBLEM_FIELDS, data["problems"]),
        CSV_FILES["attempts"]: _csv_text(_ATTEMPT_FIELDS, data["attempts"]),
        CSV_FILES["notes"]: _csv_text(_NOTE_FIELDS, data["notes"]),
        CSV_FILES["review_state"]: _csv_text(_REVIEW_FIELDS, data["review_state"]),
    }


def parse_export_json(text: str) -> dict:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ExportDataError(f"Could not read JSON export: {exc.msg}.") from exc
    if not isinstance(payload, dict):
        raise ExportDataError("JSON export must be an object.")
    _require_export_lists(payload)
    return payload


def restore_export(conn: sqlite3.Connection, payload: dict) -> ImportResult:
    """Replace attempts, notes, and review state for problems in the export.

    Problems already in the database are updated. Other problems are left in place.
    """
    data = _validated_export(payload)
    problem_ids = [problem["id"] for problem in data["problems"]]
    with conn:
        for problem in data["problems"]:
            conn.execute(
                """
                INSERT INTO problems (
                    id, slug, title, difficulty, category, leetcode_url, list_name
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    slug = excluded.slug,
                    title = excluded.title,
                    difficulty = excluded.difficulty,
                    category = excluded.category,
                    leetcode_url = excluded.leetcode_url,
                    list_name = excluded.list_name
                """,
                (
                    problem["id"],
                    problem["slug"],
                    problem["title"],
                    problem["difficulty"],
                    problem["category"],
                    problem["leetcode_url"],
                    problem["list_name"],
                ),
            )
        if problem_ids:
            placeholders = ", ".join("?" for _ in problem_ids)
            for table in ("attempts", "problem_notes", "review_state"):
                conn.execute(
                    f"DELETE FROM {table} WHERE problem_id IN ({placeholders})",
                    problem_ids,
                )
        conn.executemany(
            """
            INSERT INTO attempts (id, problem_id, date, minutes, outcome, notes)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    attempt["id"],
                    attempt["problem_id"],
                    attempt["date"],
                    attempt["minutes"],
                    attempt["outcome"],
                    attempt["notes"],
                )
                for attempt in data["attempts"]
            ],
        )
        conn.executemany(
            """
            INSERT INTO problem_notes (
                problem_id, key_insight, time_complexity, space_complexity, gotchas
            ) VALUES (?, ?, ?, ?, ?)
            """,
            [
                (
                    note["problem_id"],
                    note["key_insight"],
                    note["time_complexity"],
                    note["space_complexity"],
                    note["gotchas"],
                )
                for note in data["notes"]
            ],
        )
        conn.executemany(
            """
            INSERT INTO review_state (
                problem_id, ease_factor, interval_days, repetitions, next_review_date
            ) VALUES (?, ?, ?, ?, ?)
            """,
            [
                (
                    review["problem_id"],
                    review["ease_factor"],
                    review["interval_days"],
                    review["repetitions"],
                    review["next_review_date"],
                )
                for review in data["review_state"]
            ],
        )
        _sync_attempt_sequence(conn)
    return ImportResult(
        problems=len(data["problems"]),
        attempts=len(data["attempts"]),
        notes=len(data["notes"]),
        reviews=len(data["review_state"]),
    )


def connect_for_import(path: Path) -> sqlite3.Connection:
    """Open a database for restore, creating the file and schema if needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'problems'"
    ).fetchone()
    if row is None:
        create_schema(conn)
        conn.commit()
    return conn


def normalize_export_format(fmt: str) -> str:
    value = fmt.strip().lower()
    if value not in {"csv", "json"}:
        raise ExportFormatError(fmt)
    return value


def _require_export_lists(payload: dict) -> dict:
    if not isinstance(payload, dict):
        raise ExportDataError("JSON export must be an object.")
    version = payload.get("version", EXPORT_VERSION)
    if version != EXPORT_VERSION:
        raise ExportDataError(
            f"Unsupported export version {version!r}. Expected {EXPORT_VERSION}."
        )
    normalized = {"version": EXPORT_VERSION}
    for key in ("problems", "attempts", "notes", "review_state"):
        rows = payload.get(key)
        if rows is None:
            raise ExportDataError(f"Export is missing {key!r}.")
        if not isinstance(rows, list):
            raise ExportDataError(f"Export field {key!r} must be a list.")
        normalized[key] = rows
    return normalized


def _validated_export(payload: dict) -> dict:
    data = _require_export_lists(payload)
    problems = []
    problem_ids: set[int] = set()
    for index, raw in enumerate(data["problems"], start=1):
        problem = _require_fields(raw, _PROBLEM_FIELDS, f"Problem {index}")
        problem_id = _as_int(problem["id"], f"Problem {index} id")
        if problem_id in problem_ids:
            raise ExportDataError(f"Duplicate problem id {problem_id}.")
        problem_ids.add(problem_id)
        difficulty = str(problem["difficulty"])
        if difficulty not in DIFFICULTIES:
            raise ExportDataError(
                f"Problem {problem_id} has invalid difficulty {difficulty!r}. "
                "Expected one of: Easy, Medium, Hard."
            )
        problems.append(
            {
                "id": problem_id,
                "slug": _required_text(problem["slug"], f"Problem {problem_id} slug"),
                "title": _required_text(problem["title"], f"Problem {problem_id} title"),
                "difficulty": difficulty,
                "category": _required_text(problem["category"], f"Problem {problem_id} category"),
                "leetcode_url": _required_text(
                    problem["leetcode_url"], f"Problem {problem_id} leetcode_url"
                ),
                "list_name": _required_text(problem["list_name"], f"Problem {problem_id} list_name"),
            }
        )

    attempts = []
    attempt_ids: set[int] = set()
    for index, raw in enumerate(data["attempts"], start=1):
        attempt = _require_fields(raw, _ATTEMPT_FIELDS, f"Attempt {index}")
        attempt_id = _as_int(attempt["id"], f"Attempt {index} id")
        if attempt_id in attempt_ids:
            raise ExportDataError(f"Duplicate attempt id {attempt_id}.")
        attempt_ids.add(attempt_id)
        problem_id = _as_int(attempt["problem_id"], f"Attempt {attempt_id} problem_id")
        if problem_id not in problem_ids:
            raise ExportDataError(
                f"Attempt {attempt_id} refers to unknown problem {problem_id}."
            )
        outcome = str(attempt["outcome"]).strip().lower()
        if outcome not in {"cold", "hint", "solution", "failed"}:
            raise ExportDataError(
                f"Attempt {attempt_id} has invalid outcome {attempt['outcome']!r}. "
                "Expected one of: cold, hint, solution, failed."
            )
        minutes = _as_int(attempt["minutes"], f"Attempt {attempt_id} minutes")
        if minutes < 0:
            raise ExportDataError(
                f"Attempt {attempt_id} minutes must be zero or greater, got {minutes}."
            )
        attempt_date = _as_date(attempt["date"], f"Attempt {attempt_id} date")
        attempts.append(
            {
                "id": attempt_id,
                "problem_id": problem_id,
                "date": attempt_date.isoformat(),
                "minutes": minutes,
                "outcome": outcome,
                "notes": _optional_text(attempt["notes"]),
            }
        )

    notes = []
    note_ids: set[int] = set()
    for index, raw in enumerate(data["notes"], start=1):
        note = _require_fields(raw, _NOTE_FIELDS, f"Note {index}")
        problem_id = _as_int(note["problem_id"], f"Note {index} problem_id")
        if problem_id in note_ids:
            raise ExportDataError(f"Duplicate notes for problem {problem_id}.")
        note_ids.add(problem_id)
        if problem_id not in problem_ids:
            raise ExportDataError(f"Notes refer to unknown problem {problem_id}.")
        notes.append(
            {
                "problem_id": problem_id,
                "key_insight": _optional_text(note["key_insight"]),
                "time_complexity": _optional_text(note["time_complexity"]),
                "space_complexity": _optional_text(note["space_complexity"]),
                "gotchas": _optional_text(note["gotchas"]),
            }
        )

    reviews = []
    review_ids: set[int] = set()
    for index, raw in enumerate(data["review_state"], start=1):
        review = _require_fields(raw, _REVIEW_FIELDS, f"Review {index}")
        problem_id = _as_int(review["problem_id"], f"Review {index} problem_id")
        if problem_id in review_ids:
            raise ExportDataError(f"Duplicate review state for problem {problem_id}.")
        review_ids.add(problem_id)
        if problem_id not in problem_ids:
            raise ExportDataError(f"Review state refers to unknown problem {problem_id}.")
        reviews.append(
            {
                "problem_id": problem_id,
                "ease_factor": _as_float(review["ease_factor"], f"Review {problem_id} ease_factor"),
                "interval_days": _as_int(
                    review["interval_days"], f"Review {problem_id} interval_days"
                ),
                "repetitions": _as_int(review["repetitions"], f"Review {problem_id} repetitions"),
                "next_review_date": _as_date(
                    review["next_review_date"], f"Review {problem_id} next_review_date"
                ).isoformat(),
            }
        )
    return {
        "version": EXPORT_VERSION,
        "problems": problems,
        "attempts": attempts,
        "notes": notes,
        "review_state": reviews,
    }


def _csv_text(fieldnames: tuple[str, ...], rows: list[dict]) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=fieldnames, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({key: "" if row.get(key) is None else row.get(key) for key in fieldnames})
    return buffer.getvalue()


def _require_fields(raw: object, fields: tuple[str, ...], label: str) -> dict:
    if not isinstance(raw, dict):
        raise ExportDataError(f"{label} must be an object.")
    missing = [field for field in fields if field not in raw]
    if missing:
        joined = ", ".join(missing)
        raise ExportDataError(f"{label} is missing {joined}.")
    return raw


def _as_int(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ExportDataError(f"{label} must be an integer.")
    return value


def _as_float(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ExportDataError(f"{label} must be a number.")
    return float(value)


def _as_date(value: object, label: str) -> date:
    if not isinstance(value, str):
        raise ExportDataError(f"{label} must be a YYYY-MM-DD string.")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ExportDataError(f"{label} must be a YYYY-MM-DD string.") from exc


def _required_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ExportDataError(f"{label} is required.")
    return value


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ExportDataError("Optional text fields must be strings or null.")
    stripped = value.strip()
    return stripped or None


def _sync_attempt_sequence(conn: sqlite3.Connection) -> None:
    max_id = conn.execute("SELECT MAX(id) FROM attempts").fetchone()[0]
    if max_id is None:
        return
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'sqlite_sequence'"
    ).fetchone()
    if row is None:
        return
    existing = conn.execute(
        "SELECT seq FROM sqlite_sequence WHERE name = 'attempts'"
    ).fetchone()
    if existing is None:
        conn.execute(
            "INSERT INTO sqlite_sequence (name, seq) VALUES ('attempts', ?)",
            (max_id,),
        )
        return
    conn.execute(
        "UPDATE sqlite_sequence SET seq = ? WHERE name = 'attempts'",
        (max(max_id, int(existing["seq"])),),
    )


def _display_status(solved: bool, due: bool) -> str:
    if due:
        return "due"
    if solved:
        return "solved"
    return "unsolved"


def _canonical_difficulty(difficulty: str) -> str:
    for name in DIFFICULTIES:
        if name.casefold() == difficulty.strip().casefold():
            return name
    raise InvalidDifficultyError(difficulty)


def _canonical_status(status: str) -> str:
    for name in STATUSES:
        if name.casefold() == status.strip().casefold():
            return name
    raise InvalidStatusError(status)


def _known_categories(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute("SELECT DISTINCT category FROM problems").fetchall()
    found = {row["category"] for row in rows}
    ordered = [name for name in CATEGORY_ORDER if name in found]
    ordered.extend(sorted(found.difference(CATEGORY_ORDER)))
    return ordered


def _match_category(category: str, known: list[str]) -> str:
    for name in known:
        if name.casefold() == category.strip().casefold():
            return name
    raise UnknownCategoryError(category, known)


def _problem_from_row(row: sqlite3.Row) -> Problem:
    return Problem(
        id=row["id"],
        slug=row["slug"],
        title=row["title"],
        difficulty=row["difficulty"],
        category=row["category"],
        leetcode_url=row["leetcode_url"],
        list_name=row["list_name"],
    )


def _notes_from_row(row: sqlite3.Row) -> ProblemNotes:
    return ProblemNotes(
        problem_id=row["problem_id"],
        key_insight=row["key_insight"],
        time_complexity=row["time_complexity"],
        space_complexity=row["space_complexity"],
        gotchas=row["gotchas"],
    )


def _parse_date(value: str | None) -> date | None:
    if value is None:
        return None
    return date.fromisoformat(value)


def _normalize(text: str) -> str:
    return " ".join(text.casefold().split())


def _blank_to_none(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None
