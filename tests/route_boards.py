"""Boards built to break the autoroute gate, each reproducible from a name or a seed.

A case is a dict: its project `name`; `pads` as [ref, number, net, x, y], plus "smd" for a pad on
F.Cu only; F.Cu `shapes` as [net, points] and `arcs` as [net, start, mid, end]; `actions` for
`pcb batch`, or `legacy` board text; `project` commands run before the board is built; `before_fill`
commands (copper text); `fill` to fill the pours at all; `after_fill` commands run once they are
filled (to make a fill stale); and the `autoroute` arguments. Whether the gate should refuse a case
is not written here: route_oracle.judge asks KiCad.
"""

import json
import math
import os
import random
import subprocess

SETUP = """
nets = {name: pcbnew.NETINFO_ITEM(board, name) for name in ('GND', 'SIG', 'SIG3')}
for net in nets.values():
    board.Add(net)
footprints = {}
for ref, number, name, x, y, *kind in params['pads']:  # pads with one reference share a footprint
    fp = footprints.get(ref)
    if fp is None:
        fp = footprints[ref] = pcbnew.FOOTPRINT(board)
        fp.SetReference(ref)
        board.Add(fp)
        fp.SetPosition(vec(x, y))
    pad = pcbnew.PAD(fp)
    fp.Add(pad)
    pad.SetNumber(number)
    pad.SetSize(vec(1.6, 1.6))
    if kind == ['smd']:  # on F.Cu only
        pad.SetAttribute(pcbnew.PAD_ATTRIB_SMD)
        pad.SetLayerSet(pcbnew.PAD.SMDMask())
    else:
        pad.SetAttribute(pcbnew.PAD_ATTRIB_PTH)
        pad.SetDrillSize(vec(0.8, 0.8))
    pad.SetPosition(vec(x, y))
    pad.SetNet(nets[name])
for name, points in params.get('shapes', []):  # copper polygons that carry a net
    shape = pcbnew.PCB_SHAPE(board)
    shape.SetShape(pcbnew.SHAPE_T_POLY)
    shape.SetFilled(True)
    shape.SetLayer(pcbnew.F_Cu)
    shape.SetPolyPoints([vec(x, y) for x, y in points])
    shape.SetWidth(FromMM(0.1))
    shape.SetNet(nets[name])
    board.Add(shape)
for name, start, mid, end in params.get('arcs', []):
    arc = pcbnew.PCB_ARC(board)
    arc.SetStart(vec(*start))
    arc.SetMid(vec(*mid))
    arc.SetEnd(vec(*end))
    arc.SetWidth(FromMM(0.25))
    arc.SetLayer(pcbnew.F_Cu)
    arc.SetNet(nets[name])
    board.Add(arc)
"""


def build(case, root, kce):
    """Create the case's project under `root` and return its directory."""
    name = case.get("name", "board")
    p = os.path.join(root, name)
    kce(["project", "new", p, "--name", name], True)
    os.remove(os.path.join(p, name + ".kicad_sch"))  # footprints without symbols would add parity findings
    for command in case.get("project", []):
        kce(["--project", p, *command], True)
    if case.get("legacy"):
        with open(os.path.join(p, name + ".kicad_pcb"), "w", encoding="utf-8") as f:
            f.write(case["legacy"])
        return p
    actions = os.path.join(root, "build-actions.json")
    with open(actions, "w", encoding="utf-8") as f:
        json.dump([{"action": "script", "code": SETUP,
                    "params": {"pads": case["pads"], "shapes": case.get("shapes", []), "arcs": case.get("arcs", [])}},
                   {"action": "outline_rect", "x": 0, "y": 0, "width": 60, "height": 40}, *case["actions"]], f)
    kce(["--project", p, "pcb", "batch", actions], True)
    for command in case.get("before_fill", []):
        kce(["--project", p, *command], True)
    if case.get("fill", True):
        kce(["--project", p, "pcb", "zone", "fill"], True)
    for command in case.get("after_fill", []):
        kce(["--project", p, *command], True)
    if case.get("git"):  # the project is committed to a git repository, which ${VCSHASH} reads
        for args in (["init", "-q"], ["add", "-A"],
                     ["-c", "user.name=kce", "-c", "user.email=kce@invalid", "commit", "-qm", "board"]):
            subprocess.run(["git", "-C", root, *args], check=True, stdin=subprocess.DEVNULL)
    return p


