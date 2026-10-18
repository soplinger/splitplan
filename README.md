# Splitplan

**Offline race-split planner for rowing and paddling: one plain-text plan in, exact split times out.**

Splitplan turns a target finish time into split times that add up to the target to the tenth of a second. It supports even, negative and positive splits, start allowances, partial final pieces and stroke-length estimates. You can compare a finished race against the plan with a CI-friendly gate. It uses only the Python 3 standard library and makes no network calls.

## Features

- **Plain-text plan files** with `key: value` lines and `#` comments, so plans live happily in git.
- **Line-numbered errors**: unknown keys, repeated keys, bad times and missing fields all report `file:line`.
- **Exact splits**: cumulative clock times are rounded once, so the splits always sum to the target with no drift.
- **Three strategies**: `even`, `negative N%` (later pieces faster) and `positive N%` (later pieces slower), with fades up to 20%.
- **Start allowance**: seconds lost at the start are added to the first split and included in the clock times.
- **Partial final pieces**: a 2100 m race with 500 m splits gets a 100 m last piece, priced per 500 m.
- **Pace per split and clock time** for every piece, plus a projected finish.
- **Stroke estimates**: with a `stroke_rate`, shows metres per stroke for each piece.
- **Target and pace overrides**: `--target 7:40.0` or `--pace 1:55.0` re-plan a race without editing the file.
- **Lint with warnings**: flags steep fades, big pace jumps, unrealistic averages, long starts and tiny final pieces. `--strict` makes warnings fail.
- **Race comparison**: `compare` lines up actual split times against the plan, with per-split and running deltas.
- **CI gate**: `compare --max-behind SECONDS` exits 1 when a race runs too far behind plan.
- **CSV export** for spreadsheets and logbooks.
- **Pure stdlib**: no servers, accounts, API keys or network calls.

## Quick start

Save this as `plan.txt`, or use the copy in `examples/plan.txt`:

```text
# Club 2K time trial, negative-split plan
name: Club 2K time trial
distance: 2000 m
split: 500 m
target: 7:45.0
strategy: negative 2%
start: 3.0
stroke_rate: 30
```

Print the split table:

```console
$ python3 splitplan.py show examples/plan.txt
Club 2K time trial
2000 m, target 7:45.0, negative 2%, start 3.0 s, 30 spm

seg  to_m   split   clock  pace/500m  m/stroke
---  ----  ------  ------  ---------  --------
  1   500  1:59.7  1:59.7     1:56.7      8.57
  2  1000  1:55.9  3:55.6     1:55.9      8.63
  3  1500  1:55.1  5:50.7     1:55.1      8.69
  4  2000  1:54.3  7:45.0     1:54.3      8.75

Projected finish 7:45.0
```

Compare a finished race against the plan, failing if you are more than 2 s down:

```console
$ python3 splitplan.py compare examples/plan.txt examples/actual.txt --max-behind 2
```

## How to run

Requires Python 3.10 or newer. There is nothing to install.

```text
python3 splitplan.py show    PLAN [--target T | --pace P]
python3 splitplan.py csv     PLAN [--target T | --pace P] [-o FILE]
python3 splitplan.py lint    PLAN [--strict]
python3 splitplan.py compare PLAN ACTUAL [--max-behind SECONDS] [--target T | --pace P]
```

Exit codes: `0` success, `1` plan errors, lint failures or a failed gate, `2` a file could not be read.

### Plan keys

| Key           | Required | Example          | Meaning                                              |
|---------------|----------|------------------|------------------------------------------------------|
| `distance`    | yes      | `2000 m`         | Race distance in whole metres                        |
| `target`      | yes      | `7:45.0`         | Finish time as `m:ss.t`, `h:mm:ss.t` or `ss.t`       |
| `split`       | no       | `500 m`          | Piece length, default 500 m                          |
| `strategy`    | no       | `negative 2%`    | `even`, `negative N%` or `positive N%`               |
| `start`       | no       | `3.0`            | Start allowance, added to the first split            |
| `stroke_rate` | no       | `30`             | Strokes per minute, 10 to 60                         |
| `name`        | no       | `Head race`      | Title shown above the table                          |

Actual-time files for `compare` hold one split time per line in race order. Comments with `#` are allowed.

## How to test

Tests use pytest:

```text
python3 -m pytest tests/
```

The suite covers time parsing and formatting, exact even, negative and positive splits, partial final pieces, start allowances, stroke estimates, line-numbered parse errors, lint warnings and exit codes, overrides, comparison deltas, the CSV export and the `compare` gate.

## Project layout

```text
splitplan.py          the planner and CLI (single module)
tests/test_splitplan.py
examples/plan.txt     sample plan
examples/actual.txt   sample actual split times
```
