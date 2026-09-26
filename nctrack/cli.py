"""Commands for tracking practice, reviews, stats, and backups."""

import sys
import threading
import time
from datetime import date
from pathlib import Path
from random import Random
from typing import Annotated, Optional

import typer
from rich.console import Console
from rich.live import Live
from rich.table import Table
from rich.text import Text

from nctrack.db import (
    AmbiguousProblemError,
    DatabaseNotInitializedError,
    ExportDataError,
    ExportFormatError,
    InvalidDifficultyError,
    InvalidMinutesError,
    InvalidStatusError,
    ProblemNotFoundError,
    UnknownCategoryError,
    connect,
    connect_for_import,
    due_problems,
    dumps_csv_files,
    dumps_json,
    export_payload,
    get_notes,
    get_problem_detail,
    initialize,
    list_attempt_dates,
    list_problems,
    list_review_dates,
    load_practice_records,
    log_attempt,
    lookup_problem,
    normalize_export_format,
    parse_export_json,
    resolve_db_path,
    restore_export,
    save_notes,
)
from nctrack.models import (
    OUTCOMES,
    TARGET_MINUTES,
    AttemptSnapshot,
    CalendarWeek,
    CategoryStats,
    Problem,
    ProblemNotes,
    ProblemSummary,
    StudyPlan,
)
from nctrack.scheduler import InvalidOutcomeError, quality_for
from nctrack.stats import (
    InterviewDateError,
    InvalidPlanDateError,
    activity_calendar,
    build_plan,
    category_stats,
    choose_weighted,
    elapsed_minutes,
    mock_weight,
    parse_plan_date,
)

app = typer.Typer(
    help="Track NeetCode practice and spaced-repetition reviews.",
    no_args_is_help=True,
)
DbOption = Annotated[
    Optional[Path],
    typer.Option("--db", help="SQLite database path. Overrides NCTRACK_DB."),
]


def get_console() -> Console:
    return Console()


def fail(message: str) -> None:
    get_console().print(message, style="bold red")
    raise typer.Exit(code=1)


def open_db(explicit: Path | None):
    path = resolve_db_path(explicit)
    try:
        return connect(path)
    except DatabaseNotInitializedError as exc:
        fail(str(exc))


def resolve_query(conn, query: str) -> Problem:
    try:
        return lookup_problem(conn, query)
    except ProblemNotFoundError as exc:
        fail(str(exc))
    except AmbiguousProblemError as exc:
        return prompt_disambiguate(exc.matches)


def prompt_disambiguate(matches: list[Problem]) -> Problem:
    console = get_console()
    console.print("Multiple problems match. Choose one:")
    for index, problem in enumerate(matches, start=1):
        console.print(f"  {index}) [{problem.id}] {problem.title} ({problem.slug})")
    raw = typer.prompt("Number")
    if not str(raw).isdigit() or not 1 <= int(raw) <= len(matches):
        fail("Invalid selection.")
    return matches[int(raw) - 1]


@app.command()
def init(db: DbOption = None) -> None:
    """Create the database and load the NeetCode 150 seed data."""
    path = resolve_db_path(db)
    try:
        result = initialize(path)
    except (FileNotFoundError, ValueError, OSError) as exc:
        fail(str(exc))
    console = get_console()
    if result.added:
        console.print(
            f"Initialized {result.path} with {result.added} new problems "
            f"({result.total} total)."
        )
    else:
        console.print(
            f"Database ready at {result.path}. "
            f"No new problems added ({result.total} total)."
        )


@app.command(name="list")
def list_cmd(
    category: Annotated[Optional[str], typer.Option("--category", help="Roadmap category.")] = None,
    status: Annotated[
        Optional[str],
        typer.Option("--status", help="unsolved, solved, or due."),
    ] = None,
    difficulty: Annotated[
        Optional[str],
        typer.Option("--difficulty", help="Easy, Medium, or Hard."),
    ] = None,
    db: DbOption = None,
) -> None:
    """Show problems with status, attempt counts, and the next review date."""
    conn = open_db(db)
    try:
        rows = list_problems(
            conn,
            today=date.today(),
            category=category,
            status=status,
            difficulty=difficulty,
        )
    except (UnknownCategoryError, InvalidDifficultyError, InvalidStatusError) as exc:
        fail(str(exc))
    finally:
        conn.close()
    if not rows:
        get_console().print("No problems match.")
        return
    _print_problem_table(rows, title="Problems")


