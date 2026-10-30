"""Placement on a real board: `pcb update` placing new parts, `pcb score`, `pcb legalize` and
`pcb place near`. Every scenario runs KiCad's own DRC or reads the board back through pcbnew.
"""

from __future__ import annotations

import json
import math
import os

import pytest

from kicad_cli_extended.core import sexpr

from test_e2e import (COMMAND, ENV_PREFIX, REPO, artifact, blink, blink_master, demos_dir, read_csv,  # noqa: F401
                      read_text, rj, run, tmp_dir)

pytestmark = pytest.mark.e2e


def drc(project, *extra):
    return rj(["--project", project, "check", "drc", *extra])


def board(project):
    return {f["reference"]: f for f in rj(["--project", project, "pcb", "footprints"])}


def outline(project):
    return rj(["--project", project, "pcb", "info"])["outline_bbox"]


def box(fp):
    return fp.get("courtyard") or fp["bbox"]


def inside(fp, o):
    b = box(fp)
    return (b["min"][0] >= o["min"][0] and b["min"][1] >= o["min"][1]
            and b["max"][0] <= o["max"][0] and b["max"][1] <= o["max"][1])


def score(project, *extra):
    return rj(["--project", project, "pcb", "score", *extra])


def placement_board(root):
    p = os.path.join(root, "placement")
    run(["project", "new", p, "--name", "placement"])
    P = ["--project", p]
    run(P + ["pcb", "outline", "rect", "--at", "40,40", "--size", "20,20"])
    for ref in ("R1", "R2"):
        run(P + ["pcb", "add-footprint", "Resistor_SMD:R_0805_2012Metric", "--ref", ref,
                 "--at", "45,45"])
    return p


# ═════════════════════════════════════════════════════════════════════════════
# Item 1: new parts land beside standing ones (pcb update)
# ═════════════════════════════════════════════════════════════════════════════

class TestUpdatePlacement:
    @pytest.mark.parametrize("standing", [False, True], ids=["empty", "standing"])
    def test_overflow_is_reported_and_placed_outside(self, tmp_dir, standing):
        p = os.path.join(tmp_dir, "overflow")
        run(["project", "new", p, "--name", "overflow"])
        P = ["--project", p]
        run(P + ["pcb", "outline", "rect", "--at", "40,40",
                 "--size", "8,6" if standing else "6,4"])
        fp = "Resistor_SMD:R_0805_2012Metric"
        refs = ["R1", "R2", "R3", "R4"]
        for i, ref in enumerate(refs):
            run(P + ["sch", "add", "Device:R", "--ref", ref,
                     "--at", f"{50.8 + i * 12.7},50.8", "--footprint", fp])
        if standing:
            run(P + ["pcb", "add-footprint", fp, "--ref", "R1", "--at", "44,43"])
        before = board(p)
        assert set(before) == ({"R1"} if standing else set())

        up = rj(P + ["pcb", "update"])
        assert {a["ref"] for a in up["added"]} == set(refs) - set(before)
        assert up["unplaced_inside_outline"] == ["R2", "R3", "R4"]
        assert up["placed"]["unplaced_inside_outline"] == ["R2", "R3", "R4"]
        assert "3 new footprint(s) do not fit inside the outline (R2, R3, R4)" in up["warning"]
        placed = {f["reference"]: f for f in up["placed"]["placed"]}
        after, o = board(p), outline(p)
        for ref in refs:
            fits = ref == "R1"
            assert inside(after[ref], o) == fits, (ref, box(after[ref]), o)
            if ref in placed:
                assert placed[ref]["inside_outline"] == fits
                assert (placed[ref]["x"], placed[ref]["y"]) == (after[ref]["x"], after[ref]["y"])
            if not fits:
                assert box(after[ref])["min"][1] > o["max"][1]
        for ref, old in before.items():
            assert (after[ref]["x"], after[ref]["y"]) == (old["x"], old["y"])
        assert score(p)["courtyard_overlaps"]["count"] == 0

    def test_initial_parts_keep_the_row_layout_when_they_fit(self, tmp_dir):
        p = os.path.join(tmp_dir, "rows")
        run(["project", "new", p, "--name", "rows"])
        P = ["--project", p]
        run(P + ["pcb", "outline", "rect", "--at", "40,40", "--size", "20,12"])
        for ref, at in (("R1", "50.8,50.8"), ("R2", "63.5,50.8")):
            run(P + ["sch", "add", "Device:R", "--ref", ref, "--at", at,
                     "--footprint", "Resistor_SMD:R_0805_2012Metric"])
        up = rj(P + ["pcb", "update"])
        assert up["unplaced_inside_outline"] == []
        assert up["placed"]["overflow"] is False
        assert "warning" not in up
        after, o = board(p), outline(p)
        first, second = box(after["R1"]), box(after["R2"])
        assert first["min"] == pytest.approx([v + 2 for v in o["min"]], abs=0.001)
        assert second["min"] == pytest.approx([first["max"][0] + 1, first["min"][1]], abs=0.001)
        assert all(inside(f, o) for f in after.values())
        assert all(f["inside_outline"] for f in up["placed"]["placed"])

    def test_new_parts_avoid_existing_footprints(self, blink):
        P = ["--project", blink]
        before = board(blink)
        parts = (("R4", "Device:R", "Resistor_SMD:R_0805_2012Metric", "93.98,66.04", "2k2"),
                 ("R5", "Device:R", "Resistor_SMD:R_0805_2012Metric", "93.98,86.36", "4k7"),
                 ("C3", "Device:C", "Capacitor_SMD:C_0805_2012Metric", "109.22,99.06", "100n"))
        for ref, lib, fp, at, val in parts:
            run(P + ["sch", "add", lib, "--ref", ref, "--at", at, "--footprint", fp, "--value", val])
        up = rj(P + ["pcb", "update"])
        assert {a["ref"] for a in up["added"]} == {"R4", "R5", "C3"}
        d = drc(blink, "--no-parity")
        assert d["by_type"].get("courtyards_overlap", 0) == 0, d["by_type"]
        assert d["by_type"].get("shorting_items", 0) == 0, d["by_type"]
        assert up["unplaced_inside_outline"] == []
        after = board(blink)
        for ref, fp in before.items():
            assert (after[ref]["x"], after[ref]["y"]) == (fp["x"], fp["y"]), ref
        o = outline(blink)
        for ref in ("R4", "R5", "C3"):
            assert inside(after[ref], o), (ref, box(after[ref]), o)
        assert score(blink)["courtyard_overlaps"]["count"] == 0


