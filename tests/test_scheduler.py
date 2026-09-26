"""Unit tests for every simplified SM-2 rule in the spec."""

from datetime import date, timedelta

import pytest

from nctrack.scheduler import (
    InvalidOutcomeError,
    quality_for,
    schedule,
    updated_ease_factor,
)


@pytest.mark.parametrize(
    ("difficulty", "minutes", "expected"),
    [
        ("Easy", 9, 5),
        ("Easy", 10, 5),
        ("Easy", 11, 4),
        ("Medium", 19, 5),
        ("Medium", 20, 5),
        ("Medium", 21, 4),
        ("Hard", 34, 5),
        ("Hard", 35, 5),
        ("Hard", 36, 4),
    ],
)
def test_cold_quality_uses_target_time(difficulty, minutes, expected):
    assert quality_for("cold", minutes, difficulty) == expected


@pytest.mark.parametrize("minutes", [0, 10, 100])
@pytest.mark.parametrize(
    ("outcome", "expected"),
    [("hint", 3), ("solution", 1), ("failed", 0)],
)
def test_non_cold_quality_ignores_time(outcome, expected, minutes):
    assert quality_for(outcome, minutes, "Hard") == expected


def test_outcome_matching_is_case_insensitive():
    assert quality_for(" COLD ", 10, "Easy") == 5
    assert quality_for("Failed", 5, "Easy") == 0


def test_invalid_outcome_has_a_clear_message():
    with pytest.raises(InvalidOutcomeError, match="Invalid outcome 'nope'"):
        quality_for("nope", 10, "Easy")


def test_quality_below_3_resets_repetitions_and_interval():
    result = schedule(
        outcome="failed",
        minutes=40,
        difficulty="Hard",
        attempt_date=date(2026, 4, 1),
        ease_factor=2.5,
        interval_days=20,
        repetitions=5,
    )
    assert result.quality == 0
    assert result.repetitions == 0
    assert result.interval_days == 1
    assert result.next_review_date == date(2026, 4, 2)


def test_solution_also_resets_because_quality_is_1():
    result = schedule(
        outcome="solution",
        minutes=5,
        difficulty="Easy",
        attempt_date=date(2026, 4, 1),
        ease_factor=2.8,
        interval_days=13,
        repetitions=4,
    )
    assert result.quality == 1
    assert result.repetitions == 0
    assert result.interval_days == 1


def test_quality_3_does_not_reset():
    result = schedule(
        outcome="hint",
        minutes=100,
        difficulty="Easy",
        attempt_date=date(2026, 4, 1),
        ease_factor=2.5,
        interval_days=0,
        repetitions=0,
    )
    assert result.quality == 3
    assert result.repetitions == 1
    assert result.interval_days == 1


def test_first_success_interval_is_one_day():
    result = schedule(
        outcome="cold",
        minutes=10,
        difficulty="Easy",
        attempt_date=date(2026, 1, 1),
        ease_factor=2.5,
        interval_days=10,
        repetitions=0,
    )
    assert result.repetitions == 1
    assert result.interval_days == 1
    assert result.next_review_date == date(2026, 1, 2)


def test_second_success_interval_is_three_days():
    result = schedule(
        outcome="cold",
        minutes=10,
        difficulty="Easy",
        attempt_date=date(2026, 1, 2),
        ease_factor=2.6,
        interval_days=1,
        repetitions=1,
    )
    assert result.repetitions == 2
    assert result.interval_days == 3
    assert result.next_review_date == date(2026, 1, 5)


def test_later_interval_uses_previous_ease_factor_before_the_update():
    """A cold solve raises ease by 0.1, but the new interval uses the old ease."""
    result = schedule(
        outcome="cold",
        minutes=10,
        difficulty="Easy",
        attempt_date=date(2026, 1, 5),
        ease_factor=2.5,
        interval_days=10,
        repetitions=2,
    )
    assert result.quality == 5
    assert result.repetitions == 3
    assert result.interval_days == round(10 * 2.5)
    assert result.interval_days == 25
    assert result.interval_days != round(10 * 2.6)
    assert result.ease_factor == pytest.approx(2.6)
    assert result.next_review_date == date(2026, 1, 5) + timedelta(days=25)