HATCH = """
for zone in board.Zones():
    zone.SetFillMode(pcbnew.ZONE_FILL_MODE_HATCH_PATTERN)
    zone.SetHatchThickness(FromMM(1))
    zone.SetHatchGap(FromMM(1.5))
"""


def _zone(points, zone_clearance=None, islands=None, hatch=False, layer="F.Cu"):
    zone = {"action": "add_zone", "net": "GND", "layers": [layer], "points": points, "fill": False}
    if zone_clearance is not None:
        zone["clearance"] = zone_clearance
    if islands:
        zone["island_removal"] = islands
    return [zone] + ([{"action": "script", "code": HATCH}] if hatch else [])


def _rules(kind, clearance):
    c = f"{clearance:g}"
    return {
        "default": [],
        "net-class": [["project", "netclass", "set", "HV", "--clearance", c, "--track-width", "0.25"],
                      ["project", "netclass", "assign", "SIG", "HV"]],
        "custom-rule": [["project", "rules", "custom-add", "sig", "-c", f"clearance min {c}mm",
                         "--condition", "A.NetName == 'SIG' || B.NetName == 'SIG'"]],
        "board-minimum": [["project", "rules", "set", f"min_clearance={c}"]],
        "every-net": [["project", "netclass", "set", "Default", "--clearance", c]],
    }[kind]


def band(gap, thickness, rules="default", clearance=0.2, zone_clearance=None, islands=None, hatch=False,
         fill="fresh", extra=None):
    """SIG runs straight along y=20 under a GND band `thickness` mm thick, `gap` mm from the track's edge.
    The band is all that joins the two legs of the pour that hold GP1 and GP2."""
    lo = round(20 + 0.125 + gap, 4)
    hi = round(lo + thickness, 4)
    outline = [[4, lo], [56, lo], [56, 36], [38, 36], [38, hi], [22, hi], [22, 36], [4, 36]]
    pads = [["GP1", "1", "GND", 12, 30], ["GP2", "1", "GND", 48, 30], ["S1", "1", "SIG", 6, 20], ["S2", "1", "SIG", 54, 20]]
    case = {"pads": pads, "actions": _zone(outline, zone_clearance, islands, hatch), "project": _rules(rules, clearance),
            "autoroute": ["--net", "SIG", "--layers", "F.Cu", "--grid", "0.25"]}
    return _finish(case, fill, extra)


def neck(width, centre=20.0, sig_x=30.0, rules="default", clearance=0.2, zone_clearance=None, islands=None,
         hatch=False, fill="fresh", extra=None, layers="F.Cu"):
    """GND pours two lobes joined by a neck `width` mm wide; SIG runs from above the pour to below it."""
    lo, hi = round(centre - width / 2, 4), round(centre + width / 2, 4)
    outline = [[4, 12], [22, 12], [22, lo], [38, lo], [38, 12], [56, 12], [56, 28], [38, 28], [38, hi], [22, hi],
               [22, 28], [4, 28]]
    pads = [["GP1", "1", "GND", 10, 20], ["GP2", "1", "GND", 50, 20], ["S1", "1", "SIG", sig_x, 6],
            ["S2", "1", "SIG", sig_x, 34]]
    case = {"pads": pads, "actions": _zone(outline, zone_clearance, islands, hatch), "project": _rules(rules, clearance),
            "autoroute": ["--layers", layers, "--grid", "0.25"]}
    return _finish(case, fill, extra)


def plane(width, centre=20.0, keepout_y=18.0, rules="default", clearance=0.2, zone_clearance=None, islands=None,
          fill="fresh", extra=None):
    """A 4-layer board whose In1.Cu GND plane is two lobes joined by a neck. SIG's pads are on F.Cu and
    an F.Cu keepout band lies across the board, so SIG's route hops to B.Cu and back, and a via it
    places on the neck cuts the plane."""
    lo, hi = round(centre - width / 2, 4), round(centre + width / 2, 4)
    outline = [[4, 12], [22, 12], [22, lo], [38, lo], [38, 12], [56, 12], [56, 28], [38, 28], [38, hi], [22, hi],
               [22, 28], [4, 28]]
    pads = [["GP1", "1", "GND", 10, 20], ["GP2", "1", "GND", 50, 20], ["S1", "1", "SIG", 30, 6, "smd"],
            ["S2", "1", "SIG", 30, 34, "smd"]]
    keepout = {"action": "add_zone", "keepout": True, "layers": ["F.Cu"], "rect": [2, keepout_y, 56, 1.0]}
    case = {"pads": pads, "actions": [{"action": "set_copper_layers", "count": 4},
                                      *_zone(outline, zone_clearance, islands, layer="In1.Cu"), keepout],
            "project": _rules(rules, clearance), "autoroute": ["--layers", "F.Cu,B.Cu", "--grid", "0.25"]}
    return _finish(case, fill, extra)