# ═════════════════════════════════════════════════════════════════════════════
# Item 2: pcb score (measurements only)
# ═════════════════════════════════════════════════════════════════════════════

def build_crossing_board(root: str) -> str:
    """Four resistors in a square (corners inside the 40,40 60x50 outline); nets A and B join
    opposite corners, so the two ratsnest connections cross."""
    p = os.path.join(root, "cross")
    run(["project", "new", p, "--name", "cross"])
    P = ["--project", p]
    fp = "Resistor_SMD:R_0805_2012Metric"
    for ref, at in (("R1", "50.8,50.8"), ("R2", "88.9,50.8"), ("R3", "50.8,76.2"), ("R4", "88.9,76.2")):
        run(P + ["sch", "add", "Device:R", "--ref", ref, "--at", at, "--footprint", fp, "--value", "1k"])
    for pin, net in (("R1:1", "A"), ("R3:1", "A"), ("R2:1", "B"), ("R4:1", "B")):
        run(P + ["sch", "label-pin", pin, net])
    run(P + ["pcb", "outline", "rect", "--at", "40,40", "--size", "60,50"])
    run(P + ["pcb", "update"])
    for ref, at in (("R1", "45,45"), ("R2", "95,45"), ("R3", "95,85"), ("R4", "45,85")):
        run(P + ["pcb", "move", ref, "--at", at])
    return p


