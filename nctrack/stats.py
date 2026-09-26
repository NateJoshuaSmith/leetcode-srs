"""Pure stats, study-plan, calendar, and mock-selection helpers.

Callers pass ``today``. Nothing in this module reads the system clock.
"""

from collections.abc import Mapping, Sequence
from datetime import date, timedelta
from random import Random

from nctrack.models import (
    CATEGORY_ORDER,
    TARGET_MINUTES,
    AttemptSnapshot,
    CalendarDay,
    CalendarWeek,
    CategoryStats,
    DayPlan,
    StudyPlan,
)


class InterviewDateError(ValueError):
    def __init__(self, interview_date: date, today: date) -> None:
        self.interview_date = interview_date
        self.today = today
        super().__init__(
            "Interview date "
            f"{interview_date.isoformat()} must be after today ({today.isoformat()})."
        )


class InvalidPlanDateError(ValueError):
    def __init__(self, value: str) -> None:
        self.value = value
        super().__init__(f"Invalid date {value!r}. Expected YYYY-MM-DD.")


def parse_plan_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise InvalidPlanDateError(value) from exc


def category_stats(snapshots: Sequence[AttemptSnapshot]) -> list[CategoryStats]:
    """Per-category solved totals, cold-solve rate, and minutes vs target.

    Cold-solve rate and average minutes use each problem's latest attempt only.
    Problems with no attempts stay in the solved/total count and out of the
    rate and average. The three highest weakness scores are marked ``weakest``.
    Ties go to the category with more unsolved problems, then earlier in the roadmap.
    """
    grouped: dict[str, list[AttemptSnapshot]] = {}
    for snapshot in snapshots:
        grouped.setdefault(snapshot.category, []).append(snapshot)

    computed = [_category_row(category, items) for category, items in grouped.items()]
    weakest = _weakest_names(computed)
    marked = [
        CategoryStats(
            category=row.category,
            solved=row.solved,
            total=row.total,
            attempted=row.attempted,
            cold_solves=row.cold_solves,
            cold_solve_rate=row.cold_solve_rate,
            average_minutes=row.average_minutes,
            target_minutes=row.target_minutes,
            weakness=row.weakness,
            weakest=row.category in weakest,
        )
        for row in computed
    ]
    order = _category_index()
    marked.sort(key=lambda row: (order.get(row.category, len(order)), row.category))
    return marked


def build_plan(
    *,
    today: date,
    interview_date: date,
    total_problems: int,
    unsolved: int,
    review_dates: Sequence[date],
) -> StudyPlan:
    """New problems per day to finish the list, plus reviews already scheduled.

    The practice window is today through the day before the interview.
    Overdue reviews are placed on today. Reviews on or after the interview
    date are left out. Whole new problems are spread across the window so
    they sum to ``unsolved``.
    """
    if interview_date <= today:
        raise InterviewDateError(interview_date, today)
    if unsolved < 0 or total_problems < 0:
        raise ValueError("Problem counts cannot be negative.")
    if unsolved > total_problems:
        raise ValueError("Unsolved count cannot exceed the total.")

    days = (interview_date - today).days
    base, extra = divmod(unsolved, days)
    review_counts = _reviews_by_day(review_dates, today=today, interview_date=interview_date)
    detail: list[DayPlan] = []
    for offset in range(days):
        day = today + timedelta(days=offset)
        detail.append(
            DayPlan(
                day=day,
                new_problems=base + (1 if offset < extra else 0),
                reviews=review_counts.get(day, 0),
            )
        )
    scheduled = sum(review_counts.values())
    return StudyPlan(
        today=today,
        interview_date=interview_date,
        days=days,
        total_problems=total_problems,
        unsolved=unsolved,
        new_per_day=unsolved / days,
        scheduled_reviews=scheduled,
        reviews_per_day=scheduled / days,
        days_detail=tuple(detail),
    )


def activity_calendar(
    attempt_dates: Sequence[date],
    *,
    today: date,
    weeks: int = 12,
) -> tuple[CalendarWeek, ...]:
    """GitHub-style grid of the last ``weeks`` weeks, Sunday through Saturday.

    ``count`` is the number of attempts that day. Days after ``today`` in the
    current week are ``None`` so they are not drawn as zero-activity days.
    """
    if weeks < 1:
        raise ValueError("weeks must be positive.")
    end_sunday = sunday_on_or_before(today)
    start_sunday = end_sunday - timedelta(days=7 * (weeks - 1))
    counts: dict[date, int] = {}
    for attempt_day in attempt_dates:
        counts[attempt_day] = counts.get(attempt_day, 0) + 1

    grid: list[CalendarWeek] = []
    for week_index in range(weeks):
        sunday = start_sunday + timedelta(days=7 * week_index)
        days = []
        for offset in range(7):
            day = sunday + timedelta(days=offset)
            if day > today:
                days.append(CalendarDay(day=day, count=None))
            else:
                days.append(CalendarDay(day=day, count=counts.get(day, 0)))
        grid.append(CalendarWeek(start=sunday, days=tuple(days)))
    return tuple(grid)


