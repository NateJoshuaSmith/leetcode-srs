# nctrack

Command-line tracker for the NeetCode 150. It records practice attempts in a local SQLite database and sets the next review date from how each attempt went.

## Install

Python 3.11 or newer is required. Install from this repository so `init` can find `data/problems.json`.

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

The database is `~/.nctrack/nctrack.db`. Pass `--db PATH` on a command, or set `NCTRACK_DB`, to use another file. `--db` wins when both are set.

## Quick start

```bash
nctrack init
nctrack log "Two Sum" --outcome cold --minutes 8
nctrack due
```

`init` loads the problem list and is safe to run again. `log` looks up a problem by id, slug, or title. Outcomes are `cold`, `hint`, `solution`, and `failed`. `due` lists problems whose review date is today or earlier.

## Commands

| Command | Description |
|---|---|
| `nctrack init` | Create the database and load the NeetCode 150. |
| `nctrack list` | List problems. Filter with `--category`, `--status`, and `--difficulty`. |
| `nctrack log QUERY` | Record an attempt with `--outcome` and `--minutes`, and update the review date. |
| `nctrack show QUERY` | Show one problem, its notes, attempts, and next review. |
| `nctrack note QUERY` | Edit the key insight, time complexity, space complexity, and gotchas. |
| `nctrack due` | List problems due for review, oldest first. |
| `nctrack start QUERY` | Time a problem, then log the attempt when you press Enter. |
| `nctrack stats` | Show solve rate, cold-solve rate, and minutes versus target by category. |
| `nctrack mock` | Time a random problem with notes hidden. `--minutes` sets the limit. |
| `nctrack plan --date YYYY-MM-DD` | Show how many new problems and reviews to do each day until that date. |
| `nctrack calendar` | Show attempt counts for the last 12 weeks. |
| `nctrack export --format json\|csv` | Write problems, attempts, notes, and review state. JSON can be restored; CSV cannot. |
| `nctrack import FILE` | Restore a JSON export. |

## Review scheduling

Logging an attempt sets the next review date. A `cold` solve at or under the target (Easy 10, Medium 20, Hard 35 minutes) schedules the longest gap; a `cold` solve over the target is a step shorter. `hint`, `solution`, and `failed` schedule the next review for tomorrow. A successful streak is due in 1 day, then 3 days, then the previous gap times an ease factor that never drops below 1.3.

## Tests

With the virtual environment active:

```bash
pytest
```