class TestScore:
    def test_ratsnest_does_not_depend_on_footprint_uuids(self, tmp_dir):
        p = build_crossing_board(tmp_dir)
        P = ["--project", p]
        before = rj(P + ["pcb", "ratsnest"])
        footprints = board(p)
        positions = {ref: (f["x"], f["y"]) for ref, f in footprints.items()}
        uuids = {f["uuid"]: f"00000000-0000-0000-0000-{i:012x}"
                 for i, f in enumerate(reversed(list(footprints.values())), 1)}
        path = os.path.join(p, "cross.kicad_pcb")
        tree = sexpr.loads(read_text(path))
        for fp in tree.find_all("footprint"):
            fp.set("uuid", sexpr.QStr(uuids[fp.value("uuid")]))
        indices = [i for i, node in enumerate(tree) if isinstance(node, sexpr.SList) and node.tag == "footprint"]
        for i, node in zip(indices, reversed(tree.find_all("footprint"))):
            tree[i] = node
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(sexpr.dumps(tree))
        assert {ref: (f["x"], f["y"]) for ref, f in board(p).items()} == positions
        assert rj(P + ["pcb", "ratsnest"]) == before

    def test_hpwl_crossings_filter_and_determinism(self, tmp_dir):
        p = build_crossing_board(tmp_dir)
        P = ["--project", p]
        pads = {ref: next(p for p in rj(P + ["pcb", "footprint", ref])["pads"] if p["number"] == "1")
                for ref in ("R1", "R3")}
        a1, a3 = pads["R1"]["x"], pads["R3"]["x"]
        b1, b3 = pads["R1"]["y"], pads["R3"]["y"]
        s = score(p, "--bin-size", "5")
        assert s["units"] == "mm"
        nets = {n["net"]: n for n in s["hpwl"]["nets"]}
        assert nets["/A"]["pads"] == 2
        assert nets["/A"]["hpwl"] == pytest.approx(abs(a1 - a3) + abs(b1 - b3), abs=1e-3)
        assert s["hpwl"]["total"] == pytest.approx(sum(n["hpwl"] for n in s["hpwl"]["nets"]))
        assert s["ratsnest_crossings"]["count"] == 1, s["ratsnest_crossings"]
        assert score(p, "--bin-size", "5") == s
        only = score(p, "--net", "/A")
        assert [n["net"] for n in only["hpwl"]["nets"]] == ["/A"]
        assert only["ratsnest_crossings"]["count"] == 0
        c = s["congestion"]
        assert c["mean_density"] > 0 and c["max_density"] >= c["mean_density"]
        assert c["hottest"] and [b["density"] for b in c["hottest"]] == sorted(
            [b["density"] for b in c["hottest"]], reverse=True)
        assert c["hottest"][0]["density"] == pytest.approx(
            c["hottest"][0]["length"] / (c["bin_size"] ** 2), abs=1e-4)

    def test_overlap_and_outside_outline(self, tmp_dir):
        p = build_crossing_board(tmp_dir)
        P = ["--project", p]
        assert score(p)["courtyard_overlaps"]["count"] == 0
        r1 = rj(P + ["pcb", "footprint", "R1"])
        run(P + ["pcb", "move", "R2", "--at", f"{r1['x']},{r1['y']}"])
        ov = score(p)["courtyard_overlaps"]
        assert ov["count"] == 1 and ov["pairs"][0]["a"] == "R1" and ov["pairs"][0]["b"] == "R2"
        assert ov["pairs"][0]["area"] > 0
        run(P + ["pcb", "move", "R4", "--at", "10,10"])
        out = score(p)["outside_outline"]
        assert [f["reference"] for f in out["footprints"]] == ["R4"]
        assert score(p, "--net", "/A")["courtyard_overlaps"]["count"] == 1


# ═════════════════════════════════════════════════════════════════════════════
# Item 3: pcb legalize
# ═════════════════════════════════════════════════════════════════════════════