RING = """
zone = pcbnew.ZONE(board)
zone.SetLayer(pcbnew.F_Cu)
zone.SetNet(board.FindNet('GND'))
zone.SetMinThickness(FromMM(0.25))
outline = zone.Outline()
outline.NewOutline()
for x, y in params['outer']:
    outline.Append(FromMM(x), FromMM(y))
outline.NewHole()
for x, y in params['hole']:
    outline.Append(FromMM(x), FromMM(y), -1, 0)
board.Add(zone)
"""


def ring(text=None, git=False):
    """A GND pour with a hole, so bar A (y 14 to 17) and bar B (y 30.4 to 31.6) both join its left and
    right lobes; SIG's route crosses bar A. Copper `text` at (14, 31) can reach bar B."""
    case = {"pads": [["GP1", "1", "GND", 10, 20], ["GP2", "1", "GND", 50, 20], ["S1", "1", "SIG", 30, 6],
                     ["S2", "1", "SIG", 30, 24]],
            "actions": [{"action": "script", "code": RING,
                         "params": {"outer": [[4, 14], [56, 14], [56, 31.6], [4, 31.6]],
                                    "hole": [[22, 17], [38, 17], [38, 30.4], [22, 30.4]]}}],
            "autoroute": ["--net", "SIG", "--layers", "F.Cu", "--grid", "0.25"], "git": git}
    if text:
        case["before_fill"] = [copper_text(text, 14, 31, 0.6)]
    return case


def _finish(case, fill, extra):
    if fill == "unfilled":
        case["fill"] = False
    elif fill == "stale":  # a track added after the fill leaves the saved fill out of date
        case["after_fill"] = [["pcb", "track", "2,1;2,39", "--layer", "F.Cu", "--width", "0.25", "--net", "SIG3"]]
    gp2 = next(p for p in case["pads"] if p[0] == "GP2")
    if extra == "stub":  # a SIG3 route that also joins a padless stub on its way
        case["pads"] += [["A1", "1", "SIG3", 6, 4], ["B1", "1", "SIG3", 18, 4]]
        case["actions"].append({"action": "add_track", "net": "SIG3", "layer": "F.Cu", "points": [[12, 4], [12, 9]],
                                "width": 0.25})
        if "--net" in case["autoroute"]:
            i = case["autoroute"].index("--net")
            del case["autoroute"][i:i + 2]
    elif extra == "gnd-outside":  # a GND pad off the pour, so GND gains a route in the same run
        case["pads"].append(["GP3", "1", "GND", 58, 38])
        if "--net" in case["autoroute"]:
            i = case["autoroute"].index("--net")
            del case["autoroute"][i:i + 2]
    elif extra == "shared-number":  # GP1 and GP2 become two pads numbered MH of one footprint
        for p in case["pads"]:
            if p[0] in ("GP1", "GP2"):
                p[0], p[1] = "J1", "MH"
    elif extra == "via-only":  # GP2 moves next to GP1; a lone GND via holds the far side
        x, y = gp2[3], gp2[4]
        gp2[3] = next(p for p in case["pads"] if p[0] == "GP1")[3] + 3
        case["actions"].append({"action": "add_via", "net": "GND", "x": x, "y": y, "diameter": 0.8, "drill": 0.4})
    elif extra == "shape-only":  # GP2 moves next to GP1; a GND copper polygon holds the far side
        x, y = gp2[3], gp2[4]
        gp2[3] = next(p for p in case["pads"] if p[0] == "GP1")[3] + 3
        case["shapes"] = [["GND", [[x - 2, y - 2], [x + 2, y - 2], [x + 2, y + 2], [x - 2, y + 2]]]]
    elif extra == "arc":  # GP2 moves below a neck board's pour and reaches it through an arc track
        x = gp2[3]
        gp2[4] = 35
        case["arcs"] = [["GND", [x, 35], [x + 2.5, 31], [x, 26]]]
    return case