@app.command()
def log(
    query: Annotated[str, typer.Argument(help="Problem id, slug, or title.")],
    outcome: Annotated[Optional[str], typer.Option("--outcome", help="cold, hint, solution, or failed.")] = None,
    minutes: Annotated[Optional[int], typer.Option("--minutes", help="Minutes spent.")] = None,
    notes: Annotated[Optional[str], typer.Option("--notes", help="Optional attempt notes.")] = None,
    db: DbOption = None,
) -> None:
    """Record an attempt and update the review schedule."""
    conn = open_db(db)
    try:
        problem = resolve_query(conn, query)
        interactive = outcome is None or minutes is None
        if outcome is None:
            outcome = typer.prompt("Outcome (cold, hint, solution, failed)")
        if minutes is None:
            minutes = typer.prompt("Minutes", type=int)
        if interactive and notes is None:
            notes = typer.prompt("Notes", default="", show_default=False)
        attempt, review = log_attempt(
            conn,
            problem,
            outcome=outcome,
            minutes=minutes,
            notes=notes,
            attempt_date=date.today(),
        )
    except (InvalidOutcomeError, InvalidMinutesError) as exc:
        fail(str(exc))
    finally:
        conn.close()
    _print_logged(problem, attempt.outcome, attempt.minutes, review)


@app.command()
def show(
    query: Annotated[str, typer.Argument(help="Problem id, slug, or title.")],
    db: DbOption = None,
) -> None:
    """Show problem info, notes, attempt history, and the next review date."""
    conn = open_db(db)
    try:
        problem = resolve_query(conn, query)
        detail = get_problem_detail(conn, problem, today=date.today())
    finally:
        conn.close()
    console = get_console()
    problem = detail.problem
    console.print(f"[bold]{problem.title}[/bold] ({problem.difficulty}) #{problem.id}")
    console.print(f"Slug: {problem.slug}")
    console.print(f"Category: {problem.category}")
    console.print(f"List: {problem.list_name}")
    console.print(f"URL: {problem.leetcode_url}")
    console.print(f"Status: {detail.status}")
    if detail.review is None:
        console.print("Next review: —")
    else:
        review = detail.review
        console.print(
            "Next review: "
            f"{review.next_review_date.isoformat()} "
            f"(ease {review.ease_factor:.2f}, interval {review.interval_days}d, "
            f"repetitions {review.repetitions})"
        )
    console.print()
    _print_notes(detail.notes)
    console.print()
    if not detail.attempts:
        console.print("No attempts yet.")
        return
    table = Table(title="Attempts")
    table.add_column("Date")
    table.add_column("Minutes", justify="right")
    table.add_column("Outcome")
    table.add_column("Notes")
    for attempt in detail.attempts:
        table.add_row(
            attempt.date.isoformat(),
            str(attempt.minutes),
            attempt.outcome,
            attempt.notes or "",
        )
    console.print(table)


@app.command()
def note(
    query: Annotated[str, typer.Argument(help="Problem id, slug, or title.")],
    db: DbOption = None,
) -> None:
    """Edit the key insight, complexities, and gotchas for a problem."""
    conn = open_db(db)
    try:
        problem = resolve_query(conn, query)
        detail = get_problem_detail(conn, problem, today=date.today())
        current = detail.notes or ProblemNotes(problem.id, None, None, None, None)
        key_insight = typer.prompt("Key insight", default=current.key_insight or "")
        time_complexity = typer.prompt("Time complexity", default=current.time_complexity or "")
        space_complexity = typer.prompt("Space complexity", default=current.space_complexity or "")
        gotchas = typer.prompt("Gotchas", default=current.gotchas or "")
        save_notes(
            conn,
            problem.id,
            key_insight=key_insight,
            time_complexity=time_complexity,
            space_complexity=space_complexity,
            gotchas=gotchas,
        )
    finally:
        conn.close()
    get_console().print(f"Saved notes for {problem.title}.")


@app.command()
def due(db: DbOption = None) -> None:
    """List problems due for review, oldest next-review date first."""
    conn = open_db(db)
    try:
        rows = due_problems(conn, today=date.today())
    finally:
        conn.close()
    if not rows:
        get_console().print("No problems are due.")
        return
    _print_problem_table(rows, title="Due")


