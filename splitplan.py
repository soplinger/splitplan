#!/usr/bin/env python3
"""Splitplan: offline rowing and paddling race-split planner.

Reads one plain-text plan file, works out exact split times that add up to
the target finish time, and compares a finished race against the plan.
Standard library only; nothing is written or sent anywhere unless you ask.
"""

import argparse
import csv
import math
import re
import sys
from dataclasses import dataclass, replace
from fractions import Fraction

KEYS = ("name", "distance", "split", "target", "strategy", "start", "stroke_rate")
REQUIRED = ("distance", "target")

WARN_FADE = Fraction(5, 100)     # strategies fading more than 5% draw a warning
WARN_START = 100                 # start allowance over 10.0 s draws a warning
WARN_STEP = 30                   # pace may move by at most 3.0 s between splits
WARN_LAST_SPLIT_M = 50           # a final piece shorter than this is suspicious
MIN_PACE, MAX_PACE = 600, 3000   # sane average pace per split: 1:00 to 5:00
MAX_FADE_PCT = Fraction(20)


@dataclass(frozen=True)
class Issue:
    line: int       # 0 when the problem is not tied to one line
    severity: str   # "error" or "warning"
    message: str

    def render(self, name):
        where = f"{name}:{self.line}" if self.line else name
        return f"{where}: {self.severity}: {self.message}"


@dataclass(frozen=True)
class Plan:
    name: str = ""
    distance: int = 0                    # metres
    split: int = 500                     # metres per split
    target: int = 0                      # finish time, tenths of a second
    strategy: str = "even"               # "even", "negative" or "positive"
    fade: Fraction = Fraction(0)         # pace change across the race, as a fraction
    start: int = 0                       # start allowance, tenths of a second
    stroke_rate: Fraction | None = None  # strokes per minute


@dataclass(frozen=True)
class Segment:
    number: int
    start_m: int
    end_m: int
    split: int                           # clock time for this piece, incl. start
    cumulative: int                      # clock time at the end of this piece
    pace: int                            # race pace per split length, no start
    m_per_stroke: Fraction | None

    @property
    def length(self):
        return self.end_m - self.start_m


def _round(value):
    """Round half up to a whole number, keeping Fractions exact."""
    return math.floor(value + Fraction(1, 2))


def parse_time(text):
    """Return tenths of a second from 'h:mm:ss.t', 'm:ss.t' or 'ss.t'."""
    parts = text.strip().split(":")
    if len(parts) > 3 or not all(part.isdigit() for part in parts[:-1]):
        raise ValueError(f"'{text}' is not a time like 1:55.0 or 45.5")
    seconds = parts[-1]
    if not re.fullmatch(r"\d+(\.\d)?", seconds):
        raise ValueError(f"'{text}' needs seconds like 45 or 45.5")
    whole, _, tenth = seconds.partition(".")
    if len(parts) > 1 and int(whole) >= 60:
        raise ValueError(f"'{text}' has seconds of 60 or more")
    if len(parts) == 3 and int(parts[1]) >= 60:
        raise ValueError(f"'{text}' has minutes of 60 or more")
    total = 0
    for part in parts[:-1]:
        total = total * 60 + int(part)
    total = total * 60 + int(whole)
    return total * 10 + int(tenth or 0)


def format_time(tenths):
    """Format tenths of a second as h:mm:ss.t, m:ss.t or s.t."""
    hours, rest = divmod(tenths, 36000)
    minutes, rest = divmod(rest, 600)
    seconds, tenth = divmod(rest, 10)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}.{tenth}"
    if minutes:
        return f"{minutes}:{seconds:02d}.{tenth}"
    return f"{seconds}.{tenth}"


def format_delta(tenths):
    """Signed difference: '+1.2' is behind plan, '-0.4' is ahead."""
    sign = "+" if tenths > 0 else "-" if tenths < 0 else ""
    return f"{sign}{format_time(abs(tenths))}"


def parse_metres(text, label):
    match = re.fullmatch(r"(\d+)\s*m?", text.strip())
    if not match or int(match.group(1)) == 0:
        raise ValueError(f"{label} must be a whole number of metres, like 2000 m")
    return int(match.group(1))


