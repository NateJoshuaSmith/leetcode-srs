# NeetCode Progress Tracker — Python CLI

Build a command-line tool in Python 3.11+ for tracking NeetCode practice with spaced-repetition reviews. Build it in phases; finish and test each phase before starting the next.

## Tech constraints
- Python 3.11+, SQLite via the standard `sqlite3` module (no ORM)
- CLI with `typer`, terminal output with `rich` (tables, colors)
- Package layout: `nctrack/` (cli.py, db.py, scheduler.py, stats.py, models.py), `tests/`, `pyproject.toml` with a `nctrack` console entry point
- DB file stored at `~/.nctrack/nctrack.db` (overridable with `--db` flag or `NCTRACK_DB` env var)
- Tests with `pytest`; scheduler and stats logic must be pure functions that are easy to test (pass `today` in rather than calling `date.today()` inside)

## Data model
- **problems**: id, slug, title, difficulty (Easy/Medium/Hard), category (NeetCode roadmap category, e.g. "Arrays & Hashing", "Two Pointers", "Sliding Window", "1-D DP"), leetcode_url, list_name (e.g. "neetcode150")
- **attempts**: id, problem_id, date, minutes, outcome (`cold` | `hint` | `solution` | `failed`), notes
- **review_state**: problem_id, ease_factor (default 2.5), interval_days, repetitions, next_review_date
- **problem_notes**: problem_id, key_insight (one sentence), time_complexity, space_complexity, gotchas

## Seed data
- Load problems from `data/problems.json` (list of objects matching the problems table)
- Generate this file with the NeetCode 150 list grouped by category; I will verify it manually

## Phase 1 — Core
- `nctrack init` — create DB and load seed data (idempotent; re-running doesn't duplicate)
- `nctrack list [--category X] [--status unsolved|solved|due] [--difficulty X]` — table of problems with status, attempts count, next review date
- `nctrack log <slug-or-id> --outcome cold --minutes 18 [--notes "..."]` — record an attempt and update the review schedule. If flags are omitted, prompt interactively
- `nctrack show <slug>` — problem info, notes, full attempt history, next review date
- `nctrack note <slug>` — prompt to edit key insight, complexities, gotchas
- `nctrack due` — review queue: problems whose next_review_date <= today, oldest first
- Problem lookup should accept id, exact slug, or a fuzzy/partial title match (ask to disambiguate if multiple matches)

## Spaced repetition (scheduler.py)
Simplified SM-2. Map outcome to quality:
- `cold` within target time → 5; `cold` over target time → 4; `hint` → 3; `solution` → 1; `failed` → 0
- Target times: Easy 10 min, Medium 20 min, Hard 35 min

Rules:
- If quality < 3: repetitions = 0, interval = 1 day
- Else: repetitions += 1; interval = 1 if repetitions == 1, 3 if repetitions == 2, else round(previous_interval * ease_factor)
- ease_factor = max(1.3, EF + (0.1 - (5 - q) * (0.08 + (5 - q) * 0.02)))
- next_review_date = attempt date + interval

## Phase 2 — Insight and practice
- `nctrack start <slug>` — start a live timer; on Enter, stop it and prompt for outcome, then log the attempt with elapsed minutes
- `nctrack stats` — per-category table: solved/total, cold-solve rate (share of latest attempts that were `cold`), average minutes vs target. Highlight the 3 weakest categories
- `nctrack mock [--minutes N]` — pick a random problem weighted toward weak categories and unsolved/due problems, hide notes, run the timer, require entering time and space complexity before logging
- `nctrack plan --date YYYY-MM-DD` — given an interview date, show new problems per day needed to finish the list plus projected reviews per day
- `nctrack calendar` — GitHub-style activity heatmap of the last 12 weeks in the terminal

## Phase 3 — Data
- `nctrack export --format csv|json [--out path]` — export problems, attempts, and notes
- `nctrack import <file>` — restore from a JSON export

## Non-goals (don't build)
- No web UI, no auth, no LeetCode API sync

## Quality bar
- Unit tests for every scheduler rule (including ease factor floor and over-target cold solves), stats calculations, and the plan calculation
- Clear error messages for unknown problems or invalid outcomes
- README with install steps and example usage for every command