class TestLegalize:
    def test_bbox_fallback_and_explicit_placement_area(self, tmp_dir):
        p = placement_board(tmp_dir)
        P = ["--project", p]
        run(P + ["raw", "pcbnew", "-c", """
fp = board.FindFootprintByReference('R1')
for item in list(fp.GraphicalItems()):
    if item.GetLayer() in (pcbnew.F_CrtYd, pcbnew.B_CrtYd):
        fp.Remove(item)
"""])
        assert "courtyard" not in board(p)["R1"]
        assert score(p)["courtyard_overlaps"]["count"] == 1
        placed = rj(P + ["pcb", "place", "grid", "--refs", "R2", "--area", "40,40,20,20"])
        assert [f["reference"] for f in placed["placed"]] == ["R2"]
        assert score(p)["courtyard_overlaps"]["count"] == 0
        assert inside(board(p)["R2"], outline(p))

    def test_cutout_inside_courtyard_is_not_a_legal_position(self, tmp_dir):
        p = placement_board(tmp_dir)
        P = ["--project", p]
        run(P + ["pcb", "move", "R2", "--at", "55,55"])
        run(P + ["raw", "pcbnew", "-c", """
cutout = pcbnew.PCB_SHAPE(board)
cutout.SetShape(pcbnew.SHAPE_T_RECT)
cutout.SetLayer(pcbnew.Edge_Cuts)
cutout.SetStart(vec(44.8, 44.8))
cutout.SetEnd(vec(45.2, 45.2))
cutout.SetWidth(FromMM(0.05))
board.Add(cutout)
"""])
        assert [f["reference"] for f in score(p)["outside_outline"]["footprints"]] == ["R1"]
        res = rj(P + ["pcb", "legalize"])
        assert [m["reference"] for m in res["moves"]] == ["R1"]
        assert score(p)["outside_outline"]["count"] == 0

    def test_legalize_moves_overlap_keeps_locked_and_dry_run(self, blink):
        P = ["--project", blink]
        r1 = rj(P + ["pcb", "footprint", "R1"])
        run(P + ["pcb", "move", "R2", "--at", f"{r1['x']},{r1['y']}"])
        run(P + ["pcb", "lock", "R1"])
        assert score(blink)["courtyard_overlaps"]["count"] == 1
        before = read_text(os.path.join(blink, "blink.kicad_pcb"))
        dry = json.loads(run(["--json", "--dry-run", *P, "pcb", "legalize"]).stdout)
        assert [m["reference"] for m in dry["moves"]] == ["R2"]
        assert read_text(os.path.join(blink, "blink.kicad_pcb")) == before
        res = rj(P + ["pcb", "legalize"])
        moves = {m["reference"]: m for m in res["moves"]}
        assert "R1" not in moves and moves["R2"]["distance"] > 0
        assert moves["R2"]["from"] == [r1["x"], r1["y"]]
        assert res["unmoved"] == []
        r1_after = rj(P + ["pcb", "footprint", "R1"])
        assert (r1_after["x"], r1_after["y"]) == (r1["x"], r1["y"])
        assert score(blink)["courtyard_overlaps"]["count"] == 0
        run(P + ["session", "undo"])
        assert read_text(os.path.join(blink, "blink.kicad_pcb")) == before
        assert rj(P + ["pcb", "legalize"]) == res
        assert rj(P + ["pcb", "legalize"])["moves"] == []

    def test_outside_locked_and_bounded_search(self, tmp_dir):
        p = placement_board(tmp_dir)
        P = ["--project", p]
        run(P + ["pcb", "move", "R1", "--at", "10,10"])
        run(P + ["pcb", "lock", "R1"])
        run(P + ["pcb", "move", "R2", "--at", "39,50"])
        blocked = rj(P + ["pcb", "legalize", "--max-dist", "0"])
        assert blocked["moves"] == []
        assert {f["reference"] for f in blocked["unmoved"]} == {"R1", "R2"}
        assert "locked" in next(f["reason"] for f in blocked["unmoved"] if f["reference"] == "R1")
        res = rj(P + ["pcb", "legalize", "--max-dist", "5"])
        assert [m["reference"] for m in res["moves"]] == ["R2"]
        assert 0 < res["moves"][0]["distance"] <= 5
        assert [f["reference"] for f in res["unmoved"]] == ["R1"]
        assert inside(board(p)["R2"], outline(p))
        assert [f["reference"] for f in score(p)["outside_outline"]["footprints"]] == ["R1"]

    def test_nearest_grid_position_and_reference_order(self, tmp_dir):
        p = placement_board(tmp_dir)
        P = ["--project", p]
        before = board(p)
        rect = box(before["R1"])
        step = 0.5
        gap = min(rect["max"][0] - rect["min"][0], rect["max"][1] - rect["min"][1]) + 0.2
        nearest = math.ceil(gap / step) * step
        res = rj(P + ["pcb", "legalize", "--grid", str(step)])
        assert len(res["moves"]) == 1
        assert res["moves"][0]["distance"] == pytest.approx(nearest)
        assert score(p)["courtyard_overlaps"]["count"] == 0
        run(P + ["session", "undo"])
        assert board(p) == before
        assert rj(P + ["pcb", "legalize", "--grid", str(step)]) == res


# ═════════════════════════════════════════════════════════════════════════════
# Item 4: pcb place near
# ═════════════════════════════════════════════════════════════════════════════