def parse_strategy(text):
    match = re.fullmatch(r"(even)|(negative|positive)\s+(\d+(?:\.\d+)?)\s*%?", text.strip(), re.I)
    if not match:
        raise ValueError("strategy must be 'even', 'negative 2%' or 'positive 1.5%'")
    if match.group(1):
        return "even", Fraction(0)
    percent = Fraction(match.group(3))
    if percent > MAX_FADE_PCT:
        raise ValueError("strategy fade must be 20% or less")
    return match.group(2).lower(), percent / 100


def parse_stroke_rate(text):
    try:
        rate = Fraction(text.strip())
    except ValueError:
        raise ValueError(f"stroke rate '{text}' is not a number") from None
    if not 10 <= rate <= 60:
        raise ValueError("stroke rate must be between 10 and 60 strokes per minute")
    return rate


def _parse_value(key, value):
    if key == "name":
        return value
    if key in ("distance", "split"):
        return parse_metres(value, key)
    if key in ("target", "start"):
        return parse_time(value)
    if key == "strategy":
        return parse_strategy(value)
    return parse_stroke_rate(value)


def build_segments(plan):
    """Split the race into pieces and return one Segment per piece.

    Pace is shaped by weighting each piece's length with a linear fade, then
    cumulative clock times are rounded once, so the splits always add up to
    the target exactly and rounding never drifts.
    """
    ends = list(range(plan.split, plan.distance, plan.split)) + [plan.distance]
    starts = [0] + ends[:-1]
    count = len(ends)

    weights = []
    for index, (begin, end) in enumerate(zip(starts, ends)):
        factor = Fraction(1)
        if plan.strategy != "even" and count > 1:
            fade = plan.fade * index / (count - 1)
            factor = 1 - fade if plan.strategy == "negative" else 1 + fade
        weights.append((end - begin) * factor)

    race_time = plan.target - plan.start
    total_weight = sum(weights)
    race_cumulative = [0]
    running = Fraction(0)
    for weight in weights:
        running += weight
        race_cumulative.append(_round(race_time * running / total_weight))

    segments = []
    for index, (begin, end) in enumerate(zip(starts, ends)):
        race_split = race_cumulative[index + 1] - race_cumulative[index]
        length = end - begin
        strokes = None
        m_per_stroke = None
        if plan.stroke_rate:
            strokes = Fraction(race_split, 10) * plan.stroke_rate / 60
            if strokes:
                m_per_stroke = Fraction(length) / strokes
        segments.append(Segment(
            number=index + 1,
            start_m=begin,
            end_m=end,
            split=race_split + (plan.start if index == 0 else 0),
            cumulative=plan.start + race_cumulative[index + 1],
            pace=_round(Fraction(race_split * plan.split, length)),
            m_per_stroke=m_per_stroke,
        ))
    return segments


def _warnings(plan, line_of):
    found = []

    def warn(key, message):
        found.append(Issue(line_of(key), "warning", message))

    average = Fraction(plan.target * plan.split, plan.distance)
    if average < MIN_PACE or average > MAX_PACE:
        warn("target", f"average pace {format_time(_round(average))} per {plan.split} m looks unrealistic")
    if plan.fade > WARN_FADE:
        warn("strategy", f"fade of {float(plan.fade * 100):g}% is steep (over 5%)")
    if plan.start > WARN_START:
        warn("start", "start allowance is over 10 s")
    if plan.stroke_rate is not None and not 20 <= plan.stroke_rate <= 40:
        warn("stroke_rate", "stroke rate is outside the usual 20 to 40 strokes per minute")

    segments = build_segments(plan)
    last = segments[-1]
    if len(segments) > 1 and last.length < WARN_LAST_SPLIT_M:
        warn("distance", f"final split is only {last.length} m")
    full = [s for s in segments if s.length == plan.split]
    for previous, current in zip(full, full[1:]):
        step = abs(current.pace - previous.pace)
        if step > WARN_STEP:
            warn("strategy", f"pace moves {format_time(step)} between split {previous.number} "
                             f"and {current.number} (limit {format_time(WARN_STEP)})")
    return found