@app.command()
def start(
    query: Annotated[str, typer.Argument(help="Problem id, slug, or title.")],
    db: DbOption = None,
) -> None:
    """Start a timer, then log the attempt when you press Enter."""
    conn = open_db(db)
    try:
        problem = resolve_query(conn, query)
        console = get_console()
        console.print(f"Solving [bold]{problem.title}[/bold] ({problem.difficulty}).")
        minutes = run_timer(console, limit_minutes=None)
        console.print(f"Elapsed: {minutes} min.")
        outcome = _prompt_outcome()
        attempt, review = log_attempt(
            conn,
            problem,
            outcome=outcome,
            minutes=minutes,
            notes=None,
            attempt_date=date.today(),
        )
    finally:
        conn.close()
    _print_logged(problem, attempt.outcome, attempt.minutes, review)


@app.command()
def stats(db: DbOption = None) -> None:
    """Show per-category solve rate, cold-solve rate, and minutes vs target."""
    conn = open_db(db)
    try:
        records = load_practice_records(conn, today=date.today())
    finally:
        conn.close()
    rows = category_stats(_snapshots(records))
    if not rows:
        get_console().print("No problems to summarize.")
        return
    _print_stats(rows)


@app.command()
def mock(
    minutes: Annotated[
        Optional[int],
        typer.Option("--minutes", help="Time limit in minutes. Defaults to the difficulty target."),
    ] = None,
    db: DbOption = None,
) -> None:
    """Time a random problem, hide notes, and require complexity before logging."""
    if minutes is not None and minutes < 1:
        fail("Minutes must be greater than zero.")
    conn = open_db(db)
    try:
        today = date.today()
        records = load_practice_records(conn, today=today)
        if not records:
            fail("No problems to practice.")
        rows = category_stats(_snapshots(records))
        weakness = {row.category: row.weakness for row in rows}
        weights = [
            mock_weight(
                category=record.problem.category,
                solved=record.solved,
                due=record.due,
                weakness_by_category=weakness,
            )
            for record in records
        ]
        chosen_id = choose_weighted(
            [record.problem.id for record in records],
            weights,
            rng=Random(),
        )
        record = next(item for item in records if item.problem.id == chosen_id)
        problem = record.problem
        limit = minutes if minutes is not None else TARGET_MINUTES[problem.difficulty]
        console = get_console()
        console.print(f"[bold]{problem.title}[/bold] ({problem.difficulty})")
        console.print(f"Category: {problem.category}")
        console.print(f"URL: {problem.leetcode_url}")
        console.print("Notes are hidden.")
        spent = run_timer(console, limit_minutes=limit)
        console.print(f"Elapsed: {spent} min.")
        time_complexity = _prompt_required("Time complexity")
        space_complexity = _prompt_required("Space complexity")
        outcome = _prompt_outcome()
        attempt, review = log_attempt(
            conn,
            problem,
            outcome=outcome,
            minutes=spent,
            notes=None,
            attempt_date=today,
        )
        existing = get_notes(conn, problem.id)
        save_notes(
            conn,
            problem.id,
            key_insight=existing.key_insight if existing else None,
            time_complexity=time_complexity,
            space_complexity=space_complexity,
            gotchas=existing.gotchas if existing else None,
        )
    finally:
        conn.close()
    _print_logged(problem, attempt.outcome, attempt.minutes, review)
    get_console().print(f"Saved complexity: {time_complexity} time, {space_complexity} space.")


@app.command()
def plan(
    interview: Annotated[str, typer.Option("--date", help="Interview date, YYYY-MM-DD.")],
    db: DbOption = None,
) -> None:
    """Show new problems per day until an interview, plus projected reviews."""
    try:
        interview_date = parse_plan_date(interview)
    except InvalidPlanDateError as exc:
        fail(str(exc))
    conn = open_db(db)
    try:
        today = date.today()
        records = load_practice_records(conn, today=today)
        study_plan = build_plan(
            today=today,
            interview_date=interview_date,
            total_problems=len(records),
            unsolved=sum(1 for record in records if not record.solved),
            review_dates=list_review_dates(conn),
        )
    except InterviewDateError as exc:
        fail(str(exc))
    finally:
        conn.close()
    _print_plan(study_plan)


@app.command()
def calendar(db: DbOption = None) -> None:
    """Show a GitHub-style heatmap of attempts over the last 12 weeks."""
    conn = open_db(db)
    try:
        dates = list_attempt_dates(conn)
    finally:
        conn.close()
    weeks = activity_calendar(dates, today=date.today())
    _print_calendar(weeks)


