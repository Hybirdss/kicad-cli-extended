"""End-to-end tests of the advanced commands on real KiCad 10 (kicad-cli + pcbnew), through the
installed ``kce``: teardrops, via stitching/fences, rule areas, dimensions, barcodes, holes,
fiducials, differential pairs, lengths and tuning, 3D models, footprint updates, SPICE
simulation, live-GUI client, panelization, foreign-board import and design variants.

Every board-changing scenario ends with KiCad's own DRC (and kicad-cli re-loading the files).
"""

from __future__ import annotations

import json
import math
import os
import re
import shutil
import tempfile

import pytest

from kicad_cli_extended import STATE_DIR
from test_e2e import (COMMAND, ENV_PREFIX, REPO, artifact, blink, blink_master, demo_copy, demos_dir,  # noqa: F401
                      read_csv, read_text, rj, run, tmp_dir)

pytestmark = pytest.mark.e2e


def drc(project, *extra):
    return rj(["--project", project, "check", "drc", *extra])


def qa_dir() -> str:
    d = os.path.join(os.path.dirname(demos_dir()), "qa", "data", "pcbnew", "plugins", "altium")
    if not os.path.isdir(d):
        raise RuntimeError(f"Altium importer fixture not found at {d}; run `python scripts/fetch_fixtures.py`.")
    return d


# ─────────────────────────────────────────────────────────────────────────────
# Board features on the 555 blinker
# ─────────────────────────────────────────────────────────────────────────────

class TestBoardFeatures:
    def test_teardrops_stitching_holes_fiducial_barcode_dimension_drc_clean(self, blink):
        P = ["--project", blink]
        td = rj(P + ["pcb", "teardrops", "add"])
        assert td["added"] >= 10 and td["filled"]
        assert "(type padvia)" in read_text(os.path.join(blink, "blink.kicad_pcb"))  # KiCad teardrop zones
        h1 = rj(P + ["pcb", "hole", "--at", "135,124", "--drill", "3.2", "--pad", "6", "--net", "/GND"])
        h2 = rj(P + ["pcb", "hole", "--at", "135,115", "--drill", "2.2"])
        assert (h1["reference"], h1["plated"], h2["reference"], h2["plated"]) == ("H1", True, "H2", False)
        assert rj(P + ["pcb", "fiducial", "--at", "128,127"])["reference"] == "FID1"
        assert rj(P + ["pcb", "barcode", "kce", "--at", "127,118", "--size", "4", "--layer", "B.SilkS"])["barcode"] == "qr"
        dim = rj(P + ["pcb", "dimension", "aligned", "--start", "100,100", "--end", "140,100", "--height", "-4"])
        assert dim["value"] == pytest.approx(40.0) and "40" in dim["text"]
        assert rj(P + ["pcb", "dimension", "leader", "--start", "120,110", "--end", "145,95",
                       "--text", "NOTE"])["text"] == "NOTE"
        ra = rj(P + ["pcb", "zone", "rule-area", "--name", "keepclear", "--rect", "113,122,6,5",
                     "--no", "vias", "--no", "tracks", "--rule", "clearance min 0.5mm"])
        assert ra["disallow"] == ["tracks", "vias"] and "intersectsArea('keepclear')" in ra["custom_rule"]
        st = rj(P + ["pcb", "stitch", "--net", "/GND", "--pitch", "3"])
        assert st["added"] > 20 and st["layers"] == ["F.Cu", "B.Cu"]
        vias = [v["at"] for v in rj(P + ["pcb", "tracks"]) if v["type"] == "via"]
        assert not any(113 <= x <= 119 and 122 <= y <= 127 for x, y in vias)  # rule area respected
        fps = {f["reference"]: f for f in rj(P + ["pcb", "footprints"])}
        assert fps["H2"]["exclude_from_bom"] and fps["FID1"]["exclude_from_bom"] and not fps["FID1"]["exclude_from_pos"]
        d = drc(blink)
        assert d["errors"] == 0 and d["unconnected"] == 0, d["by_type"]
        # undo walks back through every step
        rj(P + ["session", "undo", "--steps", str(len(rj(P + ["session", "history"])["undo"]))])
        assert "(type padvia)" not in read_text(os.path.join(blink, "blink.kicad_pcb"))

    def test_teardrops_remove_and_settings(self, blink):
        P = ["--project", blink]
        add = rj(P + ["pcb", "teardrops", "add", "--target", "vias", "--target", "smd", "--max-length", "0.8"])
        assert add["settings"]["max_length"] == 0.8
        rem = rj(P + ["pcb", "teardrops", "remove"])
        assert rem["removed"] == add["added"]
        assert "(type padvia)" not in read_text(os.path.join(blink, "blink.kicad_pcb"))

    def test_via_fence(self, blink):
        P = ["--project", blink]
        f = rj(P + ["pcb", "fence", "--net", "/OUT", "--fence-net", "/GND", "--pitch", "1.5"])
        assert f["added"] >= 4 and f["fence_net"] == "/GND"
        assert drc(blink, "--no-parity")["errors"] == 0  # the vias join the GND pour


# ─────────────────────────────────────────────────────────────────────────────
# Differential pairs, lengths, tuning, rules
# ─────────────────────────────────────────────────────────────────────────────

def build_pair_project(root: str) -> str:
    p = os.path.join(root, "dp")
    run(["project", "new", p, "--name", "dp"])
    P = ["--project", p]
    fp = "Connector_PinHeader_2.54mm:PinHeader_1x04_P2.54mm_Vertical"
    run(P + ["sch", "add", "Connector_Generic:Conn_01x04", "--ref", "J1", "--at", "50.8,50.8", "--footprint", fp])
    run(P + ["sch", "add", "Connector_Generic:Conn_01x04", "--ref", "J2", "--at", "101.6,50.8", "--mirror", "y",
             "--footprint", fp])
    for j in ("J1", "J2"):
        run(P + ["sch", "label-pin", f"{j}:1", "GND", "--kind", "global"])
        run(P + ["sch", "label-pin", f"{j}:2", "USB_D+"])
        run(P + ["sch", "label-pin", f"{j}:3", "USB_D-"])
        run(P + ["sch", "label-pin", f"{j}:4", "GND", "--kind", "global"])
    run(P + ["pcb", "outline", "rect", "--at", "100,100", "--size", "60,30"])
    run(P + ["pcb", "update"])
    run(P + ["pcb", "move", "J1", "--at", "110,110"])
    run(P + ["pcb", "move", "J2", "--at", "150,112"])
    return p


def _row(edit):
    """A change to the tuning profile row write_tuning_profile wrote, as an edit of the .kicad_pro text."""
    def apply(text):
        data = json.loads(text)
        edit(data["tuning_profiles"]["tuning_profiles_impedance_geometric"])
        return json.dumps(data, indent=2)
    return apply


def _second_row(**fields):
    return _row(lambda rows: rows.append(dict(rows[0], profile_name="RG50", **fields)))