def parse_plan(text):
    """Return (plan, issues). The plan is None when any error was found."""
    issues = []
    entries = {}
    for number, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        key, sep, value = stripped.partition(":")
        key = key.strip().lower()
        if not sep:
            issues.append(Issue(number, "error", "expected 'key: value'"))
        elif key not in KEYS:
            issues.append(Issue(number, "error", f"unknown key '{key}'"))
        elif key in entries:
            issues.append(Issue(number, "error", f"'{key}' repeated (first on line {entries[key][0]})"))
        else:
            entries[key] = (number, value.strip())

    values = {}
    for key, (number, value) in entries.items():
        try:
            values[key] = _parse_value(key, value)
        except ValueError as exc:
            issues.append(Issue(number, "error", str(exc)))
    for key in REQUIRED:
        if key not in entries:
            issues.append(Issue(0, "error", f"missing required key '{key}'"))
    if any(issue.severity == "error" for issue in issues):
        return None, issues

    kind, fade = values.get("strategy", ("even", Fraction(0)))
    plan = Plan(
        name=values.get("name", ""),
        distance=values["distance"],
        split=values.get("split", 500),
        target=values["target"],
        strategy=kind,
        fade=fade,
        start=values.get("start", 0),
        stroke_rate=values.get("stroke_rate"),
    )
    if plan.start >= plan.target:
        line = entries["start"][0] if "start" in entries else entries["target"][0]
        return None, [Issue(line, "error", "start allowance must be less than the target time")]

    def line_of(key):
        return entries[key][0] if key in entries else 0

    return plan, issues + _warnings(plan, line_of)


def apply_overrides(plan, target=None, pace=None):
    """Replace the target with --target, or derive it from --pace per split."""
    if target is not None:
        value = parse_time(target)
    elif pace is not None:
        value = _round(Fraction(parse_time(pace) * plan.distance, plan.split))
    else:
        return plan
    if value <= plan.start:
        raise ValueError("target must be slower than the start allowance")
    return replace(plan, target=value)


def parse_actuals(text):
    """Return ([(line, tenths), ...], issues) for a file of split times."""
    times = []
    issues = []
    for number, line in enumerate(text.splitlines(), 1):
        stripped = line.split("#", 1)[0].strip()
        if not stripped:
            continue
        try:
            times.append((number, parse_time(stripped.split()[0])))
        except ValueError as exc:
            issues.append(Issue(number, "error", str(exc)))
    return times, issues


def compare_rows(segments, actuals):
    """One row per completed split: plan, actual, delta and running totals."""
    rows = []
    actual_clock = 0
    for segment, actual in zip(segments, actuals):
        actual_clock += actual
        rows.append((
            segment.number,
            segment.split,
            actual,
            actual - segment.split,
            segment.cumulative,
            actual_clock,
            actual_clock - segment.cumulative,
        ))
    return rows


def describe(plan):
    if plan.strategy == "even":
        strategy = "even"
    else:
        strategy = f"{plan.strategy} {float(plan.fade * 100):g}%"
    parts = [f"{plan.distance} m", f"target {format_time(plan.target)}", strategy]
    if plan.start:
        parts.append(f"start {format_time(plan.start)} s")
    if plan.stroke_rate:
        parts.append(f"{float(plan.stroke_rate):g} spm")
    return f"{plan.name or 'Race plan'}\n" + ", ".join(parts)


def render_table(headers, rows):
    widths = [max(len(str(cell)) for cell in column) for column in zip(headers, *rows)]

    def line(cells):
        return "  ".join(str(cell).rjust(width) for cell, width in zip(cells, widths))

    return "\n".join([line(headers), line(["-" * width for width in widths])] + [line(row) for row in rows])


def plan_table(plan, segments):
    headers = ["seg", "to_m", "split", "clock", f"pace/{plan.split}m"]
    if plan.stroke_rate:
        headers.append("m/stroke")
    rows = []
    for segment in segments:
        row = [segment.number, segment.end_m, format_time(segment.split),
               format_time(segment.cumulative), format_time(segment.pace)]
        if plan.stroke_rate:
            row.append(f"{float(segment.m_per_stroke):.2f}" if segment.m_per_stroke else "-")
        rows.append(row)
    return render_table(headers, rows)


def write_csv(plan, segments, handle):
    writer = csv.writer(handle, lineterminator="\n")
    headers = ["segment", "start_m", "end_m", "length_m", "split", "clock", f"pace_per_{plan.split}m"]
    if plan.stroke_rate:
        headers.append("m_per_stroke")
    writer.writerow(headers)
    for segment in segments:
        row = [segment.number, segment.start_m, segment.end_m, segment.length,
               format_time(segment.split), format_time(segment.cumulative),
               format_time(segment.pace)]
        if plan.stroke_rate:
            row.append(f"{float(segment.m_per_stroke):.2f}" if segment.m_per_stroke else "")
        writer.writerow(row)