@app.command()
def export(
    fmt: Annotated[str, typer.Option("--format", help="csv or json.")],
    out: Annotated[
        Optional[Path],
        typer.Option("--out", help="JSON file, or a directory for CSV files."),
    ] = None,
    db: DbOption = None,
) -> None:
    """Export problems, attempts, notes, and review state."""
    try:
        chosen = normalize_export_format(fmt)
    except ExportFormatError as exc:
        fail(str(exc))
    conn = open_db(db)
    try:
        payload = export_payload(conn)
    finally:
        conn.close()
    if chosen == "json":
        text = dumps_json(payload)
        if out is None:
            sys.stdout.write(text)
            return
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
        get_console().print(f"Wrote {out}.")
        return
    directory = Path.cwd() if out is None else out
    directory.mkdir(parents=True, exist_ok=True)
    written = []
    for name, text in dumps_csv_files(payload).items():
        path = directory / name
        path.write_text(text, encoding="utf-8")
        written.append(str(path))
    get_console().print("Wrote " + ", ".join(written) + ".")


@app.command(name="import")
def import_cmd(
    file: Annotated[Path, typer.Argument(help="JSON export to restore.")],
    db: DbOption = None,
) -> None:
    """Restore problems, attempts, notes, and review state from a JSON export."""
    if file.suffix.lower() == ".csv":
        fail("Import expects a JSON export. CSV files cannot be restored.")
    if not file.is_file():
        fail(f"Export file not found at {file}.")
    try:
        payload = parse_export_json(file.read_text(encoding="utf-8"))
    except ExportDataError as exc:
        fail(str(exc))
    except OSError as exc:
        fail(f"Could not read {file}: {exc}.")
    path = resolve_db_path(db)
    conn = connect_for_import(path)
    try:
        result = restore_export(conn, payload)
    except ExportDataError as exc:
        fail(str(exc))
    finally:
        conn.close()
    get_console().print(
        "Restored "
        f"{_count(result.problems, 'problem')}, "
        f"{_count(result.attempts, 'attempt')}, "
        f"{_count(result.notes, 'note')}, and "
        f"{_count(result.reviews, 'review state')} into {path}."
    )


def _count(amount: int, singular: str) -> str:
    if amount == 1:
        return f"1 {singular}"
    return f"{amount} {singular}s"


def run_timer(console: Console, *, limit_minutes: int | None) -> int:
    """Run until Enter. A time limit also stops the timer when stdout is a TTY."""
    if limit_minutes is None:
        console.print("Press Enter to stop.")
    else:
        console.print(f"Time limit: {limit_minutes} min. Press Enter to stop.")
    start = time.monotonic()
    if sys.stdin.isatty() and sys.stdout.isatty():
        _live_wait(console, start, limit_minutes)
    else:
        input()
    return elapsed_minutes(time.monotonic() - start)


def _live_wait(console: Console, start: float, limit_minutes: int | None) -> None:
    stop = threading.Event()

    def wait_for_enter() -> None:
        input()
        stop.set()

    thread = threading.Thread(target=wait_for_enter, daemon=True)
    thread.start()
    limit_seconds = None if limit_minutes is None else limit_minutes * 60
    with Live("", console=console, refresh_per_second=4) as live:
        while not stop.is_set():
            seconds = time.monotonic() - start
            if limit_seconds is not None and seconds >= limit_seconds:
                stop.set()
                break
            live.update(Text(f"{_format_clock(seconds)}  (Enter to stop)", style="bold"))
            time.sleep(0.25)