class TestPlaceNear:
    def test_place_near_pad_stays_legal_and_inside(self, blink):
        P = ["--project", blink]
        u1 = rj(P + ["pcb", "footprint", "U1"])
        run(P + ["pcb", "add-footprint", "Resistor_SMD:R_0805_2012Metric", "--ref", "R9",
                 "--at", f"{u1['x']},{u1['y']}"])
        assert score(blink)["courtyard_overlaps"]["count"] >= 1
        pad = next(p for p in rj(P + ["pcb", "footprint", "U1"])["pads"] if p["number"] == "1")
        path = os.path.join(blink, "blink.kicad_pcb")
        before = read_text(path)
        dry = json.loads(run(["--json", "--dry-run", *P, "pcb", "place", "near", "R9",
                              "--to", "U1:1", "--max-dist", "15"]).stdout)
        assert read_text(path) == before
        res = rj(P + ["pcb", "place", "near", "R9", "--to", "U1:1", "--max-dist", "15"])
        assert dry['dry_run'] is True and res['dry_run'] is False
        assert path in dry['would_write'] and path in res['would_write']
        metadata = {'dry_run', 'would_write', 'would_save'}
        assert {k: v for k, v in res.items() if k not in metadata} == {k: v for k, v in dry.items() if k not in metadata}
        assert res["reference"] == "R9" and res["target"] == "U1:1"
        assert res["target_at"] == [pad["x"], pad["y"]]
        assert res["distance"] <= 15 + 1e-9
        assert res["side"] == "front"
        placed = rj(P + ["pcb", "footprint", "R9"])
        assert inside(placed, outline(blink))
        assert score(blink)["courtyard_overlaps"]["count"] == 0
        run(P + ["session", "undo"])
        assert read_text(path) == before
        back = rj(P + ["pcb", "place", "near", "R9", "--to", "U1", "--max-dist", "15", "--side", "back"])
        assert back["side"] == "back"
        assert rj(P + ["pcb", "footprint", "R9"])["side"] == "bottom"
        vcc = next(p for p in u1["pads"] if p["number"] == "8")
        named = rj(P + ["pcb", "place", "near", "R9", "--to", "U1:VCC", "--max-dist", "15"])
        assert named["target_at"] == [vcc["x"], vcc["y"]]

    def test_place_near_without_room_fails_clearly(self, blink):
        P = ["--project", blink]
        u1 = rj(P + ["pcb", "footprint", "U1"])
        run(P + ["pcb", "add-footprint", "Resistor_SMD:R_0805_2012Metric", "--ref", "R9",
                 "--at", f"{u1['x']},{u1['y']}"])
        run(P + ["pcb", "lock", "U1"])
        before = read_text(os.path.join(blink, "blink.kicad_pcb"))
        r = run(["--json", *P, "pcb", "place", "near", "R9", "--to", "U1:1", "--max-dist", "0.5",
                 "--side", "back"], check=False)
        assert r.returncode == 3
        assert r.stdout == ""
        assert read_text(os.path.join(blink, "blink.kicad_pcb")) == before
        err = json.loads(r.stderr)
        assert err["type"] == "PlacementBlocked" and err["category"] == "design"
        assert "U1" in err["details"]["locked_blockers"]
        assert "No legal spot for R9 within 0.5 mm of U1:1" in err["error"]
        assert err["hint"].startswith("kce pcb place near")

    def test_locked_and_self_targets_are_rejected(self, tmp_dir):
        p = placement_board(tmp_dir)
        P = ["--project", p]
        run(P + ["pcb", "lock", "R2"])
        before = read_text(os.path.join(p, "placement.kicad_pcb"))
        for ref, target, message in (("R2", "R1", "locked"), ("R1", "R1", "itself")):
            r = run(["--json", *P, "pcb", "place", "near", ref, "--to", target], check=False)
            assert r.returncode != 0 and r.stdout == ""
            err = json.loads(r.stderr)
            assert message in err["error"]
            assert err["hint"].startswith("kce pcb")
            assert read_text(os.path.join(p, "placement.kicad_pcb")) == before

    def test_invalid_search_parameters_fail_with_hint(self, tmp_dir):
        p = placement_board(tmp_dir)
        P = ["--json", "--project", p]
        before = read_text(os.path.join(p, "placement.kicad_pcb"))
        for command in (["pcb", "legalize"], ["pcb", "place", "near", "R2", "--to", "R1"]):
            for flag, value in (("--grid", "0"), ("--clearance", "-1"), ("--max-dist", "nan")):
                r = run(P + command + [flag, value], check=False)
                assert r.returncode != 0 and r.stdout == ""
                err = json.loads(r.stderr)
                assert flag in err["error"] and "finite" in err["error"]
                assert err["hint"].startswith("kce pcb")
                assert read_text(os.path.join(p, "placement.kicad_pcb")) == before


