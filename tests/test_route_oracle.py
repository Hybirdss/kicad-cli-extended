"""The autoroute gate judged by KiCad on boards built to break it.

Each board is routed twice: by `pcb autoroute`, whose gate refuses a route that breaks a connection or
adds a DRC error, and by `pcb batch`, which saves whatever the router does. route_oracle asks KiCad
about the second board, and the gate must agree with it. Each named board also states whether KiCad
refuses its route, so a board that stops exercising its scenario fails instead of passing quietly.

KCE_ROUTE_SEEDS=0-199 adds that many random boards from route_boards.random_case.
"""

import os

import pytest

import route_boards as rb
import route_oracle
from test_e2e import run, tmp_dir  # noqa: F401

# copper text over the left leg, just short of the band's join; a pour keeps clear of a text's bounding box
PROJECT_NAME_TEXT = [rb.copper_text("${PROJECTNAME}", 19, 21.7, 1)]

BOARDS = {
    # SIG's route crosses the neck that joins the two lobes of a GND pour
    "neck": (rb.neck(3), True),
    "neck-stale-fill": (rb.neck(3, fill="stale"), True),
    # unfilled, GP1 and GP2 look apart, so the same run routes GND around the pour and the cut loses nothing
    "neck-unfilled": (rb.neck(3, fill="unfilled"), False),
    "neck-hatched": (rb.neck(3, hatch=True), True),
    "neck-stub-on-another-route": (rb.neck(3, extra="stub"), True),
    "neck-gnd-routed-too": (rb.neck(3, extra="gnd-outside"), True),
    "neck-pads-share-a-number": (rb.neck(3, extra="shared-number"), True),
    "neck-via-island": (rb.neck(3, extra="via-only"), True),
    "neck-copper-shape": (rb.neck(3, extra="shape-only"), True),
    "neck-arc": (rb.neck(3, extra="arc"), True),
    # a via SIG places to hop over an F.Cu keepout lands on the neck of an inner-layer plane
    "plane-via-on-neck": (rb.plane(1.0, keepout_y=20.8), True),
    "plane-via-beside-neck": (rb.plane(1.0, keepout_y=23.5), False),
    # SIG runs under the band that joins two legs of a pour; the clearance decides whether it survives
    "band-net-class-clearance": (rb.band(0.225, 0.8, rules="net-class", clearance=1.0), True),
    "band-tight-clearance": (rb.band(0.075, 0.325, rules="every-net", clearance=0.1, zone_clearance=0.1), False),
    # the band survives the route alone; resolved to a long project name the text reaches the join
    # and thins the band from below, so the route cuts it, and resolved to a short one it does not
    "band-long-project-name-text": (dict(rb.band(0, 0.8, zone_clearance=0.5), name="PowerStage-RevC",
                                         before_fill=PROJECT_NAME_TEXT), True),
    "band-short-project-name-text": (dict(rb.band(0, 0.8, zone_clearance=0.5), name="x",
                                          before_fill=PROJECT_NAME_TEXT), False),
    # KiCad 5 boards carry no pad UUIDs, and the first save moves a pad of a rotated footprint by 1 nm
    "kicad5-pour": (rb.kicad5(15, "pour"), True),
    "kicad5-track": (rb.kicad5(15, "track"), False),
    "kicad5-known-clearance-error": (rb.kicad5(15, "track", pre_existing_violation=True), False),
    # a GND copper wall with a 0.55 mm gap: routed with 0.1 mm clearance, SIG passes through it closer
    # than the board's 0.2 mm rule, which only DRC sees
    "clearance-below-the-rules": ({
        "pads": [["S1", "1", "SIG", 10, 20], ["S2", "1", "SIG", 50, 20]],
        "shapes": [["GND", [[29, -1], [31, -1], [31, 19.725], [29, 19.725]]],
                   ["GND", [[29, 20.275], [31, 20.275], [31, 41], [29, 41]]]],
        "actions": [],
        "autoroute": ["--net", "SIG", "--layers", "F.Cu", "--grid", "0.05", "--clearance", "0.1", "--width", "0.2"]},
        True),
    # --refs S1 limits the run to SIG, whose route still crosses the neck; SIG3's stub stays unrouted
    "neck-refs": (dict(rb.neck(3, extra="stub"), autoroute=["--layers", "F.Cu", "--grid", "0.25", "--refs", "S1"]),
                  True),
    "neck-area": (dict(rb.neck(3), autoroute=["--layers", "F.Cu", "--grid", "auto", "--area", "20,2,20,36"]), True),
    # S3 joins SIG above the pour; the area holds S1 and S3 only, so S2 stays unrouted and the rest is kept
    "partial-route-in-an-area": (dict(rb.neck(3), pads=rb.neck(3)["pads"] + [["S3", "1", "SIG", 50, 6]],
                                      autoroute=["--layers", "F.Cu", "--grid", "0.25", "--area", "0,0,60,12"]), False),
    # SIG cuts bar A of a ring; bar B still joins the lobes
    "ring": (rb.ring(), False),
    # outside git, copper text ${VCSHASH} is "no hash", which stays in the left lobe
    "ring-vcshash-text-outside-git": (rb.ring("${VCSHASH}"), False),
    # no pour: J1's first MH pad is tracked to C1 and its second waits for GND to be routed later
    "pads-share-a-number-no-pour": ({
        "pads": [["J1", "MH", "GND", 10, 20], ["C1", "1", "GND", 14, 20], ["J1", "MH", "GND", 50, 30],
                 ["S1", "1", "SIG", 30, 6], ["S2", "1", "SIG", 30, 14]],
        "actions": [{"action": "add_track", "net": "GND", "layer": "F.Cu", "points": [[10, 20], [14, 20]],
                     "width": 0.25}],
        "autoroute": ["--skip-net", "GND", "--layers", "F.Cu", "--grid", "0.25"]}, False),
}


def judge(case, root):
    p = rb.build(case, root, lambda args, check: run(args, check=check))
    return route_oracle.judge(p, case.get("name", "board"), case["autoroute"],
                              lambda args, check: run(args, check=check))


@pytest.mark.parametrize("name", BOARDS)
def test_autoroute_gate_agrees_with_kicad(tmp_dir, name):
    case, kicad_refuses = BOARDS[name]
    verdict = judge(case, tmp_dir)
    if verdict["oracle_refuses"] != kicad_refuses:
        pytest.fail(f"{name} no longer tests what it was built for: {verdict}")
    assert verdict["agrees"], verdict


def test_known_gap_the_gate_cannot_see_vcshash_text(tmp_dir):
    """Inside a git repository, KiCad and kicad-cli resolve copper text ${VCSHASH} to a 40-digit hash,
    which on this ring knocks bar B out, so cutting bar A splits GND. KiCad's Python, which the gate
    refills with, resolves it to "no hash" even there, so the gate saves the route. This fails once the
    gate sees the hash; the board then belongs in BOARDS, refused."""
    verdict = judge(rb.ring("${VCSHASH}", git=True), tmp_dir)
    assert verdict["unconnected_rose"] == ["GND"] and verdict["exit"] == 0 and verdict["changed"], verdict


def seeds():
    first, _, last = os.environ.get("KCE_ROUTE_SEEDS", "").partition("-")
    return list(range(int(first), int(last or first) + 1)) if first else []


@pytest.mark.parametrize("seed", seeds())
def test_autoroute_gate_agrees_with_kicad_on_a_random_board(tmp_dir, seed):
    verdict = judge(rb.random_case(seed), tmp_dir)
    assert verdict["agrees"], verdict
