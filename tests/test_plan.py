"""Unit tests for the interview study-plan calculation."""

from datetime import date

import pytest

from nctrack.stats import InterviewDateError, InvalidPlanDateError, build_plan, parse_plan_date


def test_plan_splits_new_problems_and_projects_reviews():
    plan = build_plan(
        today=date(2026, 1, 1),
        interview_date=date(2026, 1, 11),
        total_problems=40,
        unsolved=23,
        review_dates=[
            date(2025, 12, 1),
            date(2026, 1, 1),
            date(2026, 1, 5),
            date(2026, 1, 11),
            date(2026, 2, 1),
        ],
    )
    assert plan.days == 10
    assert plan.new_per_day == pytest.approx(2.3)
    assert plan.scheduled_reviews == 3
    assert plan.reviews_per_day == pytest.approx(0.3)
    assert sum(day.new_problems for day in plan.days_detail) == 23
    assert [day.new_problems for day in plan.days_detail[:3]] == [3, 3, 3]
    assert [day.new_problems for day in plan.days_detail[3:]] == [2] * 7
    assert plan.days_detail[0].reviews == 2
    assert plan.days_detail[4].day == date(2026, 1, 5)
    assert plan.days_detail[4].reviews == 1
    assert sum(day.reviews for day in plan.days_detail) == 3


def test_plan_with_nothing_left_to_learn_still_counts_reviews():
    plan = build_plan(
        today=date(2026, 5, 1),
        interview_date=date(2026, 5, 3),
        total_problems=10,
        unsolved=0,
        review_dates=[date(2026, 5, 2)],
    )
    assert plan.new_per_day == 0
    assert plan.reviews_per_day == pytest.approx(0.5)
    assert [day.new_problems for day in plan.days_detail] == [0, 0]
    assert [day.reviews for day in plan.days_detail] == [0, 1]


@pytest.mark.parametrize(
    "interview",
    [date(2026, 1, 1), date(2025, 12, 31)],
)
def test_plan_rejects_an_interview_that_is_not_in_the_future(interview):
    with pytest.raises(InterviewDateError, match="must be after today"):
        build_plan(
            today=date(2026, 1, 1),
            interview_date=interview,
            total_problems=1,
            unsolved=1,
            review_dates=[],
        )


def test_parse_plan_date():
    assert parse_plan_date("2026-10-01") == date(2026, 10, 1)
    with pytest.raises(InvalidPlanDateError, match="Expected YYYY-MM-DD"):
        parse_plan_date("October 1")