def test_over_target_cold_keeps_ease_and_still_advances():
    result = schedule(
        outcome="cold",
        minutes=11,
        difficulty="Easy",
        attempt_date=date(2026, 5, 1),
        ease_factor=2.5,
        interval_days=1,
        repetitions=1,
    )
    assert result.quality == 4
    assert result.ease_factor == pytest.approx(2.5)
    assert result.repetitions == 2
    assert result.interval_days == 3


@pytest.mark.parametrize(
    ("quality", "expected_delta"),
    [(5, 0.1), (4, 0.0), (3, -0.14), (1, -0.54), (0, -0.8)],
)
def test_ease_factor_formula(quality, expected_delta):
    assert updated_ease_factor(2.5, quality) == pytest.approx(2.5 + expected_delta)


def test_ease_factor_floor():
    assert updated_ease_factor(1.3, 0) == 1.3
    assert updated_ease_factor(1.5, 0) == 1.3
    floored = schedule(
        outcome="failed",
        minutes=50,
        difficulty="Hard",
        attempt_date=date(2026, 6, 1),
        ease_factor=1.3,
        interval_days=8,
        repetitions=3,
    )
    assert floored.ease_factor == 1.3
    assert floored.repetitions == 0
    assert floored.interval_days == 1


def test_rounding_uses_pythons_half_to_even():
    half_down = schedule(
        outcome="cold",
        minutes=11,
        difficulty="Easy",
        attempt_date=date(2026, 7, 1),
        ease_factor=2.5,
        interval_days=5,
        repetitions=2,
    )
    assert half_down.quality == 4
    assert half_down.ease_factor == pytest.approx(2.5)
    assert half_down.interval_days == 12  # round(12.5) == 12

    also_even = schedule(
        outcome="cold",
        minutes=11,
        difficulty="Medium",
        attempt_date=date(2026, 7, 1),
        ease_factor=2.5,
        interval_days=1,
        repetitions=2,
    )
    assert also_even.interval_days == 2  # round(2.5) == 2


def test_success_then_failure_sequence():
    attempt_date = date(2026, 1, 1)
    state = schedule(
        outcome="cold",
        minutes=10,
        difficulty="Easy",
        attempt_date=attempt_date,
    )
    assert (state.repetitions, state.interval_days, state.next_review_date) == (
        1,
        1,
        date(2026, 1, 2),
    )
    assert state.ease_factor == pytest.approx(2.6)

    state = schedule(
        outcome="cold",
        minutes=10,
        difficulty="Easy",
        attempt_date=state.next_review_date,
        ease_factor=state.ease_factor,
        interval_days=state.interval_days,
        repetitions=state.repetitions,
    )
    assert (state.repetitions, state.interval_days) == (2, 3)
    assert state.ease_factor == pytest.approx(2.7)
    assert state.next_review_date == date(2026, 1, 5)

    previous_ease = state.ease_factor
    previous_interval = state.interval_days
    state = schedule(
        outcome="cold",
        minutes=18,
        difficulty="Easy",
        attempt_date=state.next_review_date,
        ease_factor=state.ease_factor,
        interval_days=state.interval_days,
        repetitions=state.repetitions,
    )
    assert state.quality == 4  # over the 10 minute Easy target
    assert state.repetitions == 3
    assert state.interval_days == round(previous_interval * previous_ease)
    assert state.ease_factor == pytest.approx(previous_ease)

    state = schedule(
        outcome="failed",
        minutes=30,
        difficulty="Easy",
        attempt_date=state.next_review_date,
        ease_factor=state.ease_factor,
        interval_days=state.interval_days,
        repetitions=state.repetitions,
    )
    assert state.quality == 0
    assert state.repetitions == 0
    assert state.interval_days == 1
    assert state.ease_factor == pytest.approx(2.7 - 0.8)