def _read(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def _report(name, issues):
    for issue in issues:
        print(issue.render(name), file=sys.stderr)


def _load(path, target=None, pace=None):
    """Return (plan, issues) with overrides applied; plan is None on errors."""
    plan, issues = parse_plan(_read(path))
    if plan is None:
        return None, issues
    try:
        return apply_overrides(plan, target, pace), issues
    except ValueError as exc:
        return None, issues + [Issue(0, "error", str(exc))]


def cmd_show(args):
    plan, issues = _load(args.plan, args.target, args.pace)
    _report(args.plan, issues)
    if plan is None:
        return 1
    segments = build_segments(plan)
    print(describe(plan))
    print()
    print(plan_table(plan, segments))
    print()
    print(f"Projected finish {format_time(plan.target)}")
    return 0


def cmd_csv(args):
    plan, issues = _load(args.plan, args.target, args.pace)
    _report(args.plan, issues)
    if plan is None:
        return 1
    segments = build_segments(plan)
    if args.output:
        with open(args.output, "w", encoding="utf-8", newline="") as handle:
            write_csv(plan, segments, handle)
    else:
        write_csv(plan, segments, sys.stdout)
    return 0


def cmd_lint(args):
    plan, issues = parse_plan(_read(args.plan))
    _report(args.plan, issues)
    errors = [issue for issue in issues if issue.severity == "error"]
    warnings = len(issues) - len(errors)
    if errors or (args.strict and warnings):
        return 1
    print(f"{args.plan}: ok ({warnings} warning{'s' if warnings != 1 else ''})")
    return 0


def cmd_compare(args):
    plan, issues = _load(args.plan, args.target, args.pace)
    _report(args.plan, issues)
    if plan is None:
        return 1
    actual_text = _read(args.actual)
    actuals, actual_issues = parse_actuals(actual_text)
    if actual_issues:
        _report(args.actual, actual_issues)
        return 1
    segments = build_segments(plan)
    if not actuals:
        print(f"{args.actual}: no split times found", file=sys.stderr)
        return 1
    if len(actuals) > len(segments):
        print(f"{args.actual}: {len(actuals)} splits but the plan has {len(segments)}", file=sys.stderr)
        return 1

    rows = compare_rows(segments, [time for _, time in actuals])
    headers = ["seg", "plan", "actual", "delta", "plan_clock", "actual_clock", "behind"]
    table = [[row[0], format_time(row[1]), format_time(row[2]), format_delta(row[3]),
              format_time(row[4]), format_time(row[5]), format_delta(row[6])] for row in rows]
    print(describe(plan))
    print()
    print(render_table(headers, table))
    print()

    behind = rows[-1][6]
    if behind > 0:
        verdict = f"{format_time(behind)} behind plan"
    elif behind < 0:
        verdict = f"{format_time(-behind)} ahead of plan"
    else:
        verdict = "on plan"
    print(f"After {len(rows)} of {len(segments)} splits: {verdict}")

    if args.max_behind is not None and behind > round(args.max_behind * 10):
        print(f"GATE FAILED: more than {args.max_behind:g} s behind plan", file=sys.stderr)
        return 1
    return 0


def build_parser():
    parser = argparse.ArgumentParser(
        prog="splitplan",
        description="Offline race-split planner for rowing and paddling plans.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    def plan_options(sub):
        sub.add_argument("plan", help="plain-text plan file")
        group = sub.add_mutually_exclusive_group()
        group.add_argument("--target", help="override the finish time, e.g. 7:40.0")
        group.add_argument("--pace", help="override by pace per split, e.g. 1:55.0")

    show = commands.add_parser("show", help="print the split table")
    plan_options(show)
    show.set_defaults(func=cmd_show)

    export = commands.add_parser("csv", help="export the split table as CSV")
    plan_options(export)
    export.add_argument("-o", "--output", help="write to this file instead of stdout")
    export.set_defaults(func=cmd_csv)

    lint = commands.add_parser("lint", help="check a plan file for errors and warnings")
    lint.add_argument("plan")
    lint.add_argument("--strict", action="store_true", help="treat warnings as failures")
    lint.set_defaults(func=cmd_lint)

    compare = commands.add_parser("compare", help="compare actual split times with the plan")
    plan_options(compare)
    compare.add_argument("actual", help="file with one actual split time per line")
    compare.add_argument("--max-behind", type=float, metavar="SECONDS",
                         help="fail when the race is more than this many seconds behind plan")
    compare.set_defaults(func=cmd_compare)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except OSError as exc:
        print(f"splitplan: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
