"""End-to-end tests of the advanced commands on real KiCad 10 (kicad-cli + pcbnew), through the
installed ``kce``: teardrops, via stitching/fences, rule areas, dimensions, barcodes, holes,
fiducials, differential pairs, lengths and tuning, 3D models, footprint updates, SPICE
simulation, live-GUI client, panelization, foreign-board import and design variants.

Every board-changing scenario ends with KiCad's own DRC (and kicad-cli re-loading the files).
"""

from __future__ import annotations

import json
import os
import re
import shutil

import pytest

from test_e2e import (COMMAND, ENV_PREFIX, REPO, artifact, blink, blink_master, demos_dir, read_csv,  # noqa: F401
                      read_text, rj, run, tmp_dir)

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
        assert err["type"] == "ValueError" and "floating_pin" in err["error"] and "C1:2" in err["error"]
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