def kicad5(angle=15.0, gnd="pour", pre_existing_violation=False):
    """A board as KiCad 5 saved it: no pad UUIDs, an SOIC-8 rotated `angle` degrees whose pads 1 and 5
    are GND, joined by a pour whose neck SIG crosses (`gnd="pour"`) or by a track (`gnd="track"`).
    With `pre_existing_violation` the GND track runs straight between the pads, too close to pad 2."""
    offsets = [(-2.475, -1.905), (-2.475, -0.635), (-2.475, 0.635), (-2.475, 1.905),
               (2.475, 1.905), (2.475, 0.635), (2.475, -0.635), (2.475, -1.905)]
    pads = "".join(f"    (pad {n} smd roundrect (at {x} {y} {angle:g}) (size 1.95 0.6) (layers F.Cu F.Paste F.Mask) "
                   f"(roundrect_rratio 0.25){' (net 1 GND)' if n in (1, 5) else ''})\n"
                   for n, (x, y) in enumerate(offsets, 1))
    sig = "".join(f"""  (module TP (layer F.Cu) (tedit 5A0F774F) (tstamp 5C7D{k:04X})
    (at 30 {y})
    (fp_text reference S{k + 1} (at 0 -2.5) (layer F.SilkS) (effects (font (size 1 1) (thickness 0.15))))
    (pad 1 thru_hole circle (at 0 0) (size 1.6 1.6) (drill 0.8) (layers *.Cu *.Mask) (net 2 SIG))
  )
""" for k, y in enumerate((19.5, 27.5)))
    a = math.radians(angle)
    pad1, pad5 = [(30 + x * math.cos(a) + y * math.sin(a), 15 - x * math.sin(a) + y * math.cos(a))
                  for x, y in (offsets[0], offsets[4])]
    if gnd == "track":
        path = [pad1, pad5] if pre_existing_violation else [pad1, (25.5, 11), (35.5, 11), (35.5, pad5[1]), pad5]
        copper = "".join(f"  (segment (start {x0:.6f} {y0:.6f}) (end {x1:.6f} {y1:.6f}) (width 0.25) (layer F.Cu) (net 1))\n"
                         for (x0, y0), (x1, y1) in zip(path, path[1:]))
    else:
        neck_outline = [(22, 10), (29, 10), (29, 22), (31, 22), (31, 10), (38, 10), (38, 25), (31, 25), (31, 23.5),
                        (29, 23.5), (29, 25), (22, 25)]
        copper = ("  (zone (net 1) (net_name GND) (layer F.Cu) (hatch edge 0.508) (connect_pads (clearance 0.508))\n"
                  "    (min_thickness 0.254) (fill yes (arc_segments 32) (thermal_gap 0.508) (thermal_bridge_width 0.508))\n"
                  "    (polygon (pts " + " ".join(f"(xy {x} {y})" for x, y in neck_outline) + ")))\n")
    edges = "".join(f"  (gr_line (start {a} {b}) (end {c} {d}) (layer Edge.Cuts) (width 0.05))\n"
                    for a, b, c, d in [(0, 0, 60, 0), (60, 0, 60, 40), (60, 40, 0, 40), (0, 40, 0, 0)])
    text = f"""(kicad_pcb (version 20171130) (host pcbnew "(5.1.12)-1")
  (general (thickness 1.6))
  (page A4)
  (layers (0 F.Cu signal) (31 B.Cu signal) (35 F.Paste user) (37 F.SilkS user) (39 F.Mask user)
    (44 Edge.Cuts user) (49 F.Fab user))
  (setup (last_trace_width 0.25) (trace_clearance 0.2) (zone_clearance 0.508) (trace_min 0.2)
    (via_size 0.8) (via_drill 0.4) (via_min_size 0.4) (via_min_drill 0.3) (edge_width 0.05))
  (net 0 "") (net 1 GND) (net 2 SIG)
  (net_class Default "" (clearance 0.2) (trace_width 0.25) (via_dia 0.8) (via_drill 0.4) (add_net GND) (add_net SIG))
  (module Package_SO:SOIC-8_3.9x4.9mm_P1.27mm (layer F.Cu) (tedit 5D9F72B1) (tstamp 5C7E0001)
    (at 30 15 {angle:g})
    (fp_text reference U1 (at 0 -3.4 {angle:g}) (layer F.SilkS) (effects (font (size 1 1) (thickness 0.15))))
{pads}  )
{sig}{edges}{copper})
"""
    return {"legacy": text, "autoroute": ["--net", "SIG", "--layers", "F.Cu", "--grid", "0.25"]}


