"""An oracle for the autoroute gate that shares no code with it.

The gate decides inside kce whether a route broke a connection. This module decides the same thing
from outside: it routes a copy of the board with `pcb batch` (which saves whatever the router does)
and asks KiCad about that copy before and after. KiCad's Python refills the pours in the board's
own project and reports which copper is connected; copper is matched by kind, reference, pad number
and position within a few micrometres, never by UUID, so the answer holds for boards that KiCad 5
saved without pad UUIDs. kicad-cli's DRC, run in place, reports the errors and a second view of
connectivity: adding copper can only lower a net's count of unconnected items, so a count that
rises means the route split the net. The second view sees what KiCad's Python cannot, such as copper
text whose ${VCSHASH} only kicad-cli resolves inside a git repository; but a count can stay the same
while a net splits, when the same run also routes that net.
"""

import json
import os
import shutil
import subprocess
import tempfile

from kicad_cli_extended.utils import kicad_backend as kb

TOLERANCE_NM = 5000

COPPER_SCRIPT = r'''
import json, sys
import pcbnew
board = pcbnew.LoadBoard(sys.argv[1])
pcbnew.ZONE_FILLER(board).Fill(board.Zones())
board.BuildConnectivity()
conn = board.GetConnectivity()

def describe(x):
    cls = x.GetClass()
    if cls == "PAD":
        p = x.GetPosition()
        return ["pad", x.GetParentFootprint().GetReference(), x.GetNumber()], [[p.x, p.y]]
    if cls == "PCB_VIA":
        p = x.GetPosition()
        return ["via", "", ""], [[p.x, p.y]]
    if cls in ("PCB_TRACK", "PCB_ARC"):
        ends = sorted([[x.GetStart().x, x.GetStart().y], [x.GetEnd().x, x.GetEnd().y]])
        return ["track", str(x.GetLayer()), ""], ends
    box = x.GetBoundingBox()
    return ["shape", str(x.GetLayer()), ""], [[box.GetX(), box.GetY()], [box.GetRight(), box.GetBottom()]]

items = [p for fp in board.GetFootprints() for p in fp.Pads()] + list(board.GetTracks())
items += [d for d in board.GetDrawings() if isinstance(d, pcbnew.BOARD_CONNECTED_ITEM) and d.IsOnCopperLayer()]
items = [x for x in items if x.GetNetCode() > 0]
out = []
for x in items:
    sig, pts = describe(x)
    out.append({"net": x.GetNetname(), "sig": sig, "pts": pts, "address": int(x.this)})
by_address = {o["address"]: i for i, o in enumerate(out)}
clusters, seen = [], set()
for x in items:
    i = by_address[int(x.this)]
    if i in seen:
        continue
    group = {i} | {by_address[int(y.this)] for y in conn.GetConnectedItems(x) if int(y.this) in by_address}
    seen |= group
    clusters.append(sorted(group))
for o in out:
    del o["address"]
print(json.dumps({"items": out, "clusters": clusters}))
'''


def copper(board_path):
    """KiCad's own view of a board's connected copper, with every pour refilled in its project."""
    with tempfile.TemporaryDirectory(prefix="kce-oracle-") as td:
        script = os.path.join(td, "copper.py")
        with open(script, "w", encoding="utf-8") as f:
            f.write(COPPER_SCRIPT)
        r = subprocess.run([*kb.find_pcbnew_python()["command"], script, board_path], capture_output=True,
                           text=True, encoding="utf-8", stdin=subprocess.DEVNULL)
    if r.returncode:
        raise RuntimeError(f"oracle could not read {board_path}:\n{r.stderr[-3000:]}")
    return json.loads(r.stdout.strip().splitlines()[-1])


def _close(a, b):
    return len(a) == len(b) and all(abs(p[0] - q[0]) <= TOLERANCE_NM and abs(p[1] - q[1]) <= TOLERANCE_NM
                                    for p, q in zip(a, b))


def _match(before, after):
    """Map each item of `before` to the item of `after` that is the same copper, or None."""
    free = {}
    for j, item in enumerate(after):
        free.setdefault((item["net"], *item["sig"]), []).append(j)
    pairs = {}
    for i, item in enumerate(before):
        candidates = free.get((item["net"], *item["sig"]), [])
        hit = next((j for j in candidates if _close(item["pts"], after[j]["pts"])), None)
        if hit is not None:
            candidates.remove(hit)
        pairs[i] = hit
    return pairs


def lost_connections(before, after):
    """Nets whose copper one cluster joined before routing and that is split, or gone, after it."""
    pairs = _match(before["items"], after["items"])
    where = {j: k for k, cluster in enumerate(after["clusters"]) for j in cluster}
    lost = set()
    for cluster in before["clusters"]:
        parts = {where.get(pairs[i], ("missing", i)) for i in cluster}
        if len(parts) > 1:
            lost.add(before["items"][cluster[0]]["net"])
    return sorted(lost)


