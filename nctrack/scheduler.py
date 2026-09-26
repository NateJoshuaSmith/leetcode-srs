"""Simplified SM-2 scheduling.

Rules (see SPEC.md):

- Map an outcome to a quality score, using difficulty target times for ``cold``.
- If quality < 3, reset repetitions to 0 and set the interval to 1 day.
- Otherwise increment repetitions. The interval is 1 day on the first
  repetition, 3 days on the second, and otherwise
  ``round(previous_interval * ease_factor)``.
- The ease factor used for that interval is the value from before this review.
  Ease is updated afterwards, and never drops below 1.3.
- ``next_review_date`` is the attempt date plus the new interval.

``round`` is Python's built-in round (half to even). Callers pass ``attempt_date``;
this module does not read the system clock.
"""

from datetime import date, timedelta

from nctrack.models import (
    DEFAULT_EASE_FACTOR,
    MIN_EASE_FACTOR,
    OUTCOMES,
    TARGET_MINUTES,
    ReviewUpdate,
)


class InvalidOutcomeError(ValueError):
    def __init__(self, outcome: str) -> None:
        self.outcome = outcome
        joined = ", ".join(OUTCOMES)
        super().__init__(f"Invalid outcome {outcome!r}. Expected one of: {joined}.")


def quality_for(outcome: str, minutes: int, difficulty: str) -> int:
    """Map an attempt to an SM-2 quality in 0..5."""
    normalized = outcome.strip().lower()
    if normalized not in OUTCOMES:
        raise InvalidOutcomeError(outcome)
    if difficulty not in TARGET_MINUTES:
        raise ValueError(
            f"Invalid difficulty {difficulty!r}. Expected one of: Easy, Medium, Hard."
        )
    if normalized == "cold":
        return 5 if minutes <= TARGET_MINUTES[difficulty] else 4
    if normalized == "hint":
        return 3
    if normalized == "solution":
        return 1
    return 0


def updated_ease_factor(ease_factor: float, quality: int) -> float:
    """Apply the SM-2 ease update and enforce the 1.3 floor."""
    gap = 5 - quality
    updated = ease_factor + (0.1 - gap * (0.08 + gap * 0.02))
    return max(MIN_EASE_FACTOR, updated)


def schedule(
    *,
    outcome: str,
    minutes: int,
    difficulty: str,
    attempt_date: date,
    ease_factor: float = DEFAULT_EASE_FACTOR,
    interval_days: int = 0,
    repetitions: int = 0,
) -> ReviewUpdate:
    """Return the review state that follows one attempt.

    ``ease_factor``, ``interval_days``, and ``repetitions`` are the values from
    before this attempt. A problem with no prior review uses the defaults.
    """
    quality = quality_for(outcome, minutes, difficulty)
    if quality < 3:
        new_repetitions = 0
        new_interval = 1
    else:
        new_repetitions = repetitions + 1
        if new_repetitions == 1:
            new_interval = 1
        elif new_repetitions == 2:
            new_interval = 3
        else:
            new_interval = round(interval_days * ease_factor)
    return ReviewUpdate(
        ease_factor=updated_ease_factor(ease_factor, quality),
        interval_days=new_interval,
        repetitions=new_repetitions,
        next_review_date=attempt_date + timedelta(days=new_interval),
        quality=quality,
    )