# damage: (edit of the .kicad_pro text, words the error must name). Each is a file KiCad 10.0.8 does not read
# whole: it opens the board without the project's net classes, stops reading tuning profiles at the row, aborts at
# a missing field, or stops loading the board.
UNREADABLE = {
    "cut short": (lambda text: text[:len(text) // 2], ["Cannot parse"]),
    "NaN": (lambda text: text.replace('"target_impedance": 50.0', '"target_impedance": NaN'), ["NaN"]),
    "Infinity": (lambda text: text.replace('"target_impedance": 50.0', '"target_impedance": Infinity'), ["Infinity"]),
    "NUL inside a line comment": (lambda text: "// a\0 b\n" + text, ["Cannot parse"]),
    "top-level array": (lambda text: "[" + text + "]", ["not a JSON object"]),
    "unpaired surrogate": (lambda text: text.replace('"tuning_profiles": {', '"x": "\\ud800", "tuning_profiles": {', 1),
                           ["surrogate"]),
    "flag 1": (_row(lambda rows: rows[0].update(enable_time_domain_tuning=1)),
               ["RF50", "row 1", "enable_time_domain_tuning"]),
    "via_prop_delay string": (_row(lambda rows: rows[0].update(via_prop_delay="8000000")),
                              ["RF50", "row 1", "via_prop_delay"]),
    "target_impedance null": (_row(lambda rows: rows[0].update(target_impedance=None)), ["RF50", "target_impedance"]),
    "type string": (_row(lambda rows: rows[0].update(type="single")), ["RF50", "type"]),
    "type true": (_row(lambda rows: rows[0].update(type=True)), ["RF50", "row 1", "type true"]),
    "number beyond a double": (lambda text: text.replace('"target_impedance": 50.0', '"target_impedance": 1e400'),
                               ["number overflow parsing '1e400'", "stops loading"]),
    "integer beyond a double": (
        lambda text: text.replace('"target_impedance": 50.0', '"target_impedance": 1' + "0" * 399),
        ["number overflow parsing '1000", "stops loading"]),
    "layer delay string": (_row(lambda rows: rows[0]["layer_entries"][0].update(delay="6000000")),
                           ["RF50", "layer_entries[0].delay"]),
    "signal_layer number": (_row(lambda rows: rows[0]["layer_entries"][1].update(signal_layer=0)),
                            ["RF50", "layer_entries[1].signal_layer"]),
    "override delay string": (_row(lambda rows: rows[0]["via_overrides"][0].update(delay="9000000")),
                              ["RF50", "via_overrides[0].delay"]),
    "profile_name number": (_row(lambda rows: rows.insert(0, dict(rows[0], profile_name=5))),
                            ["row 1", "profile_name"]),
    "via_overrides missing": (_row(lambda rows: rows[0].pop("via_overrides")), ["RF50", "no via_overrides"]),
    "layer delay missing": (_row(lambda rows: rows[0]["layer_entries"][1].pop("delay")),
                            ["RF50", "no layer_entries[1].delay"]),
    "second row bad": (_second_row(via_prop_delay="x"), ["RG50", "row 2", "via_prop_delay"]),
}


class TestDiffPairsAndLengths:
    def test_route_rules_and_tuning(self, tmp_dir):
        p = build_pair_project(tmp_dir)
        P = ["--project", p]
        pairs = rj(P + ["pcb", "diffpair", "list"])
        assert [(x["pair"], x["p"], x["n"], x["routed"]) for x in pairs] == [("/USB_D", "/USB_D+", "/USB_D-", False)]
        r = rj(P + ["pcb", "diffpair", "route", "/USB_D", "--from", "J1", "--to", "J2"])
        assert r["skew"] == pytest.approx(0, abs=1e-3) and r["gap"] == pytest.approx(0.25)
        conn = rj(P + ["pcb", "connectivity"])
        assert {n["net"] for n in conn["nets"]} == {"GND"}  # both pair nets fully routed
        d = drc(p, "--no-parity")
        assert set(d["by_type"]) <= {"unconnected_items"}, d["by_type"]
        rj(P + ["pcb", "length", "rule", "usb_len", "--nets", "/USB_D*", "--min", "45", "--max", "50"])
        rule = rj(P + ["pcb", "diffpair", "rule", "/USB_D", "--gap", "0.25", "--max-skew", "0.1"])
        assert "inDiffPair('/USB_D')" in rule["rule"]
        assert drc(p, "--no-parity")["by_type"].get("length_out_of_range") == 2  # KiCad enforces the rule
        t1 = rj(P + ["pcb", "length", "tune", "/USB_D+", "--target", "46"])
        assert t1["length"] == pytest.approx(46, abs=0.01) and t1["tuned_segments"]
        t2 = rj(P + ["pcb", "length", "tune", "/USB_D-", "--match", "/USB_D+"])
        assert t2["length"] == pytest.approx(t1["length"], abs=0.01)
        rep = rj(P + ["pcb", "length", "report", "--net", "/USB*"])
        assert rep["diff_pairs"][0]["skew"] < 0.02 and len(rep["nets"]) == 2
        d = drc(p, "--no-parity")
        assert "length_out_of_range" not in d["by_type"] and "skew_out_of_range" not in d["by_type"]
        assert "clearance" not in d["by_type"] and "tracks_crossing" not in d["by_type"]

    def test_report_splits_length_by_layer_with_kicad_delay_and_flags_branches(self, rf):
        P = ["--project", rf]
        b_cu, tee = math.hypot(124 - 100, 167 - 160), (140 - 100) + (188 - 180)

        nets = {n["net"]: n for n in rj(P + ["pcb", "length", "report"])["nets"]}
        assert nets["RF1"]["delay_ps"] is None and 'profile "RF50", which the project does not define' in \
            nets["RF1"]["delay_reason"]

        write_tuning_profile(rf, enabled=True)
        nets = {n["net"]: n for n in rj(P + ["pcb", "length", "report"])["nets"]}
        rf1, rf2 = nets["RF1"], nets["RF2"]
        assert rf1["length"] == pytest.approx(RF1_F_CU + RF1_IN1_CU) and rf1["vias"] == 1
        assert [(r["layer"], r["length"], r["delay_ps"]) for r in rf1["by_layer"]] == [
            ("F.Cu", pytest.approx(RF1_F_CU), pytest.approx(RF1_F_CU * F_CU_PS_PER_MM)),
            ("In1.Cu", pytest.approx(RF1_IN1_CU), pytest.approx(RF1_IN1_CU * IN1_CU_PS_PER_MM))]
        assert rf1["via_delay_ps"] == pytest.approx(VIA_PS) and rf1["pad_to_die_delay_ps"] == 0
        assert rf1["delay_ps"] == pytest.approx(RF1_DELAY_PS)
        assert (rf1["tuning_profile"], rf1["delay_reason"]) == ("RF50", None)
        assert (rf1["off_path_mm"], rf1["branched"], rf1["branched_reason"]) == (0, False, None)
        reach = nets["RF_VIA_REACH"]  # its In1.Cu track ends outside the via, with its copper touching the via
        assert (reach["via_delay_ps"], reach["delay_reason"]) == (pytest.approx(VIA_PS), None)
        assert [(r["layer"], r["length"], r["delay_ps"]) for r in rf2["by_layer"]] == [
            ("B.Cu", pytest.approx(b_cu), None)]
        assert rf2["via_delay_ps"] is None and rf2["delay_ps"] is None
        assert "B.Cu" in rf2["delay_reason"] and "(124.0, 167.0)" in rf2["delay_reason"]
        assert [(r["layer"], r["length"]) for r in nets["TEE"]["by_layer"]] == [("F.Cu", pytest.approx(tee))]
        assert (nets["TEE"]["off_path_mm"], nets["TEE"]["branched"]) == (pytest.approx(188 - 180), True)
        assert "8.0 mm of track lies off the shortest route from H" in nets["TEE"]["branched_reason"]
        assert (nets["VIA_TEE"]["off_path_mm"], nets["VIA_TEE"]["branched"]) == (pytest.approx(210 - 200 - 0.3), True)
        assert nets["TEE"]["delay_ps"] is None and nets["TEE"]["via_delay_ps"] is None
        assert nets["TEE"]["tuning_profile"] is None and "Default" in nets["TEE"]["delay_reason"]
        p2d, holds = nets["RF_P2D"], nets["RF_VIA_HOLDS_PAD"]
        assert p2d["delay_ps"] is None and "pad RF_P2D1:1 has a pad-to-die length but no pad-to-die delay" in \
            p2d["delay_reason"]
        assert holds["via_delay_ps"] is None and "pad around the via at (110.0, 690.0)" in holds["delay_reason"]

        write_tuning_profile(rf, enabled=False)
        [rf1] = rj(P + ["pcb", "length", "report", "--net", "RF1"])["nets"]
        assert [r["delay_ps"] for r in rf1["by_layer"]] == [None, None]
        assert rf1["via_delay_ps"] is None and rf1["delay_ps"] is None and "turned off" in rf1["delay_reason"]

    def test_branched_measures_the_track_off_the_route_between_two_pads(self, rf):
        nets = {n["net"]: n for n in rj(["--project", rf, "pcb", "length", "report"])["nets"]}
        assert {name: (nets[name]["length"], nets[name]["off_path_mm"], nets[name]["branched"]) for name in ROUTES} == {
            name: (pytest.approx(length, abs=1e-5), off if off is None else pytest.approx(off, abs=1e-4), branched)
            for name, (length, off, branched, _) in ROUTES.items()}
        assert {name: nets[name]["branched_reason"] for name in ("THREE_PADS", "NO_PADS", "APART", "NO_TRACKS")} == {
            "THREE_PADS": "the net joins 3 pads, so it has no single route",
            "NO_PADS": "the net joins no pads, so it has no single route",
            "APART": "its tracks do not join APART1:1 to APART2:1", "NO_TRACKS": None}
        assert nets["STUB_0202"]["branched_reason"] == \
            "0.202 mm of track lies off the shortest route from STUB_02021:1 to STUB_02022:1"

    @pytest.mark.parametrize("demo", ["video", "vme-wren"])
    def test_off_route_does_not_depend_on_how_the_tracks_are_cut(self, tmp_dir, demo):
        def report(project):
            nets = rj(["--project", project, "pcb", "length", "report"])["nets"]
            return {n["net"]: (n["off_path_mm"], n["branched"]) for n in nets}, sum(n["segments"] for n in nets)

        drawn, segments = report(demo_copy(demo, os.path.join(tmp_dir, "drawn")))
        for name, code in (("cut", CUT_EVERY_TRACK), ("joined", JOIN_COLLINEAR_SEGMENTS)):
            project = demo_copy(demo, os.path.join(tmp_dir, name))
            actions = os.path.join(tmp_dir, name + ".json")
            with open(actions, "w", encoding="utf-8") as f:
                json.dump([{"action": "script", "code": code, "params": {"seed": 14}}], f)
            run(["--project", project, "pcb", "batch", actions])
            recut, count = report(project)
            assert count > segments if name == "cut" else count < segments
            # KiCad stores the points of a cut arc in whole nanometres, so they lie a nanometre or so off its circle
            assert recut == {net: (off if off is None else pytest.approx(off, abs=1e-5), branched)
                             for net, (off, branched) in drawn.items()}

    def test_report_reads_tuning_profile_names_as_kicad_does(self, rf):
        write_tuning_profile(rf, enabled=True)
        pro = os.path.join(rf, "rf.kicad_pro")
        data = json.loads(read_text(pro))
        rows = data["tuning_profiles"]["tuning_profiles_impedance_geometric"]
        rows.append(dict(rows[0], enable_time_domain_tuning=False))  # KiCad keeps the last row of a name
        with open(pro, "w", encoding="utf-8") as f:
            json.dump(data, f)
        [rf1] = rj(["--project", rf, "pcb", "length", "report", "--net", "RF1"])["nets"]
        assert rf1["delay_ps"] is None
        assert rf1["delay_reason"] == 'tuning profile "RF50" has time-domain tuning turned off'

        rows[1:] = [dict(rows[0], profile_name="")]  # KiCad looks up a class's empty profile name too
        rows[0]["profile_name"] = "RF50\0 row"  # and reads a name up to a NUL
        next(c for c in data["net_settings"]["classes"] if c["name"] == "RF")["tuning_profile"] = "RF50\0 class"
        with open(pro, "w", encoding="utf-8") as f:
            json.dump(data, f)
        nets = {n["net"]: n for n in rj(["--project", rf, "pcb", "length", "report"])["nets"]}
        assert (nets["RF1"]["tuning_profile"], nets["RF1"]["delay_ps"]) == ("RF50", pytest.approx(RF1_DELAY_PS))
        tee = (140 - 100) + (188 - 180)
        assert (nets["TEE"]["tuning_profile"], nets["TEE"]["delay_ps"]) == ("", pytest.approx(tee * F_CU_PS_PER_MM))

    def test_report_reads_a_project_file_with_a_bom_and_comments_as_kicad_does(self, rf):
        name = 'RF//50 /* not a comment */ "q"'
        write_tuning_profile(rf, enabled=True, profile=name)
        pro = os.path.join(rf, "rf.kicad_pro")
        data = json.loads(read_text(pro))
        [row] = data["tuning_profiles"]["tuning_profiles_impedance_geometric"]
        row["layer_entries"][0]["diff_pair_gap"] = True  # KiCad reads a C++ int from true, as 1
        row["layer_entries"].append(7)  # KiCad skips a layer entry that is not an object
        row["via_overrides"].append({"delay": 1})  # and a via override without signal_layer_from
        data["tuning_profiles"]["tuning_profiles_impedance_geometric"].insert(0, {"type": "x"})  # and a nameless row
        next(c for c in data["net_settings"]["classes"] if c["name"] == "RF")["tuning_profile"] = name
        text = json.dumps(data, indent=2).replace('"tuning_profiles": {', '/* RF */ "tuning_profiles": {', 1)
        with open(pro, "w", encoding="utf-8") as f:
            f.write("\ufeff// edited by hand\n// a NUL ends this comment\0\n" + text + "\0 and KiCad's input")
        [rf1] = rj(["--project", rf, "pcb", "length", "report", "--net", "RF1"])["nets"]
        assert (rf1["tuning_profile"], rf1["delay_reason"]) == (name, None)
        assert rf1["delay_ps"] == pytest.approx(RF1_DELAY_PS)

    @pytest.mark.parametrize("damage", UNREADABLE)
    def test_report_refuses_a_project_file_it_cannot_read(self, rf, damage):
        write_tuning_profile(rf, enabled=True)
        pro = os.path.join(rf, "rf.kicad_pro")
        edit, words = UNREADABLE[damage]
        text = edit(read_text(pro))
        with open(pro, "w", encoding="utf-8") as f:
            f.write(text)
        r = run(["--json", "--project", rf, "pcb", "length", "report"], check=False)
        assert r.returncode == 2, r.stdout[:300]
        error = json.loads(r.stderr)
        assert error["type"] == "InvalidInput" and "rf.kicad_pro" in error["error"]
        assert all(w in error["error"] for w in words), error["error"]

    def test_resident_server_reports_the_tuning_profile_the_project_names_now(self, rf):
        write_tuning_profile(rf, enabled=True)
        report = {"jsonrpc": "2.0", "method": "run", "params": {"args": ["pcb", "length", "report", "--net", "RF1"]}}
        rename = {"jsonrpc": "2.0", "id": 2, "method": "run",
                  "params": {"args": ["project", "netclass", "set", "RF", "--set", "tuning_profile=OTHER"]}}
        requests = [dict(report, id=1), rename, dict(report, id=3), {"jsonrpc": "2.0", "id": 4, "method": "shutdown"}]
        out = run(["--project", rf, "serve"], input="".join(json.dumps(r) + "\n" for r in requests)).stdout
        results = {r["id"]: r["result"] for r in map(json.loads, out.splitlines())}
        [before], [after] = results[1]["nets"], results[3]["nets"]
        assert before["tuning_profile"] == "RF50" and before["delay_ps"] is not None
        assert after["tuning_profile"] == "OTHER" and after["delay_ps"] is None and "OTHER" in after["delay_reason"]


@pytest.fixture(scope="module")
def rf_master():
    root = tempfile.mkdtemp(prefix="kce-rf-")
    yield build_rf_project(root)
    if os.environ.get(ENV_PREFIX + "KEEP_ARTIFACTS") != "1":
        shutil.rmtree(root, ignore_errors=True)


@pytest.fixture
def rf(rf_master, tmp_dir):
    dst = os.path.join(tmp_dir, "rf")
    shutil.copytree(rf_master, dst, ignore=shutil.ignore_patterns("outputs", STATE_DIR))
    return dst


AS_PER_PS = 10**6  # KiCad keeps time in attoseconds
F_CU_PS_PER_MM, IN1_CU_PS_PER_MM, VIA_PS = 6.0, 7.1, 9.0
RF1_F_CU, RF1_IN1_CU = math.hypot(112 - 100, 116 - 100) + math.hypot(130 - 112, 140 - 116), 150 - 130
RF1_DELAY_PS = RF1_F_CU * F_CU_PS_PER_MM + RF1_IN1_CU * IN1_CU_PS_PER_MM + VIA_PS


def build_rf_project(root: str) -> str:
    p = os.path.join(root, "rf")
    run(["project", "new", p, "--name", "rf"])
    P = ["--project", p]
    run(P + ["pcb", "set-layers", "4"])
    run(P + ["pcb", "track", "100,100;112,116;130,140", "--layer", "F.Cu", "--net", "RF1"])
    run(P + ["pcb", "via", "--at", "130,140", "--net", "RF1"])
    run(P + ["pcb", "track", "130,140;150,140", "--layer", "In1.Cu", "--net", "RF1"])
    run(P + ["pcb", "track", "100,160;124,167", "--layer", "B.Cu", "--net", "RF2"])
    run(P + ["pcb", "via", "--at", "124,167", "--net", "RF2"])
    run(P + ["pcb", "track", "100,180;140,180", "--net", "TEE"])
    run(P + ["pcb", "track", "120,180;120,188", "--net", "TEE"])
    run(P + ["pcb", "track", "100,200;140,200", "--net", "VIA_TEE"])
    run(P + ["pcb", "via", "--at", "120,200", "--net", "VIA_TEE"])
    run(P + ["pcb", "track", "120,200;120,210", "--layer", "In1.Cu", "--net", "VIA_TEE"])
    for net, ends in (("RF1", ("100,100", "150,140")), ("TEE", ("100,180", "140,180")),
                      ("VIA_TEE", ("100,200", "140,200"))):
        for at in ends:
            run(P + ["pcb", "hole", "--at", at, "--drill", "0.4", "--pad", "1", "--net", net])
    with open(os.path.join(root, "routes.json"), "w", encoding="utf-8") as f:
        json.dump([action for *_, actions in ROUTES.values() for action in actions] + DELAY_CASES, f)
    run(P + ["pcb", "batch", os.path.join(root, "routes.json")])
    run(P + ["project", "netclass", "set", "RF", "--set", "tuning_profile=RF50"])
    run(P + ["project", "netclass", "assign", "RF*", "RF"])
    return p


W = 0.2


def _track(net, *points, layer="F.Cu", width=W):
    return {"action": "add_track", "net": net, "layer": layer, "width": width,
            "points": [[round(x, 6), round(y, 6)] for x, y in points]}


def _via(net, x, y, diameter=0.6, drill=0.3):
    return {"action": "add_via", "net": net, "x": x, "y": y, "diameter": diameter, "drill": drill}


def _pad(net, ref, x, y, size=1.0, layer="F.Cu", number="1"):
    """A rectangular SMD pad on one copper layer, in footprint ref: size is its side or (width, height)."""
    w, h = size if isinstance(size, tuple) else (size, size)
    code = "\n".join([
        f"net = board.FindNet({net!r})",
        "if net is None:",
        f"    net = pcbnew.NETINFO_ITEM(board, {net!r})",
        "    board.Add(net)",
        f"fp = board.FindFootprintByReference({ref!r})",
        "if fp is None:",
        "    fp = pcbnew.FOOTPRINT(board)",
        f"    fp.SetReference({ref!r})",
        "    board.Add(fp)",
        f"    fp.SetPosition(vec({x}, {y}))",
        "p = pcbnew.PAD(fp)",
        f"p.SetNumber({number!r})",
        "p.SetAttribute(pcbnew.PAD_ATTRIB_SMD)",
        "layers = pcbnew.LSET()",
        f"layers.AddLayer(pcbnew.{layer.replace('.', '_')})",
        "p.SetLayerSet(layers)",
        "p.SetShape(pcbnew.F_Cu, pcbnew.PAD_SHAPE_RECTANGLE)",
        f"p.SetSize(pcbnew.F_Cu, vec({w}, {h}))",
        "fp.Add(p)",
        f"p.SetPosition(vec({x}, {y}))",
        "p.SetNet(net)"])
    return {"action": "script", "code": code}


def _ends(net, a, b, **pad):
    return [_pad(net, f"{net}1", *a, **pad), _pad(net, f"{net}2", *b, **pad)]


def _arc(net, start, mid, end):
    code = (f"a = pcbnew.PCB_ARC(board); a.SetStart(vec(*{start})); a.SetMid(vec(*{mid})); a.SetEnd(vec(*{end}))\n"
            f"a.SetWidth(FromMM({W})); a.SetLayer(pcbnew.F_Cu); a.SetNet(board.FindNet({net!r})); board.Add(a)")
    return {"action": "script", "code": code}


def _circle(cx, cy, r, a0, a1, n):
    """n segments' worth of points on a circle, from angle a0 to a1 (degrees), to the nanometre."""
    return [(round(cx + r * math.cos(math.radians(a0 + (a1 - a0) * i / n)), 6),
             round(cy + r * math.sin(math.radians(a0 + (a1 - a0) * i / n)), 6)) for i in range(n + 1)]


def _polyline_length(points):
    return sum(math.dist(a, b) for a, b in zip(points, points[1:]))


R, H = 0.135, 0.135 * math.sqrt(0.5)  # a quarter arc this tight is longer than 0.2 mm, its chord shorter
CHAIN = [(round(100 + 0.15 * i, 6), 300) for i in range(51)]
BEND = _circle(100, 316, 1, -90, 0, 32)  # chords of 0.049 mm on a 0.2 mm track: each end touches the next four
RING_UP, RING_DOWN = _circle(108, 350, 3, 180, 360, 48), _circle(108, 350, 3, 180, 0, 48)
CROSS_R = math.hypot(4, 0.5)  # an arc from a track's end, around and back across it at x = 102
CROSS_END = (round(106 - math.sqrt(CROSS_R ** 2 - 1.3 ** 2), 6), 721.8)
CROSS_TURN = math.atan2(0.5, 4) + 2 * math.pi - math.atan2(1.3, CROSS_END[0] - 106)
CROSS_AT = math.atan2(0.5, 4) + 2 * math.pi - math.atan2(0.5, -4)
TOUCH_R = (0.4 ** 2 + 1.5 ** 2) / (2 * 1.5)  # an arc 1.5 mm high on a 0.8 mm chord
TOUCH_ARC = TOUCH_R * (2 * math.pi - 2 * math.asin(0.4 / TOUCH_R))
TOUCH_CY = 733.5 + TOUCH_R  # the arc's centre, below its top at 733.5; the pads' top edge is at 734.5
TOUCH_IN_PAD = TOUCH_R * (math.pi - math.asin((734.5 - TOUCH_CY) / TOUCH_R) - math.atan2(735 - TOUCH_CY, 100 - 100.4))
CARD_EDGE_FINGER = (6, 1.016)
ARC_C, ARC_R = (108, -1.15), 1  # an arc's middle 0.15 mm from a track: their copper touches only there


# net: (length, off_path_mm, branched, how it is built). Each route runs between two pads, apart from the last
# four nets, which show what makes a net branched without a route. The tracks are 0.2 mm wide unless said.
ROUTES = {
    "CHAIN_STUB": (25, 5, True, [_track("CHAIN_STUB", *CHAIN), _track("CHAIN_STUB", (107.5, 300), (120, 300)),
                                 _track("CHAIN_STUB", (103.75, 300), (103.75, 305)),
                                 *_ends("CHAIN_STUB", (100, 300), (120, 300))]),
    "TESS_BEND": (_polyline_length(BEND) + 4, 0, False, [
        _track("TESS_BEND", *BEND), _track("TESS_BEND", (101, 316), (101, 320)),
        *_ends("TESS_BEND", (100, 315), (101, 320))]),
    "TESS_BEND_STUB": (_polyline_length(BEND) + 4 + 3, 3, True, [
        _track("TESS_BEND_STUB", *((x, y + 15) for x, y in BEND)), _track("TESS_BEND_STUB", (101, 331), (101, 335)),
        _track("TESS_BEND_STUB", (BEND[16][0], BEND[16][1] + 15),
               (BEND[16][0] + 3 * math.sqrt(0.5), BEND[16][1] + 15 - 3 * math.sqrt(0.5))),
        *_ends("TESS_BEND_STUB", (100, 330), (101, 335))]),
    "TESS_RING": (10 + _polyline_length(RING_UP) + _polyline_length(RING_DOWN), _polyline_length(RING_UP), True, [
        _track("TESS_RING", (100, 350), (105, 350)), _track("TESS_RING", (111, 350), (116, 350)),
        _track("TESS_RING", *RING_UP), _track("TESS_RING", *RING_DOWN), *_ends("TESS_RING", (100, 350), (116, 350))]),
    **{f"STUB_{tag}": (20 + d, d, d > 0.2, [
        _track(f"STUB_{tag}", (100, y), (120, y)), _track(f"STUB_{tag}", (110, y), (110, y + d)),
        *_ends(f"STUB_{tag}", (100, y), (120, y))]) for tag, d, y in (("0198", 0.198, 365), ("0202", 0.202, 375))},
    "DUP": (40, 20 - 1, True, [_track("DUP", (100, 385), (120, 385)), _track("DUP", (120, 385), (100, 385)),
                           *_ends("DUP", (100, 385), (120, 385))]),
    **{f"OVERHANG_{tag}": (30, d, d > 0.2, [
        _track(f"OVERHANG_{tag}", (100, y), (120, y)), _track(f"OVERHANG_{tag}", (120 - d, y), (120 - d, y + 10)),
        *_ends(f"OVERHANG_{tag}", (100, y), (120 - d, y + 10))]) for tag, d, y in (("0198", 0.198, 395),
                                                                                   ("0202", 0.202, 410))},
    "VIA_FANOUT": (20, 0, False, [
        _via("VIA_FANOUT", 110, 425), _track("VIA_FANOUT", (110, 425), (110.35, 425), (120, 425)),
        _track("VIA_FANOUT", (110, 425), (100, 425), layer="B.Cu"),
        _pad("VIA_FANOUT", "VIA_FANOUT1", 120, 425), _pad("VIA_FANOUT", "VIA_FANOUT2", 100, 425, layer="B.Cu")]),
    "PAD_FANOUT": (math.hypot(0.5, 0.5) + 9.5, 0, False, [
        _track("PAD_FANOUT", (110, 440), (110.5, 440.5), (120, 440.5)),
        _pad("PAD_FANOUT", "PAD_FANOUT1", 110, 440, size=2), _pad("PAD_FANOUT", "PAD_FANOUT2", 120, 440.5)]),
    "DOUBLED_IN_PAD": (10.8, 0, False, [
        _track("DOUBLED_IN_PAD", (110, 455), (120, 455)), _track("DOUBLED_IN_PAD", (110, 455), (110.8, 455)),
        _pad("DOUBLED_IN_PAD", "DOUBLED_IN_PAD1", 110, 455, size=2),
        _pad("DOUBLED_IN_PAD", "DOUBLED_IN_PAD2", 120, 455)]),
    "STUB_NEAR_VIA": (30, 10 - 0.3, True, [  # the B.Cu track ends outside the via, its copper on the via
        _via("STUB_NEAR_VIA", 110, 470), _track("STUB_NEAR_VIA", (100, 470), (110, 470)),
        _track("STUB_NEAR_VIA", (110, 470), (120, 470), layer="In1.Cu"),
        _track("STUB_NEAR_VIA", (110.35, 470), (110.35, 480), layer="B.Cu"),
        _pad("STUB_NEAR_VIA", "STUB_NEAR_VIA1", 100, 470),
        _pad("STUB_NEAR_VIA", "STUB_NEAR_VIA2", 110.35, 480, layer="B.Cu")]),
    "STUB_NEAR_PAD": (29.45, 20 - 1, True, [  # the stub ends outside the pad, its copper on the pad
        _track("STUB_NEAR_PAD", (100, 490), (110, 490)), _track("STUB_NEAR_PAD", (110, 490), (120, 490)),
        _track("STUB_NEAR_PAD", (110, 490.55), (110, 500)), *_ends("STUB_NEAR_PAD", (110, 490), (110, 500))]),
    "PASS_OVER_PAD": (20, 10 - 0.5, True, [
        _track("PASS_OVER_PAD", (100, 505.3), (120, 505.3)), *_ends("PASS_OVER_PAD", (110, 505), (100, 505.3))]),
    "PASS_OVER_VIA": (20, 5 - math.sqrt(0.3 ** 2 - 0.2 ** 2), True, [
        _via("PASS_OVER_VIA", 110, 520), _track("PASS_OVER_VIA", (100, 520), (110, 520)),
        _track("PASS_OVER_VIA", (110.2, 515), (110.2, 525), layer="B.Cu"),
        _pad("PASS_OVER_VIA", "PASS_OVER_VIA1", 100, 520),
        _pad("PASS_OVER_VIA", "PASS_OVER_VIA2", 110.2, 525, layer="B.Cu")]),
    "VIA_LOOP": (50, 20 - 2 * 0.3, True, [
        _via("VIA_LOOP", 100, 540), _via("VIA_LOOP", 120, 540), _track("VIA_LOOP", (95, 540), (100, 540)),
        _track("VIA_LOOP", (100, 540), (120, 540)), _track("VIA_LOOP", (100, 540), (120, 540), layer="B.Cu"),
        _track("VIA_LOOP", (120, 540), (125, 540)), *_ends("VIA_LOOP", (95, 540), (125, 540))]),
    "ARC_CORNER": (10 + R * math.pi / 2 + (10 - R), 0, False, [
        _track("ARC_CORNER", (100, 555), (110, 555)), _arc("ARC_CORNER", (110, 555), (110 + H, 555 + R - H),
                                                          (110 + R, 555 + R)),
        _track("ARC_CORNER", (110 + R, 555 + R), (110 + R, 565)), *_ends("ARC_CORNER", (100, 555), (110 + R, 565))]),
    "ARC_STUB": (20 + R * math.pi / 2, R * math.pi / 2, True, [
        _track("ARC_STUB", (100, 570), (120, 570)), _arc("ARC_STUB", (110, 570), (110 + R - H, 570 + H),
                                                        (110 + R, 570 + R)),
        *_ends("ARC_STUB", (100, 570), (120, 570))]),
    "WIDE_OVER_VIA": (26.4, 1.4 - 0.3, True, [  # a 1 mm B.Cu track runs over a via where F.Cu and In1.Cu tracks end
        _track("WIDE_OVER_VIA", (100, 585), (110, 585)), _via("WIDE_OVER_VIA", 110, 585, 0.3, 0.15),
        _track("WIDE_OVER_VIA", (110, 585), (120, 585), layer="In1.Cu"), _via("WIDE_OVER_VIA", 120, 585),
        _track("WIDE_OVER_VIA", (120, 585), (125, 585)),
        _track("WIDE_OVER_VIA", (110, 584.3), (110, 585.7), layer="B.Cu", width=1.0),
        *_ends("WIDE_OVER_VIA", (100, 585), (125, 585))]),
    "SAME_NUMBER": (10, 0, False, [  # pads of one footprint with one number are one pad
        _track("SAME_NUMBER", (100, 600), (110, 600)), _pad("SAME_NUMBER", "SN1", 100, 600),
        _pad("SAME_NUMBER", "SN1", 100, 600, layer="B.Cu"), _pad("SAME_NUMBER", "SN2", 110, 600)]),
    "CROSS_AFTER_END": (10 + CROSS_R * CROSS_TURN, 10 - 2 + CROSS_R * CROSS_AT, True, [
        _track("CROSS_AFTER_END", (100, 721), (110, 721)),
        _arc("CROSS_AFTER_END", (110, 721), (106, 721 - 0.5 - CROSS_R), CROSS_END),
        _pad("CROSS_AFTER_END", "CROSS_AFTER_END1", 100, 721),
        _pad("CROSS_AFTER_END", "CROSS_AFTER_END2", *CROSS_END)]),
    "PAD_TO_TOUCHING_PAD": (TOUCH_ARC, TOUCH_ARC - 2 * TOUCH_IN_PAD, True, [
        _arc("PAD_TO_TOUCHING_PAD", (100, 735), (100.4, 733.5), (100.8, 735)),
        *_ends("PAD_TO_TOUCHING_PAD", (100, 735), (100.8, 735))]),
    "LOOP_AT_WIDE_TRACK": (15 + 1.65, 1.65, True, [  # a 0.1 mm loop on a 1 mm track
        _track("LOOP_AT_WIDE_TRACK", (100, 750), (110, 750), (115, 750), width=1),
        _track("LOOP_AT_WIDE_TRACK", (110, 750), (110, 749.3), (109.75, 749.3), (109.75, 750), width=0.1),
        *_ends("LOOP_AT_WIDE_TRACK", (100, 750), (115, 750), size=1.5)]),
    **{net: (length, max(0, 100 + length - (120 + CARD_EDGE_FINGER[0] / 2)), False, [
        *(_track(net, a, b) for a, b in segments), _pad(net, f"{net}1", 120, y, size=CARD_EDGE_FINGER),
        _pad(net, f"{net}2", 100, y)])
       for net, y, segments, length in (
           ("FINGER_TWO", 765, [((100, 765), (120, 765)), ((120, 765), (122.667, 765))], 22.667),
           ("FINGER_ONE", 780, [((100, 780), (122.667, 780))], 22.667),
           ("FINGER_EDGE_IN", 795, [((100, 795), (123.05, 795))], 23.05),
           ("FINGER_EDGE_OUT", 810, [((100, 810), (123.15, 810))], 23.15))},
    **{net: (10.4, 0, False, [*(_track(net, a, b) for a, b in segments), *_ends(net, (110, y), (100, y))])
       for net, y, segments in (("SMD_PAST_CENTRE_ONE", 825, [((100, 825), (110.4, 825))]),
                                ("SMD_PAST_CENTRE_TWO", 840, [((100, 840), (110, 840)), ((110, 840), (110.4, 840))]))},
    **{net: (20.25, 0, False, [
        *(_track(net, a, b) for a, b in segments), _via(net, 110, y), _track(net, (110, y), (120, y), layer="B.Cu"),
        _pad(net, f"{net}1", 100, y), _pad(net, f"{net}2", 120, y, layer="B.Cu")])
       for net, y, segments in (("VIA_OVERSHOOT_ONE", 855, [((100, 855), (110.25, 855))]),
                                ("VIA_OVERSHOOT_TWO", 870, [((100, 870), (110, 870)), ((110, 870), (110.25, 870))]))},
    # a U on a 1 mm track whose legs, 0.8 mm apart, touch: a step across counts twice its length, so the route
    # cuts across where the U's track is longer than 1.6 mm
    **{net: (10 + 2 * h, 2 * h + 0.8 if 2 * h + 0.8 > 2 * 0.8 else 0, 2 * h + 0.8 > 2 * 0.8, [
        _track(net, (100, y), (104.6, y), (104.6, y - h), (105.4, y - h), (105.4, y), (110, y), width=1),
        *_ends(net, (100, y), (110, y), size=1.5)]) for net, h, y in (("WIDE_NOTCH_LOW", 0.3, 885),
                                                                     ("WIDE_NOTCH_MID", 0.5, 900))},
    "VIA_RING_TRACK": (20.28, 0, False, [  # KiCad joins the second B.Cu track only to the first, inside the ring
        _track("VIA_RING_TRACK", (100, 930), (110, 930)), _via("VIA_RING_TRACK", 110, 930),
        {"action": "script", "code": "[v] = [t for t in board.GetTracks() if t.GetClass() == 'PCB_VIA' and "
                                     "t.GetNetname() == 'VIA_RING_TRACK']\n"
                                     "v.Padstack().SetUnconnectedLayerMode(pcbnew.UNCONNECTED_LAYER_MODE_REMOVE_ALL)"},
        _track("VIA_RING_TRACK", (110, 930), (110.28, 930), layer="B.Cu"),
        _track("VIA_RING_TRACK", (110.28, 930), (110.28, 940), layer="B.Cu"),
        _pad("VIA_RING_TRACK", "VIA_RING_TRACK1", 100, 930),
        _pad("VIA_RING_TRACK", "VIA_RING_TRACK2", 110.28, 940, layer="B.Cu")]),
    **{net: (10 + ARC_R * math.pi / 2 + 5 + ARC_C[1] + ARC_R * math.sqrt(0.5), 2 + ARC_R * math.pi / 4, True, [
        _track(net, (100, y), (110, y)),
        *(_arc(net, *_circle(ARC_C[0], y + ARC_C[1], ARC_R, a0, a1, 2)) for a0, a1 in zip(cuts, cuts[1:])),
        _track(net, _circle(ARC_C[0], y + ARC_C[1], ARC_R, 135, 135, 1)[0], (ARC_C[0] - ARC_R * math.sqrt(0.5), y - 5)),
        _pad(net, f"{net}1", 100, y), _pad(net, f"{net}2", ARC_C[0] - ARC_R * math.sqrt(0.5), y - 5)])
       for net, y, cuts in (("ARC_NEAR_TRACK", 965, (45, 135)), ("ARC_NEAR_TRACK_CUT", 980, (45, 100, 135)))},
    "PAD_VIA_LOOP": (2 + 9.3, 0.3 + 0.4 + 0.5, True, [  # the via's copper touches the pad's, so the loop is off
        _pad("PAD_VIA_LOOP", "PAD_VIA_LOOP1", 100, 995), _via("PAD_VIA_LOOP", 100.7, 995),
        _track("PAD_VIA_LOOP", (100.3, 995), (100.3, 995.8), (100.7, 995.8), (100.7, 995)),
        _track("PAD_VIA_LOOP", (100.7, 995), (110, 995), layer="B.Cu"),
        _pad("PAD_VIA_LOOP", "PAD_VIA_LOOP2", 110, 995, layer="B.Cu")]),
    "SAME_FP_PINS": (3, 0, False, [
        _track("SAME_FP_PINS", (100, 915), (103, 915)), _pad("SAME_FP_PINS", "SFP", 100, 915, size=0.6),
        _pad("SAME_FP_PINS", "SFP", 103, 915, size=0.6, number="2")]),
    "THREE_PADS": (20, None, True, [
        _track("THREE_PADS", (100, 615), (110, 615)), _track("THREE_PADS", (110, 615), (120, 615)),
        *_ends("THREE_PADS", (100, 615), (120, 615)), _pad("THREE_PADS", "THREE_PADS3", 110, 615)]),
    "NO_PADS": (10, None, True, [_track("NO_PADS", (100, 630), (110, 630))]),
    "APART": (16, None, True, [_track("APART", (100, 645), (108, 645)), _track("APART", (112, 645), (120, 645)),
                               *_ends("APART", (100, 645), (120, 645))]),
    "NO_TRACKS": (0, None, False, _ends("NO_TRACKS", (100, 660), (120, 660))),
}
# nets of class RF for the delay checks: a route through a via its In1.Cu track reaches with copper only, a pad whose
# centre lies in a via whose centre lies outside the pad, and a pad with a pad-to-die length and no delay
DELAY_CASES = [
    _track("RF_VIA_REACH", (100, 675), (110, 675)), _via("RF_VIA_REACH", 110, 675),
    _track("RF_VIA_REACH", (110.35, 675), (120, 675), layer="In1.Cu"), _pad("RF_VIA_REACH", "RF_VIA_REACH1", 100, 675),
    {"action": "add_hole", "net": "RF_VIA_REACH", "x": 120, "y": 675, "drill": 0.4, "pad": 1},
    _track("RF_VIA_HOLDS_PAD", (100, 690), (110, 690)), _via("RF_VIA_HOLDS_PAD", 110, 690, 1.0, 0.3),
    _track("RF_VIA_HOLDS_PAD", (110, 690), (110, 700), layer="B.Cu"), _pad("RF_VIA_HOLDS_PAD", "VHP1", 100, 690),
    _pad("RF_VIA_HOLDS_PAD", "VHP2", 110, 700, layer="B.Cu"),
    _pad("RF_VIA_HOLDS_PAD", "VHP3", 110.4, 690, size=0.3, layer="B.Cu"),
    _track("RF_P2D", (100, 705), (110, 705)), *_ends("RF_P2D", (100, 705), (110, 705)),
    {"action": "script",
     "code": "[p] = board.FindFootprintByReference('RF_P2D1').Pads(); p.SetPadToDieLength(FromMM(2))"},
]


CUT_EVERY_TRACK = '''
import math, random
rng = random.Random(params["seed"])
V = pcbnew.VECTOR2I
cut = 0
for t in list(board.GetTracks()):
    if t.GetClass() == "PCB_VIA" or t.GetLength() < 1000:
        continue
    f = rng.uniform(0.01, 0.99)
    s, e = t.GetStart(), t.GetEnd()
    if t.GetClass() == "PCB_ARC":
        m = t.GetMid()
        bx, by, cx, cy = m.x - s.x, m.y - s.y, e.x - s.x, e.y - s.y
        d = 2 * (bx * cy - by * cx)
        if not d:
            continue
        ux = (cy * (bx * bx + by * by) - by * (cx * cx + cy * cy)) / d
        uy = (bx * (cx * cx + cy * cy) - cx * (bx * bx + by * by)) / d
        r = math.hypot(ux, uy)
        a0, am, a1 = (math.atan2(p.y - s.y - uy, p.x - s.x - ux) for p in (s, m, e))
        sweep = (a1 - a0) % (2 * math.pi)
        if (am - a0) % (2 * math.pi) > sweep:
            sweep -= 2 * math.pi
        at = lambda g: V(round(s.x + ux + r * math.cos(a0 + g * sweep)), round(s.y + uy + r * math.sin(a0 + g * sweep)))
        piece = pcbnew.PCB_ARC(board)
        piece.SetStart(at(f))
        piece.SetMid(at((1 + f) / 2))
        piece.SetEnd(e)
        t.SetMid(at(f / 2))
        t.SetEnd(at(f))
    else:
        p = V(round(s.x + f * (e.x - s.x)), round(s.y + f * (e.y - s.y)))
        piece = pcbnew.PCB_TRACK(board)
        piece.SetStart(p)
        piece.SetEnd(e)
        t.SetEnd(p)
    piece.SetWidth(t.GetWidth())
    piece.SetLayer(t.GetLayer())
    piece.SetNet(t.GetNet())
    board.Add(piece)
    cut += 1
result = cut
'''
JOIN_COLLINEAR_SEGMENTS = '''
from collections import defaultdict
joined = 0
while True:
    ends = defaultdict(list)
    for t in board.GetTracks():
        if t.GetClass() == "PCB_TRACK":
            u = t.m_Uuid.AsString()
            for p, far in ((t.GetStart(), t.GetEnd()), (t.GetEnd(), t.GetStart())):
                ends[(t.GetNetCode(), t.GetLayer(), t.GetWidth(), p.x, p.y)].append((u, t, far))
    done, plan = set(), []
    for (_, _, _, x, y), here in ends.items():
        pairs = [(ua, a, fa, ub, fb, b) for i, (ua, a, fa) in enumerate(here) for ub, b, fb in here[i + 1:]
                 if ua != ub and (fa.x - x) * (fb.y - y) == (fa.y - y) * (fb.x - x)
                 and (fa.x - x) * (fb.x - x) + (fa.y - y) * (fb.y - y) < 0]
        if len(pairs) == 1 and not {pairs[0][0], pairs[0][3]} & done:
            done |= {pairs[0][0], pairs[0][3]}
            plan.append(pairs[0])
    for ua, a, fa, ub, fb, b in plan:
        a.SetStart(fa)
        a.SetEnd(fb)
        board.Delete(b)
    joined += len(plan)
    if not plan:
        break
result = joined
'''


def write_tuning_profile(project: str, enabled: bool, profile: str = "RF50") -> None:
    def layer(name, top, bottom, ps_per_mm):
        return {"signal_layer": name, "top_reference_layer": top, "bottom_reference_layer": bottom,
                "width": 300000, "diff_pair_gap": 0, "delay": round(ps_per_mm * AS_PER_PS)}
    pro = os.path.join(project, "rf.kicad_pro")
    data = json.loads(read_text(pro))
    data["tuning_profiles"] = {"meta": {"version": 0}, "tuning_profiles_impedance_geometric": [{
        "profile_name": profile, "type": 0, "target_impedance": 50.0, "enable_time_domain_tuning": enabled,
        "layer_entries": [layer("F.Cu", "UNDEFINED", "In1.Cu", F_CU_PS_PER_MM),
                          layer("In1.Cu", "F.Cu", "In2.Cu", IN1_CU_PS_PER_MM)],
        "via_prop_delay": 8 * AS_PER_PS,
        "via_overrides": [{"signal_layer_from": "F.Cu", "signal_layer_to": "In1.Cu", "via_layer_from": "F.Cu",
                           "via_layer_to": "B.Cu", "delay": round(VIA_PS * AS_PER_PS)}]}]}
    with open(pro, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


# ─────────────────────────────────────────────────────────────────────────────
# 3D models, update footprints from library, update symbols
# ─────────────────────────────────────────────────────────────────────────────

class TestModelsAndLibraryUpdates:
    def test_models_report_assign_adjust_remove(self, blink):
        P = ["--project", blink]
        rep = rj(P + ["pcb", "models"])
        assert rep["models"] >= 8
        os.makedirs(os.path.join(blink, "3d"))
        with open(os.path.join(blink, "3d", "dip8.step"), "w", encoding="utf-8") as f:
            f.write("ISO-10303-21;\nEND-ISO-10303-21;\n")
        m = rj(P + ["pcb", "model", "set", "U1", os.path.join(blink, "3d", "dip8.step"), "--offset", "0,0,1",
                    "--rotation", "0,0,90"])
        assert m["models"][0]["file"] == "${KIPRJMOD}/3d/dip8.step" and m["models"][0]["rotation"] == [0, 0, 90]
        m = rj(P + ["pcb", "model", "adjust", "U1", "--offset", "1,2,3"])
        assert m["models"][0]["offset"] == [1, 2, 3]
        u1 = rj(P + ["pcb", "models", "--ref", "U1"])["footprints"][0]["models"][0]
        assert u1["exists"] and u1["path"].endswith(os.path.join("3d", "dip8.step"))
        missing = rj(P + ["pcb", "models", "--missing"])
        assert "U1" not in {x["reference"] for x in missing["missing_models"]}
        assert rj(P + ["pcb", "model", "remove", "U1"])["models"] == []
        r = run(["--json"] + P + ["pcb", "model", "set", "U1", "nope.step"], check=False)
        err = json.loads(r.stderr)
        assert r.returncode == 2 and err["category"] == "input" and "--no-check" in err["error"]

    def test_update_footprints_from_library(self, blink):
        P = ["--project", blink]
        src = os.path.join(rj(["info", "paths"])["KICAD10_FOOTPRINT_DIR"],
                           "Resistor_SMD.pretty", "R_0805_2012Metric.kicad_mod")
        lib = os.path.join(blink, "mylib.pretty")
        os.makedirs(lib)
        shutil.copyfile(src, os.path.join(lib, "R_0805_2012Metric.kicad_mod"))
        rj(P + ["project", "libtable", "add", "fp", "mylib", "${KIPRJMOD}/mylib.pretty"])
        rj(P + ["sch", "set-footprint", "R1", "mylib:R_0805_2012Metric"])
        rj(P + ["pcb", "update"])
        before = rj(P + ["pcb", "footprint", "R1"])
        assert rj(P + ["pcb", "update-footprints", "R1", "R2"])["unchanged"] == ["R1", "R2"]
        text = read_text(os.path.join(lib, "R_0805_2012Metric.kicad_mod"))
        text = re.sub(r"\(size 1\.025 1\.4\)", "(size 1.1 1.4)", text)  # the library pads change
        with open(os.path.join(lib, "R_0805_2012Metric.kicad_mod"), "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        res = rj(P + ["pcb", "update-footprints", "--match", "R*"])
        assert [u["ref"] for u in res["updated"]] == ["R1"] and "R2" in res["unchanged"]
        after = rj(P + ["pcb", "footprint", "R1"])
        assert [p_["net"] for p_ in after["pads"]] == [p_["net"] for p_ in before["pads"]]
        assert (after["x"], after["y"], after["rotation"]) == (before["x"], before["y"], before["rotation"])
        assert after["pads"][0]["size"][0] == pytest.approx(1.1)

    def test_update_symbols_reports_unchanged(self, blink):
        res = rj(["--project", blink, "sch", "update-symbols"])
        assert "Timer:NE555P" in res["unchanged"] and not res["updated"] and not res["missing"]


# ─────────────────────────────────────────────────────────────────────────────
# SPICE simulation
# ─────────────────────────────────────────────────────────────────────────────

def build_rc(root: str) -> str:
    p = os.path.join(root, "rc")
    run(["project", "new", p, "--name", "rc"])
    P = ["--project", p]
    run(P + ["sch", "add", "Simulation_SPICE:VDC", "--ref", "V1", "--at", "50.8,63.5", "--value", "5"])
    run(P + ["sch", "add", "Device:R", "--ref", "R1", "--at", "76.2,50.8", "--value", "1k"])
    run(P + ["sch", "add", "Device:C", "--ref", "C1", "--at", "101.6,63.5", "--value", "1u"])
    for pin, net in (("V1:1", "IN"), ("R1:1", "IN"), ("R1:2", "OUT"), ("C1:1", "OUT")):
        run(P + ["sch", "label-pin", pin, net])
    run(P + ["sch", "power", "GND", "--at", "V1:2"])
    run(P + ["sch", "power", "GND", "--at", "C1:2"])
    return p


class TestSimulation:
    def test_ac_corner_warnings_probes_and_floating_pin(self, tmp_dir):
        import csv
        import math
        p = os.path.join(tmp_dir, "ac")
        rj(["project", "new", p, "--name", "ac"])
        P = ["--project", p]
        for lid, ref, at, value in (("Simulation_SPICE:VDC", "V1", "50,60", "0"),
                                    ("Device:R", "R1", "75,50", "1.6k"),
                                    ("Device:C", "C1", "100,60", "100n")):
            rj(P + ["sch", "add", lid, "--ref", ref, "--at", at, "--value", value])
        floating = run(["--json", *P, "sim", "run", "--analysis", ".ac dec 200 10 100k"], check=False)
        assert floating.returncode == 2
        err = json.loads(floating.stderr)
        assert err["type"] == "InvalidInput" and "floating_pin" in err["error"] and "C1:2" in err["error"]
        for pin, net in (("V1:1", "IN"), ("R1:1", "IN"), ("R1:2", "OUT"), ("C1:1", "OUT")):
            rj(P + ["sch", "label-pin", pin, net])
        for pin in ("V1:2", "C1:2"):
            rj(P + ["sch", "power", "Simulation_SPICE:0", "--at", pin])
        missing = rj(P + ["sim", "run", "--analysis", ".ac dec 200 10 100k"])
        assert [w["code"] for w in missing["warnings"]] == ["ac_source_missing"]
        rj(P + ["sim", "model", "V1", "--device", "V", "--type", "DC", "--params", "dc=0 ac=1"])
        wrong = rj(P + ["sim", "run", "--analysis", ".ac dec 200 10 100k", "--probe", "v(out)"])
        assert wrong["warnings"][0]["code"] == "probe_not_found"
        assert "v(/out)" in wrong["warnings"][0]["available"]
        good = rj(P + ["sim", "run", "--analysis", ".ac dec 200 10 100k", "--probe", "v(/out)"])
        assert good["warnings"] == [] and good["errors"] == []
        with open(good["plots"][0]["csv"], encoding="utf-8", newline="") as f:
            rows = list(csv.DictReader(f))
        corner = min(rows, key=lambda row: abs(abs(complex(row["v(/out)"])) - 1 / math.sqrt(2)))
        assert complex(corner["frequency"]).real == pytest.approx(994.7, rel=0.02)

    @pytest.mark.smoke
    def test_rc_transient_and_operating_point(self, tmp_dir):
        p = build_rc(tmp_dir)
        P = ["--project", p]
        assert rj(["sim", "engine"])["kind"] in ("executable", "shared")
        r = run(["--json"] + P + ["sim", "run"], check=False)
        err = json.loads(r.stderr)
        assert r.returncode == 2 and err["category"] == "input" and "sim directive add" in err["hint"]
        rj(P + ["sim", "directive", "add", ".tran 10u 5m uic"])
        assert [d["text"] for d in rj(P + ["sim", "directives"])] == [".tran 10u 5m uic"]
        net = rj(P + ["sim", "netlist"])
        assert net["elements"] == 3 and net["analyses"] == [".tran 10u 5m uic"]
        res = rj(P + ["sim", "run", "--probe", "v(/out)", "--points", "11"])
        tran = res["plots"][0]
        assert tran["name"] == "Transient Analysis" and tran["points"] > 100
        out = next(v for v in tran["vectors"] if v["name"] == "v(/out)")
        assert out["last"] == pytest.approx(5 * (1 - 2.718281828 ** -5), abs=0.02)  # 5 tau of 1k * 1u
        assert out["first"] < 0.05 and len(out["values"]) == 11
        assert read_csv(tran["csv"])[0] == ["time", "v(/in)", "v(/out)", "i(v1)"]  # CSV keeps every vector
        op = rj(P + ["sim", "run", "--analysis", ".op"])["plots"][0]
        assert op["points"] == 1 and {v["name"]: v["value"] for v in op["vectors"]}["v(/out)"] == pytest.approx(5)
        bad = run(["--json"] + P + ["sim", "model", "V1", "--type", "PULSE",
                                    "--params", "y1=0 y2=3.3 td=0 tr=1n tf=1n pw=1 per=2"], check=False)
        bad_err = json.loads(bad.stderr)
        assert bad.returncode == 2 and bad_err["category"] == "input" and "pw -> tw" in bad_err["error"]
        m = rj(P + ["sim", "model", "V1", "--type", "PULSE", "--params", "y1=0 y2=3.3 td=0 tr=1n tf=1n tw=1 per=2"])
        assert m["sim_fields"]["Sim.Type"] == "PULSE"
        res = rj(P + ["sim", "run", "--probe", "v(/out)", "--points", "0"])
        assert next(v for v in res["plots"][0]["vectors"] if v["name"] == "v(/out)")["max"] == pytest.approx(
            3.3 * (1 - 2.718281828 ** -5), abs=0.02)
        assert rj(P + ["sim", "directive", "remove", ".tran"])["removed"] == 1

    def test_shared_library_engine(self, tmp_dir):
        """The ngspice library KiCad ships (no ngspice executable needed, e.g. on Windows/macOS)."""
        from kicad_cli_extended.core import sim as sim_mod
        lib = next((c for c in sim_mod._shared_candidates() if os.path.isfile(c)), None)
        assert lib, "no ngspice shared library found (KiCad's simulator needs one)"
        p = build_rc(tmp_dir)
        env = dict(os.environ, **{ENV_PREFIX + "LIBNGSPICE": lib, ENV_PREFIX + "NGSPICE": ""})
        env["PATH"] = os.pathsep.join(d for d in env["PATH"].split(os.pathsep)
                                      if not os.path.exists(os.path.join(d, "ngspice")))
        r = run(["--json", "--project", p, "sim", "run", "--analysis", ".op"], env=env)
        res = json.loads(r.stdout)
        assert res["engine"]["kind"] == "shared"
        assert {v["name"]: v["value"] for v in res["plots"][0]["vectors"]}["v(/in)"] == pytest.approx(5)


# ─────────────────────────────────────────────────────────────────────────────
# Live GUI client (IPC API)
# ─────────────────────────────────────────────────────────────────────────────

class TestGui:
    @pytest.mark.smoke
    def test_status_and_errors_without_a_running_kicad(self, tmp_dir):
        env = dict(os.environ, KICAD_API_SOCKET="ipc://" + os.path.join(tmp_dir, "none.sock").replace("\\", "/"))
        st = json.loads(run(["--json", "gui", "status"], env=env).stdout)
        assert st["connected"] is False and "error" in st
        r = run(["--json", "gui", "selection"], env=env, check=False)
        err = json.loads(r.stderr)
        assert r.returncode == 7 and err["category"] == "environment"
        assert err["hint"].startswith((f"{COMMAND} gui status", "pip install", "uv tool"))

    @pytest.mark.skipif(os.environ.get(ENV_PREFIX + "GUI_TESTS") != "1",
                        reason="needs a running KiCad GUI with the API enabled (set KCE_GUI_TESTS=1)")
    def test_live_gui(self):
        st = rj(["gui", "status"])
        assert st["connected"] and st["kicad_version"].startswith("10.")
        assert "count" in rj(["gui", "selection"])


# ─────────────────────────────────────────────────────────────────────────────
# Panelization
# ─────────────────────────────────────────────────────────────────────────────

class TestPanel:
    def test_mousebites_panel_with_rails_drc_clean_and_gerbers(self, blink, tmp_dir):
        out = os.path.join(tmp_dir, "panel", "blink-panel.kicad_pcb")
        res = rj(["--project", blink, "pcb", "panelize", "--rows", "2", "--cols", "3", "--output", out, "--drc"])
        assert (res["boards"], res["mode"], res["panel_size"]) == (6, "mousebites", [124.0, 76.0])
        assert res["tabs"] > 0 and res["mouse_bites"] > 0 and res["fiducials"] == 3 and res["tooling_holes"] == 4
        assert res["edge_cut_outlines"] == 1 and res["drc"]["errors"] == 0, res["drc"]
        assert os.path.exists(out[:-len(".kicad_pcb")] + ".kicad_pro")
        info = rj(["--project", out, "pcb", "info"])
        assert info["outline_bbox"]["width"] == pytest.approx(124.0, abs=0.2)
        nets = {n["name"] for n in rj(["--project", out, "pcb", "nets"])}
        assert {"Board_1-/GND", "Board_6-/OUT"} <= nets and "/GND" not in nets
        g = rj(["--project", out, "pcb", "export", "gerbers", "--output", os.path.join(tmp_dir, "pg") + os.sep])
        assert any(f.get("function", "").startswith("Profile") for f in g["outputs"])
        artifact(out, "panel")

    def test_vscore_panel(self, blink, tmp_dir):
        out = os.path.join(tmp_dir, "v.kicad_pcb")
        res = rj(["--project", blink, "pcb", "panelize", "--rows", "1", "--cols", "2", "--spacing", "0",
                  "--rails", "lr", "--output", out, "--drc"])
        assert res["mode"] == "vscore" and res["vscore_lines"] == 3 and res["mouse_bites"] == 0
        assert res["drc"]["errors"] == 0

    def test_kikit_engine_missing_is_a_clear_error(self, blink):
        if shutil.which("kikit"):
            pytest.skip("kikit installed")
        r = run(["--json", "--project", blink, "pcb", "panelize", "--rows", "2", "--cols", "2", "--engine", "kikit"],
                check=False)
        err = json.loads(r.stderr)
        assert r.returncode == 7 and err["category"] == "environment" and "pip install kikit" in err["error"]


# ─────────────────────────────────────────────────────────────────────────────
# Foreign board import, design variants, KiCad 10 setup
# ─────────────────────────────────────────────────────────────────────────────

class TestImportVariantsSetup:
    def test_import_altium_board(self, tmp_dir):
        from kicad_cli_extended.utils import kicad_backend as kb
        src = os.path.join(qa_dir(), "eDP_adapter_dvt1_source", "eDP_adapter_dvt1.PcbDoc")
        native = os.path.join(tmp_dir, "native.kicad_pcb")
        kb.run_kicad_cli(["pcb", "import", "--format", "altium", "--output", native, src])
        stats_path = os.path.join(tmp_dir, "native-stats.json")
        kb.run_kicad_cli(["pcb", "export", "stats", "--format", "json", "--output", stats_path, native])
        native_stats = json.loads(read_text(stats_path))
        res = rj(["project", "import", src, "--dir", os.path.join(tmp_dir, "edp")])
        b = res["board"]
        assert res["source_format"] == "Altium Designer" and b["copper_layers"] == 4
        assert b["footprints"] == native_stats["components"]["total"]["total"] == 115 and b["nets"] > 50
        assert b["vias"] == sum(native_stats["vias"].values()) > 0
        assert res["import_statistics"]["footprints"] == 115
        assert {os.path.basename(x["file"]) for x in res["not_imported"]} == {"it6251_core.SchDoc", "power.SchDoc"}
        pro = res["project"]["pro"]
        assert rj(["--project", pro, "pcb", "info"])["footprints"] == 115
        d = rj(["--project", pro, "check", "drc", "--no-parity"])
        assert d["kicad_version"].startswith("10.")

    def test_design_variants_bom_and_board(self, blink):
        P = ["--project", blink]
        rj(P + ["project", "variant", "add", "LITE", "--description", "no LED"])
        rj(P + ["sch", "variant", "D1", "LITE", "--dnp", "yes", "--field", "Value=NOLED"])
        rj(P + ["sch", "variant", "R3", "LITE", "--in-bom", "no"])
        v = rj(P + ["project", "variant", "list"])["variants"][0]
        assert v["name"] == "LITE" and v["symbols"]["D1"]["dnp"] == "yes"
        bom = rj(P + ["sch", "export", "bom", "--variant", "LITE"])
        rows = {r[0]: r for r in read_csv(os.path.join(bom["output_dir"], bom["outputs"][0]["path"]))}
        assert rows["D1"][1] == "NOLED" and rows["D1"][4] == "DNP" and "R3" not in rows
        upd = rj(P + ["pcb", "update"])
        assert upd["variants_added"] == ["LITE"] and {u["ref"] for u in upd["updated"]} == {"D1", "R3"}
        assert rj(P + ["pcb", "update"])["updated"] == []  # idempotent
        pos = rj(P + ["pcb", "export", "pos", "--variant", "LITE", "--format", "csv"])
        rows = {r[0]: r for r in read_csv(os.path.join(pos["output_dir"], pos["outputs"][0]["path"]))}
        assert rows["D1"][1] == "NOLED"
        rj(P + ["project", "variant", "remove", "LITE"])
        assert rj(P + ["project", "variant", "list"])["variants"] == []

    @pytest.mark.smoke
    def test_init_global_tables_includes_design_blocks(self, tmp_dir):
        env = dict(os.environ, KICAD_CONFIG_HOME=tmp_dir)
        res = json.loads(run(["--json", "lib", "init-global-tables"], env=env).stdout)
        assert {k: v["action"] for k, v in res.items()} == {"sym": "installed", "fp": "installed",
                                                             "design_block": "installed"}
        cfg = os.path.dirname(res["sym"]["path"])
        assert os.path.basename(cfg) == "10.0"  # KiCad appends the version to KICAD_CONFIG_HOME
        assert read_text(os.path.join(cfg, "design-block-lib-table")).startswith("(design_block_lib_table")
        doc = json.loads(run(["--json", "info", "doctor"], env=env).stdout)
        assert doc["global_design_block_lib_table"] and doc["config_dir"] == cfg