def drc(project_dir, board_path):
    """kicad-cli DRC of a board in its project, pours refilled, every severity: the violations, and
    the number of unconnected items per net. By default DRC reports one error per track, and which
    one depends on item order, which a save changes; every error of every track keeps two runs
    comparable."""
    out = os.path.join(project_dir, "oracle-drc.json")
    subprocess.run([*kb.kicad_cli_command(), "pcb", "drc", "--format", "json", "--units", "mm", "--severity-all",
                    "--refill-zones", "--all-track-errors", "-o", out, board_path], capture_output=True,
                   stdin=subprocess.DEVNULL)
    with open(out, encoding="utf-8") as f:
        report = json.load(f)
    os.remove(out)
    unconnected = {}
    for v in report.get("unconnected_items", []):
        net = _net_of(v["items"][0]["description"])
        unconnected[net] = unconnected.get(net, 0) + 1
    return [v for v in report.get("violations", []) if not v.get("excluded")], unconnected


def _net_of(description):
    """The net in brackets in an item description, such as "Pad 1 [GND] of GP1", "1의 PTH 패드[GND]GP1"
    or, for a bus member, "Pad 1 [A[1]] of J1"."""
    start, depth = description.index("["), 0
    for i in range(start, len(description)):
        depth += {"[": 1, "]": -1}.get(description[i], 0)
        if depth == 0:
            return description[start + 1:i]
    raise ValueError(f"no net in {description!r}")


def new_drc_errors(before, after):
    """Errors in `after` that `before` did not have, matched by type and item positions."""
    def key(v):
        return sorted([round(it["pos"]["x"] * 1e6), round(it["pos"]["y"] * 1e6)] for it in v.get("items", []))
    known = [(v["type"], key(v)) for v in before]
    new = []
    for v in after:
        if v.get("severity") != "error":
            continue
        hit = next((k for k in known if k[0] == v["type"] and _close(k[1], key(v))), None)
        if hit is None:
            new.append(v)
        else:
            known.remove(hit)
    return new


def routed_copper(board_path):
    """The tracks and vias of a board, as comparable tuples rounded to the micrometre."""
    c = copper(board_path)
    return sorted((i["net"], i["sig"][0], i["sig"][1], tuple(tuple(round(v / 1000) for v in p) for p in i["pts"]))
                  for i in c["items"] if i["sig"][0] in ("track", "via"))


def judge(project_dir, board_name, autoroute_args, kce):
    """Run the gated `pcb autoroute` and the ungated `pcb batch` route on copies of a project and
    compare the gate's verdict with what KiCad says about the ungated result.

    `kce(args, check)` runs the CLI. Returns a dict with the gate's exit code, whether it changed
    the board, the oracle's lost nets and new DRC errors, and whether the gate agreed: a refusal must
    leave the board as it was and name the same lost nets and new error types, and a save must hold
    exactly the copper the ungated router laid.
    """
    root = os.path.dirname(project_dir)
    gated, free = os.path.join(root, "gated"), os.path.join(root, "ungated")
    for d in (gated, free):
        shutil.rmtree(d, ignore_errors=True)
        shutil.copytree(project_dir, d)
    board = board_name + ".kicad_pcb"
    free_board = os.path.join(free, board)
    before, (before_errors, before_unconnected) = copper(free_board), drc(free, free_board)
    original_text = open(os.path.join(gated, board), encoding="utf-8").read()
    r = kce(["--json", "--project", gated, "pcb", "autoroute", *autoroute_args], False)
    gated_text = open(os.path.join(gated, board), encoding="utf-8").read()

    action = {"action": "autoroute_builtin"}
    flags = list(autoroute_args)
    while flags:
        flag = flags.pop(0)
        value = flags.pop(0)
        name = flag.lstrip("-").replace("-", "_")
        if name == "layers":
            action[name] = value.split(",")
        elif name == "skip_net":
            action.setdefault("skip_nets", []).append(value)
        elif name == "grid":
            action[name] = value if value == "auto" else float(value)
        elif name in ("width", "clearance", "via_diameter", "via_drill"):
            action[name] = float(value)
        elif name == "area":
            action[name] = [float(v) for v in value.split(",")]
        elif name == "refs":
            action[name] = value.split(",")
        else:
            action[name] = value
    actions = os.path.join(root, "route-actions.json")
    with open(actions, "w", encoding="utf-8") as f:
        json.dump([action], f)
    kce(["--json", "--project", free, "pcb", "batch", actions], True)

    after_errors, after_unconnected = drc(free, free_board)
    rose = sorted(n for n, k in after_unconnected.items() if k > before_unconnected.get(n, 0))
    lost = sorted(set(lost_connections(before, copper(free_board))) | set(rose))
    errors = new_drc_errors(before_errors, after_errors)
    refuse = bool(lost or errors)
    verdict = {"exit": r.returncode, "changed": gated_text != original_text, "oracle_lost": lost,
               "unconnected_rose": rose, "oracle_new_errors": sorted({v["type"] for v in errors}),
               "oracle_refuses": refuse}
    if refuse:
        details = json.loads(r.stderr).get("details", {}) if r.returncode == 3 else {}
        verdict["gate_lost"] = sorted({c["net"] for c in details.get("lost_connections", [])})
        verdict["gate_new_errors"] = sorted({v["type"] for v in details.get("new_violations", [])})
        verdict["agrees"] = (r.returncode == 3 and not verdict["changed"] and verdict["gate_lost"] == lost
                             and verdict["gate_new_errors"] == verdict["oracle_new_errors"])
    else:
        verdict["agrees"] = r.returncode == 0 and routed_copper(os.path.join(gated, board)) == routed_copper(free_board)
    verdict["gate_output"] = (r.stderr if r.returncode else r.stdout)[-1500:]
    return verdict