def sunday_on_or_before(day: date) -> date:
    return day - timedelta(days=(day.weekday() + 1) % 7)


def elapsed_minutes(seconds: float) -> int:
    """Convert a timer duration to whole minutes.

    Zero or negative durations stay 0. Any positive duration is at least 1
    minute. ``round`` is Python's half-to-even round.
    """
    if seconds <= 0:
        return 0
    return max(1, round(seconds / 60))


def mock_weight(
    *,
    category: str,
    solved: bool,
    due: bool,
    weakness_by_category: Mapping[str, float],
) -> float:
    """Weight a problem toward weak categories, and toward unsolved or due ones."""
    weakness = weakness_by_category.get(category, 1.0)
    weight = 1.0 + weakness
    if not solved:
        weight *= 3
    if due:
        weight *= 2
    return weight


def choose_weighted(item_ids: Sequence[int], weights: Sequence[float], *, rng: Random) -> int:
    if not item_ids:
        raise ValueError("No problems to choose from.")
    if len(item_ids) != len(weights):
        raise ValueError("Each problem needs a weight.")
    if any(weight <= 0 for weight in weights):
        raise ValueError("Weights must be positive.")
    chosen = rng.choices(list(item_ids), weights=list(weights), k=1)
    return chosen[0]


def _category_row(category: str, items: Sequence[AttemptSnapshot]) -> CategoryStats:
    total = len(items)
    solved = sum(1 for item in items if item.solved)
    attempted = [item for item in items if item.latest_outcome is not None]
    cold_solves = sum(1 for item in attempted if item.latest_outcome.casefold() == "cold")
    if attempted:
        cold_solve_rate = cold_solves / len(attempted)
        average_minutes = sum(item.latest_minutes or 0 for item in attempted) / len(attempted)
        target_minutes = sum(TARGET_MINUTES[item.difficulty] for item in attempted) / len(attempted)
    else:
        cold_solve_rate = None
        average_minutes = None
        target_minutes = None
    return CategoryStats(
        category=category,
        solved=solved,
        total=total,
        attempted=len(attempted),
        cold_solves=cold_solves,
        cold_solve_rate=cold_solve_rate,
        average_minutes=average_minutes,
        target_minutes=target_minutes,
        weakness=_weakness(
            solved=solved,
            total=total,
            cold_solve_rate=cold_solve_rate,
            average_minutes=average_minutes,
            target_minutes=target_minutes,
        ),
        weakest=False,
    )


def _weakness(
    *,
    solved: int,
    total: int,
    cold_solve_rate: float | None,
    average_minutes: float | None,
    target_minutes: float | None,
) -> float:
    """1 is weakest and 0 is strongest.

    Strength is the mean of solve ratio, cold-solve rate, and how close the
    latest attempts are to the target time. Missing attempts score 0 on the
    cold-solve and time parts, so an untouched category ranks as weak.
    """
    solve_ratio = solved / total if total else 0.0
    cold = 0.0 if cold_solve_rate is None else cold_solve_rate
    if average_minutes is None or target_minutes is None:
        time_ratio = 0.0
    elif average_minutes <= 0:
        time_ratio = 1.0
    else:
        time_ratio = min(1.0, target_minutes / average_minutes)
    return 1 - (solve_ratio + cold + time_ratio) / 3


def _weakest_names(rows: Sequence[CategoryStats]) -> set[str]:
    order = _category_index()
    ranked = sorted(
        rows,
        key=lambda row: (
            -row.weakness,
            (row.solved / row.total) if row.total else 0,
            -(row.total - row.solved),
            order.get(row.category, len(order)),
            row.category,
        ),
    )
    return {row.category for row in ranked[:3]}


def _reviews_by_day(
    review_dates: Sequence[date],
    *,
    today: date,
    interview_date: date,
) -> dict[date, int]:
    counts: dict[date, int] = {}
    for review_day in review_dates:
        if review_day >= interview_date:
            continue
        bucket = today if review_day < today else review_day
        counts[bucket] = counts.get(bucket, 0) + 1
    return counts


def _category_index() -> dict[str, int]:
    return {name: index for index, name in enumerate(CATEGORY_ORDER)}