NAMES = ["board", "amplifier", "ctl-v2", "sensor_node", "x", "PowerStage-RevC"]


def copper_text(text, x, y, size):
    """Copper text on F.Cu; a pour keeps clear of what the text resolves to."""
    return ["pcb", "text", text, "--at", f"{x:g},{y:g}", "--layer", "F.Cu", "--size", f"{size:g}"]


def random_case(seed):
    """One board drawn from every family and modifier above, near the thresholds that matter."""
    rng = random.Random(seed)
    family = rng.choice(["band", "band", "neck", "plane", "kicad5"])
    name = rng.choice(NAMES)
    if family == "kicad5":
        case = kicad5(angle=rng.choice([10, 15, 22.5, 30, 37, 45, 60]), gnd=rng.choice(["pour", "track"]),
                      pre_existing_violation=rng.random() < 0.5)
        case["name"] = name
        return case
    common = dict(rules=rng.choice(["default", "net-class", "custom-rule", "board-minimum", "every-net"]),
                  clearance=rng.choice([0.1, 0.15, 0.2, 0.3, 0.5, 0.8, 1.0]),
                  zone_clearance=rng.choice([None, None, 0.1, 0.2, 0.3, 0.5]),
                  islands=rng.choice([None, "always", "never", "area"]), hatch=rng.random() < 0.15,
                  fill=rng.choice(["fresh", "fresh", "stale", "unfilled"]),
                  extra=rng.choice([None, None, "stub", "gnd-outside", "shared-number", "via-only", "shape-only"]
                                   + (["arc"] if family == "neck" else [])))
    if family == "band":
        # a pour keeps the larger of the rule and zone clearances from the track, and a band thinner than
        # its 0.25 mm minimum width vanishes; sample around where that leaves the band just alive
        rule = common["clearance"] if common["rules"] == "every-net" else max(common["clearance"], 0.2)
        if common["rules"] == "default":
            rule = 0.2
        reach = max(rule, common["zone_clearance"] or 0)
        thickness = round(rng.uniform(0.3, 1.2), 2)
        gap = max(0.0, round((reach - thickness + 0.25 + rng.uniform(-0.15, 0.15)) / 0.025) * 0.025)
        case = band(gap=gap, thickness=thickness, **common)
        near = (rng.uniform(24, 36), 20.125 + gap + thickness + rng.uniform(0.2, 1.5))
    elif family == "plane":
        # SIG's first via lands about 0.8 mm above the keepout, and its hole with clearances is 1 to 1.6 mm
        # across; sample necks around the width and offset at which it cuts them
        width, centre = round(rng.uniform(0.4, 1.4), 2), round(rng.uniform(16, 24), 2)
        case = plane(width=width, centre=centre, keepout_y=round(centre + 0.8 + rng.uniform(-0.6, 0.6), 2),
                     **{k: v for k, v in common.items() if k != "hatch"})
        near = (rng.uniform(36, 46), centre)
    else:
        width, centre = round(rng.uniform(0.6, 6.0), 2), round(rng.uniform(16, 24), 2)
        case = neck(width=width, centre=centre, sig_x=round(rng.uniform(24, 36), 2), **common)
        near = (rng.uniform(36, 46), centre + rng.uniform(-3, 3))
    case["name"] = name
    if rng.random() < 0.4:
        text = rng.choice(["${PROJECTNAME}", "${FILENAME}", "REV-A", "${PROJECTNAME}-${FILENAME}"])
        case["before_fill"] = [copper_text(text, round(near[0], 2), round(near[1], 2), rng.choice([1, 1.5, 2, 2.5]))]
    return case
