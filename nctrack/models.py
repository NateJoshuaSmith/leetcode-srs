"""Data types shared by the database, scheduler, and CLI."""

from dataclasses import dataclass
from datetime import date

DIFFICULTIES = ("Easy", "Medium", "Hard")
OUTCOMES = ("cold", "hint", "solution", "failed")
STATUSES = ("unsolved", "solved", "due")

# A successful attempt is one where the problem was completed, with or without help.
SOLVED_OUTCOMES = ("cold", "hint", "solution")

# Roadmap order used when listing problems. Names match the NeetCode 150 headings.
CATEGORY_ORDER = (
    "Arrays & Hashing",
    "Two Pointers",
    "Sliding Window",
    "Stack",
    "Binary Search",
    "Linked List",
    "Trees",
    "Tries",
    "Heap / Priority Queue",
    "Backtracking",
    "Graphs",
    "Advanced Graphs",
    "1-D Dynamic Programming",
    "2-D Dynamic Programming",
    "Greedy",
    "Intervals",
    "Math & Geometry",
    "Bit Manipulation",
)

TARGET_MINUTES = {"Easy": 10, "Medium": 20, "Hard": 35}

DEFAULT_EASE_FACTOR = 2.5
MIN_EASE_FACTOR = 1.3


@dataclass(frozen=True)
class Problem:
    id: int
    slug: str
    title: str
    difficulty: str
    category: str
    leetcode_url: str
    list_name: str


@dataclass(frozen=True)
class Attempt:
    id: int
    problem_id: int
    date: date
    minutes: int
    outcome: str
    notes: str | None


@dataclass(frozen=True)
class ReviewState:
    problem_id: int
    ease_factor: float
    interval_days: int
    repetitions: int
    next_review_date: date


@dataclass(frozen=True)
class ProblemNotes:
    problem_id: int
    key_insight: str | None
    time_complexity: str | None
    space_complexity: str | None
    gotchas: str | None


@dataclass(frozen=True)
class ProblemSummary:
    id: int
    slug: str
    title: str
    difficulty: str
    category: str
    attempt_count: int
    status: str
    solved: bool
    due: bool
    next_review_date: date | None


@dataclass(frozen=True)
class ProblemDetail:
    problem: Problem
    notes: ProblemNotes | None
    attempts: list[Attempt]
    review: ReviewState | None
    status: str


@dataclass(frozen=True)
class ReviewUpdate:
    ease_factor: float
    interval_days: int
    repetitions: int
    next_review_date: date
    quality: int


@dataclass(frozen=True)
class InitResult:
    path: str
    added: int
    total: int


@dataclass(frozen=True)
class PracticeRecord:
    """One problem plus the fields Phase 2 stats and mocks need."""

    problem: Problem
    solved: bool
    due: bool
    latest_outcome: str | None
    latest_minutes: int | None
    next_review_date: date | None


@dataclass(frozen=True)
class AttemptSnapshot:
    """Latest-attempt view of a problem, used by pure stats functions."""

    category: str
    difficulty: str
    solved: bool
    latest_outcome: str | None
    latest_minutes: int | None


@dataclass(frozen=True)
class CategoryStats:
    category: str
    solved: int
    total: int
    attempted: int
    cold_solves: int
    cold_solve_rate: float | None
    average_minutes: float | None
    target_minutes: float | None
    weakness: float
    weakest: bool


@dataclass(frozen=True)
class DayPlan:
    day: date
    new_problems: int
    reviews: int


@dataclass(frozen=True)
class StudyPlan:
    today: date
    interview_date: date
    days: int
    total_problems: int
    unsolved: int
    new_per_day: float
    scheduled_reviews: int
    reviews_per_day: float
    days_detail: tuple[DayPlan, ...]


@dataclass(frozen=True)
class CalendarDay:
    day: date
    count: int | None


@dataclass(frozen=True)
class CalendarWeek:
    start: date
    days: tuple[CalendarDay, ...]
