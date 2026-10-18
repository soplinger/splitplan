import csv
import io
import sys
from fractions import Fraction
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import splitplan as sp  # noqa: E402


def splits_of(plan):
    return [segment.split for segment in sp.build_segments(plan)]


def test_parse_time_accepts_common_formats():
    assert sp.parse_time("7:45.0") == 4650
    assert sp.parse_time("45.5") == 455
    assert sp.parse_time("1:02:03.4") == 37234


@pytest.mark.parametrize("text", ["7:75.0", "45.55", "x:00", "", "1:2.5.3", "7:45."])
def test_parse_time_rejects_bad_input(text):
    with pytest.raises(ValueError):
        sp.parse_time(text)


def test_format_time_round_trips():
    for tenths in (0, 455, 4650, 37234):
        assert sp.parse_time(sp.format_time(tenths)) == tenths
    assert sp.format_time(4650) == "7:45.0"
    assert sp.format_delta(-16) == "-1.6"


def test_even_split_sums_exactly_to_target():
    plan = sp.Plan(distance=2000, split=500, target=4640)
    assert splits_of(plan) == [1160, 1160, 1160, 1160]
    assert sum(splits_of(plan)) == 4640


def test_negative_split_gets_faster_and_still_sums_to_target():
    plan = sp.Plan(distance=2000, split=500, target=4650, strategy="negative",
                   fade=Fraction(2, 100), start=30)
    splits = splits_of(plan)
    assert splits == [1197, 1159, 1151, 1143]
    assert sum(splits) == 4650
    assert splits[0] > splits[-1]


def test_positive_split_gets_slower():
    plan = sp.Plan(distance=2000, split=500, target=4650, strategy="positive",
                   fade=Fraction(2, 100))
    splits = splits_of(plan)
    assert splits[0] < splits[-1]
    assert sum(splits) == 4650


def test_partial_final_split_is_priced_per_split_length():
    plan = sp.Plan(distance=2100, split=500, target=4650)
    segments = sp.build_segments(plan)
    assert len(segments) == 5
    assert segments[-1].length == 100
    assert segments[-1].end_m == 2100
    assert segments[-1].split == 221
    assert segments[-1].pace == 1105


def test_start_allowance_goes_into_first_split():
    plan = sp.Plan(distance=2000, split=500, target=4650, start=30)
    segments = sp.build_segments(plan)
    assert [s.split for s in segments] == [1185, 1155, 1155, 1155]
    assert [s.cumulative for s in segments] == [1185, 2340, 3495, 4650]
    assert segments[0].pace == 1155


def test_stroke_rate_gives_metres_per_stroke():
    plan = sp.Plan(distance=2000, split=500, target=4650, start=30, stroke_rate=Fraction(30))
    segments = sp.build_segments(plan)
    assert float(segments[1].m_per_stroke) == pytest.approx(500 / 57.75)


def test_parse_plan_reports_each_error_with_its_line():
    text = "name: x\ndistance: 2000 m\nspeed: 5\ndistance: 1000\ntarget: nope\n"
    plan, issues = sp.parse_plan(text)
    assert plan is None
    assert {issue.line for issue in issues if issue.severity == "error"} == {3, 4, 5}
    assert any("unknown key 'speed'" in issue.message for issue in issues)


def test_missing_required_key_is_an_error():
    plan, issues = sp.parse_plan("distance: 2000\n")
    assert plan is None
    assert any("missing required key 'target'" in issue.message for issue in issues)


def test_start_must_be_less_than_target_and_points_at_start_line():
    plan, issues = sp.parse_plan("distance: 2000\ntarget: 5.0\nstart: 6.0\n")
    assert plan is None
    assert [(issue.line, issue.severity) for issue in issues] == [(3, "error")]


def test_lint_warns_on_steep_fade_and_pace_jumps():
    plan, issues = sp.parse_plan("distance: 2000\ntarget: 7:45.0\nstrategy: negative 10%\n")
    assert plan is not None
    messages = [issue.message for issue in issues if issue.severity == "warning"]
    assert any("steep" in message for message in messages)
    assert any("pace moves" in message for message in messages)


def test_pace_and_target_overrides():
    plan = sp.Plan(distance=2000, split=500, target=4650, start=30)
    assert sp.apply_overrides(plan, pace="1:55.0").target == 4600
    assert sp.apply_overrides(plan, target="7:40.0").target == 4600
    with pytest.raises(ValueError):
        sp.apply_overrides(plan, target="0:02.0")


def test_parse_actuals_reports_bad_lines():
    times, issues = sp.parse_actuals("1:58.1\nfast\n")
    assert times == [(1, 1181)]
    assert [issue.line for issue in issues] == [2]


def test_compare_rows_track_split_and_running_deltas():
    plan = sp.Plan(distance=2000, split=500, target=4640)
    rows = sp.compare_rows(sp.build_segments(plan), [1170, 1150, 1180, 1140])
    assert rows[0][3] == 10
    assert rows[1][6] == 0
    assert rows[2][6] == 20
    assert rows[3][6] == 0


def test_csv_export_has_one_row_per_split():
    plan, _ = sp.parse_plan("distance: 2100 m\ntarget: 7:45.0\n")
    handle = io.StringIO()
    sp.write_csv(plan, sp.build_segments(plan), handle)
    rows = list(csv.reader(io.StringIO(handle.getvalue())))
    assert rows[0][0] == "segment"
    assert len(rows) == 1 + 5


def test_lint_exit_codes_and_strict_mode(tmp_path, capsys):
    good = tmp_path / "good.txt"
    good.write_text("distance: 2000 m\ntarget: 7:45.0\n", encoding="utf-8")
    steep = tmp_path / "steep.txt"
    steep.write_text("distance: 2000\ntarget: 7:45.0\nstrategy: negative 10%\n", encoding="utf-8")
    bad = tmp_path / "bad.txt"
    bad.write_text("distance: nope\n", encoding="utf-8")

    assert sp.main(["lint", str(good)]) == 0
    assert sp.main(["lint", str(steep)]) == 0
    assert sp.main(["lint", str(steep), "--strict"]) == 1
    assert sp.main(["lint", str(bad)]) == 1


def test_compare_gate_fails_only_when_behind_limit_is_exceeded(tmp_path, capsys):
    plan = tmp_path / "plan.txt"
    plan.write_text("distance: 2000 m\ntarget: 7:44.0\n", encoding="utf-8")
    actual = tmp_path / "actual.txt"
    actual.write_text("2:00.0\n" * 4, encoding="utf-8")

    assert sp.main(["compare", str(plan), str(actual), "--max-behind", "5"]) == 1
    assert "GATE FAILED" in capsys.readouterr().err
    assert sp.main(["compare", str(plan), str(actual), "--max-behind", "20"]) == 0


def test_show_prints_table_and_projected_finish(tmp_path, capsys):
    plan = tmp_path / "plan.txt"
    plan.write_text("name: Test\ndistance: 2000 m\ntarget: 7:44.0\n", encoding="utf-8")
    assert sp.main(["show", str(plan)]) == 0
    out = capsys.readouterr().out
    assert "Test" in out
    assert "Projected finish 7:44.0" in out