class TestThermalClearance:
    def test_initial_routed_board_is_drc_clean(self, blink):
        d = drc(blink, "--no-parity")
        assert d["errors"] == 0 and d["unconnected"] == 0, d["violations"]

    def test_stitching_preserves_pad_thermal_room(self, tmp_dir):
        p = os.path.join(tmp_dir, "thermal")
        run(["project", "new", p, "--name", "thermal"])
        P = ["--project", p]
        run(P + ["pcb", "outline", "rect", "--at", "10,10", "--size", "20,20"])
        run(P + ["pcb", "add-footprint", "Connector_PinHeader_2.54mm:PinHeader_1x01_P2.54mm_Vertical",
                 "--ref", "J1", "--at", "20,20"])
        run(P + ["pcb", "set-pad-net", "J1:1", "GND"])
        run(P + ["pcb", "zone", "add", "--net", "GND", "--layer", "F.Cu", "--layer", "B.Cu",
                 "--thermal-gap", "0.5", "--thermal-width", "0.5"])
        pad = rj(P + ["pcb", "footprint", "J1"])["pads"][0]
        stitched = rj(P + ["pcb", "stitch", "--net", "GND", "--pitch", "0.5", "--rect", "17,17,6,6"])
        assert stitched["added"] > 0
        for x, y in stitched["vias"]:
            dx = max(abs(x - pad["x"]) - pad["size"][0] / 2, 0)
            dy = max(abs(y - pad["y"]) - pad["size"][1] / 2, 0)
            assert math.hypot(dx, dy) >= 0.3 + 0.2 + 0.5 + 0.5 - 1e-6, (x, y)



def test_route_uses_default_netclass_width_without_override(tmp_dir):
    p = os.path.join(tmp_dir, "route")
    run(["project", "new", p, "--name", "route"])
    P = ["--project", p]
    run(P + ["project", "netclass", "set", "Default", "--track-width", "0.37"])
    for ref, at in (("R1", "50,50"), ("R2", "60,55")):
        run(P + ["pcb", "add-footprint", "Resistor_SMD:R_0805_2012Metric", "--ref", ref, "--at", at])
    result = rj(P + ["pcb", "route", "R1:1", "R2:1", "--layer", "F.Cu"])
    assert result["width"] == pytest.approx(0.37)
    assert len(result["segments"]) == 2


def test_score_anonymous_footprints_have_stable_schema_identifiers(tmp_path):
    from pathlib import Path
    import jsonschema
    from kicad_cli_extended.utils import kicad_backend as kb
    root = tmp_path / 'anonymous'
    run(['project', 'new', root, '--name', 'p'])
    path = str(root / 'p.kicad_pcb')
    code = '''
for _ in range(2):
    fp = pcbnew.FOOTPRINT(board)
    fp.SetReference('')
    fp.SetPosition(vec(25, 5))
    board.Add(fp)
    pad = pcbnew.PAD(fp)
    pad.SetNumber('1')
    pad.SetSize(vec(1, 1))
    pad.SetPosition(fp.GetPosition())
    fp.Add(pad)
result = ['uuid:' + fp.m_Uuid.AsString() for fp in board.GetFootprints()]
'''
    edited = kb.run_pcbnew('edit', {'path': path, 'out': path, 'actions': [
        {'action': 'outline_rect', 'x': 0, 'y': 0, 'width': 20, 'height': 20},
        {'action': 'script', 'code': code}]})
    ids = edited['results'][1]['result']['result']
    first = score(root)
    second = score(root)
    schema = json.loads((Path(REPO) / 'schemas/pcb-score.schema.json').read_text(encoding='utf-8'))
    jsonschema.Draft202012Validator(schema).validate(first)
    assert first == second
    assert first['outline']['closed'] and first['error'] is None
    assert set(first['courtyard_fallback_refs']) == set(ids)
    assert {f['reference'] for f in first['outside_outline']['footprints']} == set(ids)
    assert first['courtyard_overlaps']['count'] == 1
    assert {first['courtyard_overlaps']['pairs'][0][k] for k in ('a', 'b')} == set(ids)