def _format_clock(seconds: float) -> str:
    whole = max(0, int(seconds))
    hours, remainder = divmod(whole, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours:d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def _prompt_outcome() -> str:
    while True:
        outcome = typer.prompt("Outcome (cold, hint, solution, failed)")
        if outcome.strip().lower() in OUTCOMES:
            return outcome
        joined = ", ".join(OUTCOMES)
        get_console().print(
            f"Invalid outcome {outcome!r}. Expected one of: {joined}.",
            style="bold red",
        )


def _prompt_required(label: str) -> str:
    while True:
        value = typer.prompt(label).strip()
        if value:
            return value
        get_console().print(f"{label} is required.")


def _snapshots(records) -> list[AttemptSnapshot]:
    return [
        AttemptSnapshot(
            category=record.problem.category,
            difficulty=record.problem.difficulty,
            solved=record.solved,
            latest_outcome=record.latest_outcome,
            latest_minutes=record.latest_minutes,
        )
        for record in records
    ]


def _print_logged(problem: Problem, outcome: str, minutes: int, review) -> None:
    quality = quality_for(outcome, minutes, problem.difficulty)
    suffix = "day" if review.interval_days == 1 else "days"
    get_console().print(
        f"Logged {outcome} attempt ({minutes} min, quality {quality}) "
        f"for {problem.title}. Next review: {review.next_review_date.isoformat()} "
        f"({review.interval_days} {suffix})."
    )


def _print_stats(rows: list[CategoryStats]) -> None:
    table = Table(title="Category stats")
    table.add_column("Category")
    table.add_column("Solved", justify="right")
    table.add_column("Cold rate", justify="right")
    table.add_column("Avg min", justify="right")
    table.add_column("Target", justify="right")
    for row in rows:
        style = "bold yellow" if row.weakest else None
        cold = "—" if row.cold_solve_rate is None else f"{row.cold_solve_rate * 100:.0f}%"
        average = "—" if row.average_minutes is None else f"{row.average_minutes:.1f}"
        target = "—" if row.target_minutes is None else f"{row.target_minutes:.1f}"
        table.add_row(
            row.category,
            f"{row.solved}/{row.total}",
            cold,
            average,
            target,
            style=style,
        )
    console = get_console()
    console.print(table)
    names = [row.category for row in rows if row.weakest]
    console.print("Weakest: " + ", ".join(names))


def _print_plan(study_plan: StudyPlan) -> None:
    console = get_console()
    console.print(
        f"Interview {study_plan.interview_date.isoformat()} "
        f"({study_plan.days} days from {study_plan.today.isoformat()})."
    )
    console.print(
        f"Unsolved {study_plan.unsolved} of {study_plan.total_problems}. "
        f"New problems per day: {study_plan.new_per_day:.2f}."
    )
    console.print(
        f"Scheduled reviews: {study_plan.scheduled_reviews}. "
        f"Projected reviews per day: {study_plan.reviews_per_day:.2f}."
    )
    table = Table(title="Daily plan")
    table.add_column("Date")
    table.add_column("New", justify="right")
    table.add_column("Reviews", justify="right")
    for day in study_plan.days_detail:
        table.add_row(day.day.isoformat(), str(day.new_problems), str(day.reviews))
    console.print(table)


def _print_calendar(weeks: tuple[CalendarWeek, ...]) -> None:
    console = get_console()
    console.print("Last 12 weeks")
    header = Text("    ")
    previous_month = None
    for week in weeks:
        month = week.start.strftime("%b")
        label = month if month != previous_month else ""
        header.append(f"{label:<2}")
        previous_month = month
    console.print(header)
    day_names = ("Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat")
    for index, name in enumerate(day_names):
        line = Text(f"{name} ")
        for week in weeks:
            line.append(_calendar_cell(week.days[index].count))
        console.print(line)
    total = sum(
        day.count or 0
        for week in weeks
        for day in week.days
    )
    console.print(f"Attempts in view: {total}")
    legend = Text("Less ")
    legend.append("· ", style="dim")
    legend.append("■ ", style="green")
    legend.append("■ ", style="bright_green")
    legend.append("■", style="bold bright_green")
    legend.append(" More")
    console.print(legend)


def _calendar_cell(count: int | None) -> Text:
    if count is None:
        return Text("  ")
    if count <= 0:
        return Text("· ", style="dim")
    if count == 1:
        return Text("■ ", style="green")
    if count <= 3:
        return Text("■ ", style="bright_green")
    return Text("■ ", style="bold bright_green")


def _print_notes(notes: ProblemNotes | None) -> None:
    console = get_console()
    console.print("[bold]Notes[/bold]")
    if notes is None:
        console.print("No notes yet.")
        return
    console.print(f"Key insight: {notes.key_insight or '—'}")
    console.print(f"Time complexity: {notes.time_complexity or '—'}")
    console.print(f"Space complexity: {notes.space_complexity or '—'}")
    console.print(f"Gotchas: {notes.gotchas or '—'}")


def _print_problem_table(rows: list[ProblemSummary], *, title: str) -> None:
    table = Table(title=title)
    table.add_column("ID", justify="right")
    table.add_column("Title")
    table.add_column("Difficulty")
    table.add_column("Category")
    table.add_column("Status")
    table.add_column("Attempts", justify="right")
    table.add_column("Next review")
    for row in rows:
        table.add_row(
            str(row.id),
            row.title,
            row.difficulty,
            row.category,
            row.status,
            str(row.attempt_count),
            row.next_review_date.isoformat() if row.next_review_date else "—",
        )
    get_console().print(table)
