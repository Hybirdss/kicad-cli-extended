"""Advanced ``kce`` commands: teardrops, via stitching and fences, rule areas, dimensions,
barcodes, mounting holes and fiducials, differential pairs, lengths and tuning, 3D models,
footprint updates from library, SPICE simulation, live GUI control (IPC API), panelization,
foreign-board import and KiCad 10 design variants.

Registered on the groups defined in ``cli.py`` (imported at the end of that module), so the
command tree, error contract (``handle_error``), JSON output and auto-save are shared.
"""

from __future__ import annotations

import os
from typing import List, Optional

import click

from .cli import (SHEET_OPT, _proj, _sch_mutate, _sch_tree, cli, get_session, handle_error, output, parse_kv,
                  parse_points, parse_xy, pcb_group, project_group, sch_group, zone_group)
from .core import advanced as adv
from .core import gui as gui_mod
from .core import importer as importer_mod
from .core import panel as panel_mod
from .core import pcb as pcb_mod
from .core import project as proj_mod
from .core import schematic as sch_mod
from .core import sim as sim_mod
from .core import variants as variants_mod


def _floats(s: Optional[str], n: int, what: str) -> Optional[List[float]]:
    if s is None:
        return None
    vals = [float(v) for v in s.replace(" ", "").split(",")]
    if len(vals) != n:
        raise ValueError(f"{what}: expected {n} comma-separated numbers, got {s!r}")
    return vals


def _rect(s: Optional[str]) -> Optional[List[float]]:
    return _floats(s, 4, "--rect x,y,w,h")


def _yes(v: Optional[str]) -> Optional[bool]:
    return None if v is None else v == "yes"


# ═════════════════════════════════════════════════════════════════════════════
# pcb teardrops
# ═════════════════════════════════════════════════════════════════════════════

@pcb_group.group("teardrops")
def teardrops_group():
    """Teardrops on vias and pads (KiCad's Edit > Edit Teardrops)."""


TARGETS = click.Choice(["vias", "pth", "smd"])


@teardrops_group.command("add")
@click.option("--target", "targets", multiple=True, type=TARGETS, help="vias, pth, smd (default: all three)")
@click.option("--net", "nets", multiple=True, help="Only these nets (default: all)")
@click.option("--length-ratio", type=float, default=None, help="Length / pad size (default 0.5)")
@click.option("--max-length", type=float, default=None, help="mm, 0 = no limit (default 1.0)")
@click.option("--width-ratio", type=float, default=None, help="Width / pad size (default 1.0)")
@click.option("--max-width", type=float, default=None, help="mm, 0 = no limit (default 2.0)")
@click.option("--filter-ratio", type=float, default=None,
              help="Skip if track width >= ratio x pad size (default 0.9)")
@click.option("--curved", is_flag=True, default=None, help="Store 'curved' in the settings (geometry stays straight)")
@click.option("--pads-in-zones", is_flag=True, default=None, help="Also on pads/vias inside a same-net pour")
@click.option("--round-only", is_flag=True, help="Only round pads and vias")
@handle_error
def teardrops_add(targets, nets, length_ratio, max_length, width_ratio, max_width, filter_ratio, curved,
                  pads_in_zones, round_only):
    """Create (or regenerate) teardrops; zones are refilled and DRC sees them like KiCad's own."""
    params = {"length_ratio": length_ratio, "max_length": max_length, "width_ratio": width_ratio,
              "max_width": max_width, "filter_ratio": filter_ratio, "curved": curved, "pads_in_zones": pads_in_zones}
    output(pcb_mod.edit1(get_session(), {"action": "teardrops", "targets": list(targets), "nets": list(nets),
                                         "params": params, "round_only": round_only}, "teardrops"))


@teardrops_group.command("remove")
@click.option("--net", "nets", multiple=True, help="Only these nets (default: all)")
@click.option("--keep-settings", is_flag=True, help="Keep teardrops enabled on the pads/vias (GUI regenerates them)")
@handle_error
def teardrops_remove(nets, keep_settings):
    """Delete teardrop zones and disable teardrops on the pads/vias."""
    output(pcb_mod.edit1(get_session(), {"action": "teardrops", "mode": "remove", "nets": list(nets),
                                         "keep_settings": keep_settings}, "remove teardrops"))


# ═════════════════════════════════════════════════════════════════════════════
# via stitching, via fence, rule areas
# ═════════════════════════════════════════════════════════════════════════════

@pcb_group.command("stitch")
@click.option("--net", required=True, help="Net of the pours to stitch (e.g. /GND)")
@click.option("--pitch", type=float, default=2.0, help="Grid pitch (mm)")
@click.option("--diameter", type=float, default=0.6)
@click.option("--drill", type=float, default=0.3)
@click.option("--clearance", type=float, default=None, help="To other copper (default: board min clearance, >= 0.2)")
@click.option("--margin", type=float, default=None, help="Distance from the fill edge (default: clearance)")
@click.option("--pattern", type=click.Choice(["grid", "staggered"]), default="grid")
@click.option("--layer", "layers", multiple=True, help="Pour layers the via must land in (default: all of the net's)")
@click.option("--zone", default=None, help="Only the zone with this name or uuid")
@click.option("--rect", default=None, help="Only inside x,y,w,h")
@handle_error
def pcb_stitch(net, pitch, diameter, drill, clearance, margin, pattern, layers, zone, rect):
    """Via stitching: a grid of vias inside a net's pours, clear of other nets, holes and keepouts."""
    output(pcb_mod.edit1(get_session(), {"action": "via_stitch", "net": net, "pitch": pitch, "diameter": diameter,
                                         "drill": drill, "clearance": clearance, "margin": margin, "pattern": pattern,
                                         "layers": list(layers), "zone": zone, "rect": _rect(rect)},
                         f"via stitching {net}"))


@pcb_group.command("fence")
@click.option("--net", required=True, help="Signal net to shield (its tracks)")
@click.option("--fence-net", default="GND", help="Net of the fence vias (default GND)")
@click.option("--pitch", type=float, default=1.0)
@click.option("--offset", type=float, default=None, help="Track centre to via centre (default: w/2 + clearance + r)")
@click.option("--diameter", type=float, default=0.6)
@click.option("--drill", type=float, default=0.3)
@click.option("--clearance", type=float, default=None)
@click.option("--layer", "layers", multiple=True, help="Only tracks on these layers")
@handle_error
def pcb_fence(net, fence_net, pitch, offset, diameter, drill, clearance, layers):
    """Via fence: rows of vias both sides of a net's tracks (RF / high-speed shielding)."""
    output(pcb_mod.edit1(get_session(), {"action": "via_fence", "net": net, "fence_net": fence_net, "pitch": pitch,
                                         "offset": offset, "diameter": diameter, "drill": drill,
                                         "clearance": clearance, "layers": list(layers)}, f"via fence {net}"))


@zone_group.command("rule-area")
@click.option("--name", required=True, help="Area name (used by custom rules: A.intersectsArea('NAME'))")
@click.option("--layer", "layers", multiple=True, default=("F.Cu", "B.Cu"))
@click.option("--rect", default=None, help="x,y,w,h (default: board outline)")
@click.option("--points", default=None, help="'x,y;x,y;...'")
@click.option("--no", "disallow", multiple=True,
              type=click.Choice(["tracks", "vias", "pads", "footprints", "zone_fills"]), help="Keepout flags")
@click.option("--placement", default=None,
              help="KiCad 10 placement area: sheet=NAME, component_class=NAME, group=NAME, design_block=NAME")
@click.option("--rule", "rules", multiple=True,
              help="Custom DRC constraint for items in the area, e.g. 'clearance min 0.5mm' (repeatable)")
@handle_error
def zone_rule_area(name, layers, rect, points, disallow, placement, rules):
    """Rule area: keepouts, KiCad 10 placement areas and area-scoped custom DRC rules."""
    sess = get_session()
    a = {"action": "rule_area", "name": name, "layers": list(layers), "disallow": list(disallow),
         "placement": placement, "rect": _rect(rect)}
    if points:
        a["points"] = parse_points(points)
    res = pcb_mod.edit1(sess, a, f"rule area {name}")
    if rules:
        res["custom_rule"] = proj_mod.dru_add_rule(sess, f"area {name}", list(rules),
                                                   f"A.intersectsArea('{name}')")["rule"]
    output(res)


# ═════════════════════════════════════════════════════════════════════════════
# dimensions, barcodes, mounting holes, fiducials
# ═════════════════════════════════════════════════════════════════════════════

@pcb_group.command("dimension")
@click.argument("kind", type=click.Choice(["aligned", "orthogonal", "leader", "center", "radial"]))
@click.option("--start", required=True, help="x,y (leader: arrow tip; center/radial: circle centre)")
@click.option("--end", required=True, help="x,y (leader: text; radial: a point on the arc)")
@click.option("--height", type=float, default=5.0, help="aligned/orthogonal: offset of the dimension line")
@click.option("--vertical", is_flag=True, help="orthogonal: measure Y instead of X")
@click.option("--layer", default="Cmts.User")
@click.option("--units", type=click.Choice(["mm", "in", "mils", "auto"]), default="mm")
@click.option("--precision", type=click.IntRange(0, 5), default=None, help="Decimal places")
@click.option("--text", default=None, help="Override text (leader: the note)")
@click.option("--prefix", default=None)
@click.option("--suffix", default=None)
@click.option("--text-size", type=float, default=1.0)
@click.option("--line-width", type=float, default=0.1)
@click.option("--leader-length", type=float, default=None, help="radial only")
@handle_error
def pcb_dimension(kind, start, end, height, vertical, layer, units, precision, text, prefix, suffix, text_size,
                  line_width, leader_length):
    """Add a dimension (aligned, orthogonal, leader, center mark, radial) on any layer."""
    a = {"action": "add_dimension", "kind": kind, "start": list(parse_xy(start)), "end": list(parse_xy(end)),
         "height": height, "vertical": vertical, "layer": layer, "units": units, "precision": precision,
         "text": text, "prefix": prefix, "suffix": suffix, "text_size": text_size, "line_width": line_width,
         "leader_length": leader_length}
    output(pcb_mod.edit1(get_session(), a, f"{kind} dimension"))


@pcb_group.command("barcode")
@click.argument("text")
@click.option("--at", required=True, help="Centre x,y")
@click.option("--kind", type=click.Choice(["qr", "microqr", "datamatrix", "code39", "code128"]), default="qr")
@click.option("--size", type=float, default=5.0, help="Width (and height for 2D codes) in mm")
@click.option("--height", type=float, default=None, help="1D codes: bar height")
@click.option("--layer", default="F.SilkS")
@click.option("--ecc", type=click.Choice(["L", "M", "Q", "H"]), default=None, help="QR error correction")
@click.option("--show-text/--no-text", "show_text", default=None)
@click.option("--knockout", is_flag=True, help="Inverted (knocked-out) code")
@click.option("--angle", type=float, default=0)
@handle_error
def pcb_barcode(text, at, kind, size, height, layer, ecc, show_text, knockout, angle):
    """Add a KiCad 10 barcode (QR, Micro QR, Data Matrix, Code 39/128) on any layer."""
    x, y = parse_xy(at)
    output(pcb_mod.edit1(get_session(), {"action": "add_barcode", "text": text, "x": x, "y": y, "kind": kind,
                                         "size": size, "height": height, "layer": layer, "ecc": ecc,
                                         "show_text": show_text, "knockout": knockout, "angle": angle},
                         f"barcode {kind}"))


@pcb_group.command("hole")
@click.option("--at", required=True)
@click.option("--drill", type=float, default=3.2, help="Hole diameter (M3: 3.2)")
@click.option("--pad", type=float, default=None, help="Plated: annular pad diameter (e.g. 6.0); omit for NPTH")
@click.option("--net", default=None, help="Plated holes only (e.g. /GND)")
@click.option("--ref", default=None, help="Default: next free H<n>")
@handle_error
def pcb_hole(at, drill, pad, net, ref):
    """Add a mounting hole (board-only footprint; NPTH, or plated with --pad).

    Library holes work too: `kce pcb add-footprint MountingHole:MountingHole_3.2mm_M3 --ref H1 --at x,y`.
    """
    x, y = parse_xy(at)
    output(pcb_mod.edit1(get_session(), {"action": "add_hole", "x": x, "y": y, "drill": drill, "pad": pad, "net": net,
                                "ref": ref, "npth": pad is None}, "mounting hole"))


@pcb_group.command("fiducial")
@click.option("--at", required=True)
@click.option("--copper", type=float, default=1.0, help="Copper dot diameter")
@click.option("--mask", type=float, default=2.0, help="Solder mask opening (and copper keepout) diameter")
@click.option("--side", type=click.Choice(["top", "bottom"]), default="top")
@click.option("--ref", default=None, help="Default: next free FID<n>")
@handle_error
def pcb_fiducial(at, copper, mask, side, ref):
    """Add a fiducial (kept in position files, excluded from the BOM)."""
    x, y = parse_xy(at)
    output(pcb_mod.edit1(get_session(), {"action": "add_fiducial", "x": x, "y": y, "copper": copper, "mask": mask,
                                         "side": side, "ref": ref}, "fiducial"))


# ═════════════════════════════════════════════════════════════════════════════
# differential pairs and lengths
# ═════════════════════════════════════════════════════════════════════════════

@pcb_group.group("diffpair")
def diffpair_group():
    """Differential pairs: list, route, rules (nets ending in +/- or P/N)."""


@diffpair_group.command("list")
@handle_error
def diffpair_list():
    """Pairs KiCad recognises, with net class width/gap, lengths and skew."""
    output(pcb_mod.read(get_session(), "diffpairs"))


@diffpair_group.command("route")
@click.argument("pair")
@click.option("--from", "from_ref", required=True, help="Footprint where the pair starts (e.g. U1)")
@click.option("--to", "to_ref", required=True, help="Footprint where it ends (e.g. J1)")
@click.option("--layer", default=None)
@click.option("--width", type=float, default=None, help="Default: net class diff-pair width")
@click.option("--gap", type=float, default=None, help="Default: net class diff-pair gap")
@click.option("--via-points", default=None, help="Centre-line waypoints 'x,y;x,y'")
@handle_error
def diffpair_route(pair, from_ref, to_ref, layer, width, gap, via_points):
    """Route a pair as two coupled parallel tracks (45-degree path between the two footprints)."""
    sess = get_session()
    nets = adv.diffpair_nets(sess, pair)
    a = {"action": "diffpair_route", "p_net": nets["p"], "n_net": nets["n"], "from": from_ref, "to": to_ref,
         "layer": layer, "width": width, "gap": gap}
    if via_points:
        a["via_points"] = [list(parse_xy(q)) for q in via_points.split(";") if q.strip()]
    output(pcb_mod.edit1(sess, a, f"route pair {nets['pair']}"))


@diffpair_group.command("rule")
@click.argument("pair")
@click.option("--name", default=None, help="Rule name (default: 'diff pair <PAIR>')")
@click.option("--gap-min", type=float, default=None)
@click.option("--gap", "gap_opt", type=float, default=None, help="Optimal gap")
@click.option("--gap-max", type=float, default=None)
@click.option("--width", type=float, default=None)
@click.option("--max-uncoupled", type=float, default=None, help="Max uncoupled length (mm)")
@click.option("--max-skew", type=float, default=None, help="Max P/N length difference (mm)")
@click.option("--min", "min_len", type=float, default=None, help="Min routed length")
@click.option("--max", "max_len", type=float, default=None, help="Max routed length")
@handle_error
def diffpair_rule(pair, name, gap_min, gap_opt, gap_max, width, max_uncoupled, max_skew, min_len, max_len):
    """Custom DRC rule for pairs matching PAIR (base name, wildcards ok: '/USB_D', '/LVDS*')."""
    output(adv.diffpair_rule(get_session(), name or f"diff pair {pair}", pair, gap_min, gap_opt, gap_max, width,
                             max_uncoupled, max_skew, min_len, max_len))


@pcb_group.group("length")
def length_group():
    """Routed lengths per net / net class / pair, length rules and meander tuning."""


@length_group.command("report")
@click.option("--net", "nets", multiple=True, help="Net name or wildcard (repeatable)")
@click.option("--netclass", default=None)
@handle_error
def length_report(nets, netclass):
    """Routed length per net (tracks + pad-to-die), spread per net class, skew per pair."""
    output(pcb_mod.read(get_session(), "lengths", nets=list(nets), netclass=netclass))


@length_group.command("rule")
@click.argument("name")
@click.option("--nets", default=None, help="Net name or wildcard, e.g. '/DDR_DQ*'")
@click.option("--netclass", default=None)
@click.option("--min", "min_len", type=float, default=None)
@click.option("--opt", "opt_len", type=float, default=None)
@click.option("--max", "max_len", type=float, default=None)
@click.option("--max-skew", type=float, default=None, help="Max length difference within the matched nets")
@handle_error
def length_rule(name, nets, netclass, min_len, opt_len, max_len, max_skew):
    """Length-matching rule (custom DRC rule; `kce check drc` reports length_out_of_range / skew)."""
    output(adv.length_rule(get_session(), name, nets, netclass, min_len, max_len, opt_len, max_skew))


@length_group.command("tune")
@click.argument("net")
@click.option("--target", type=float, default=None, help="Target length (mm)")
@click.option("--match", default=None, help="Tune to this net's length instead (e.g. the other half of a pair)")
@click.option("--amplitude", type=float, default=1.0, help="Max meander amplitude (mm)")
@click.option("--spacing", type=float, default=None, help="Meander spacing (default: max(3w, w + clearance))")
@click.option("--clearance", type=float, default=None)
@click.option("--layer", default=None, help="Only segments on this layer")
@click.option("--tolerance", type=float, default=0.01)
@handle_error
def length_tune(net, target, match, amplitude, spacing, clearance, layer, tolerance):
    """Add accordion meanders to a net's straight segments until it reaches --target / --match length."""
    if (target is None) == (match is None):
        raise ValueError("Give exactly one of --target LENGTH or --match NET")
    output(pcb_mod.edit1(get_session(), {"action": "tune_length", "net": net, "target": target, "match": match,
                                         "amplitude": amplitude, "spacing": spacing, "clearance": clearance,
                                         "layer": layer, "tolerance": tolerance},
                         f"tune length {net}"))


# ═════════════════════════════════════════════════════════════════════════════
# 3D models, update footprints from library, KiCad 10 extras
# ═════════════════════════════════════════════════════════════════════════════

@pcb_group.command("models")
@click.option("--missing", is_flag=True, help="Only footprints with missing 3D model files")
@click.option("--ref", default=None, help="Reference or wildcard")
@handle_error
def pcb_models(missing, ref):
    """3D models per footprint with resolved paths; reports files that do not exist."""
    output(adv.models_report(get_session(), missing, ref))


@pcb_group.group("model")
def model_group():
    """Assign, replace, offset or remove a footprint's 3D models."""


OFFSET_OPTS = [
    click.option("--offset", default=None, help="x,y,z mm"),
    click.option("--rotation", default=None, help="x,y,z degrees"),
    click.option("--scale", default=None, help="x,y,z"),
    click.option("--opacity", type=float, default=None),
]


def _offset_opts(f):
    for o in reversed(OFFSET_OPTS):
        f = o(f)
    return f


@model_group.command("set")
@click.argument("ref")
@click.argument("file")
@click.option("--add", is_flag=True, help="Add another model instead of replacing all")
@click.option("--index", type=int, default=None, help="Replace only model N (0-based)")
@_offset_opts
@click.option("--hide", is_flag=True)
@click.option("--no-check", is_flag=True, help="Store the reference even if the file does not exist")
@handle_error
def model_set(ref, file, add, index, offset, rotation, scale, opacity, hide, no_check):
    """Assign a 3D model file (stored as ${KIPRJMOD}/... or ${KICAD10_3DMODEL_DIR}/... when possible)."""
    output(adv.set_model(get_session(), ref, file, "add" if add else "replace", index,
                         _floats(offset, 3, "--offset"), _floats(rotation, 3, "--rotation"),
                         _floats(scale, 3, "--scale"), hide or None, opacity, check=not no_check))


@model_group.command("adjust")
@click.argument("ref")
@click.option("--index", type=int, default=None, help="Only model N (default: all)")
@_offset_opts
@click.option("--show/--hide", "show", default=None)
@handle_error
def model_adjust(ref, index, offset, rotation, scale, opacity, show):
    """Change offset / rotation / scale / opacity / visibility of existing models."""
    output(adv.set_model(get_session(), ref, None, "adjust", index, _floats(offset, 3, "--offset"),
                         _floats(rotation, 3, "--rotation"), _floats(scale, 3, "--scale"),
                         None if show is None else not show, opacity))


@model_group.command("remove")
@click.argument("ref")
@click.option("--index", type=int, default=None, help="Only model N (default: all)")
@handle_error
def model_remove(ref, index):
    output(adv.set_model(get_session(), ref, None, "remove", index))


@pcb_group.command("update-footprints")
@click.argument("refs", nargs=-1)
@click.option("--match", default=None, help="Reference or library id wildcard")
@click.option("--reset-text", is_flag=True, help="Use the library's reference/value text placement")
@click.option("--reset-models", is_flag=True, help="Use the library's 3D models")
@click.option("--force", is_flag=True, help="Replace even footprints identical to the library")
@handle_error
def pcb_update_footprints(refs, match, reset_text, reset_models, force):
    """Update footprints from their libraries (keeps placement, nets, fields; reports changes)."""
    output(adv.update_footprints(get_session(), list(refs), match, reset_text, reset_models, force))


@pcb_group.command("net-ties")
@handle_error
def pcb_net_ties():
    """Net-tie footprints with their pad groups and the nets they join."""
    output(pcb_mod.read(get_session(), "board_extras")["net_ties"])


@pcb_group.command("component-classes")
@handle_error
def pcb_component_classes():
    """KiCad 10 component classes per footprint (rules: A.hasComponentClass('NAME'))."""
    rows = pcb_mod.read(get_session(), "board_extras")["footprints"]
    classes = {}
    for r in rows:
        for c in filter(None, (r["component_class"] or "").split(",")):
            classes.setdefault(c.strip(), []).append(r["reference"])
    output({"classes": classes, "footprints": [{"reference": r["reference"],
                                                "component_class": r["component_class"]} for r in rows]})


# ═════════════════════════════════════════════════════════════════════════════
# sim (SPICE)
# ═════════════════════════════════════════════════════════════════════════════

@cli.group("sim")
def sim_group():
    """SPICE simulation with ngspice: directives, source/model fields, run, parsed results."""


@sim_group.command("engine")
@handle_error
def sim_engine():
    """The ngspice used: an executable, or the shared library shipped with KiCad."""
    eng = sim_mod.find_ngspice()
    if eng is None:
        raise FileNotFoundError(sim_mod.INSTALL_HINT)
    output(eng)


@sim_group.command("netlist")
@click.option("--output", "-o", "out", default=None, help="Default <project>/outputs/sim/<name>.cir")
@handle_error
def sim_netlist(out):
    """Export KiCad's SPICE netlist (kicad-cli) and list its elements and directives."""
    proj = _proj()
    out = os.path.abspath(out or os.path.join(proj.output_dir, "sim", proj.name + ".cir"))
    os.makedirs(os.path.dirname(out), exist_ok=True)
    sim_mod.export_spice(get_session(), out)
    with open(out, encoding="utf-8", errors="replace") as f:
        text = f.read()
    output(dict(sim_mod.netlist_info(text), netlist=out, text=text[-6000:]))


@sim_group.command("run")
@click.option("--analysis", "analyses", multiple=True,
              help="Analysis to run instead of the schematic's, e.g. '.op' or '.tran 10u 5m' (repeatable)")
@click.option("--probe", "probes", multiple=True, help="Only these vectors, e.g. 'v(/out)' (repeatable)")
@click.option("--points", type=int, default=50, help="Samples per vector in the JSON (0 = summary only)")
@click.option("--output-dir", default=None, help="Default <project>/outputs/sim/")
@click.option("--timeout", type=int, default=600)
@handle_error
def sim_run(analyses, probes, points, output_dir, timeout):
    """Simulate: KiCad netlist -> ngspice -> plots and CSV files.

    Read warnings[] before trusting results: no_ground_node (use a global label 0),
    ac_source_missing (sim model V1 --params "dc=0 ac=1"), probe_not_found (includes
    available vectors; KiCad probes often need '/' such as v(/out)). Floating pins
    fail with an input error (exit 2) naming the pins to wire or label.
    """
    output(sim_mod.run(get_session(), list(analyses), list(probes), points, output_dir, timeout))


@sim_group.command("directives")
@SHEET_OPT
@handle_error
def sim_directives(sheet):
    """SPICE directives placed in the schematic (text items starting with '.')."""
    _, t = _sch_tree(sheet)
    output(sim_mod.directives(t))


@sim_group.group("directive")
def directive_group():
    """Add or remove SPICE directives (schematic text, like KiCad's simulator)."""


@directive_group.command("add")
@click.argument("text")
@click.option("--at", default=None, help="x,y (default: below the existing items)")
@SHEET_OPT
@handle_error
def directive_add(text, at, sheet):
    """Add a directive, e.g. '.tran 10u 5m', '.ac dec 20 10 1meg', '.include models.lib'."""
    _, t = _sch_mutate(sheet, f"sim directive {text}")
    output(sim_mod.add_directive(t, text, parse_xy(at) if at else None))


@directive_group.command("remove")
@click.argument("key")
@SHEET_OPT
@handle_error
def directive_remove(key, sheet):
    """Remove by exact text, uuid, or analysis keyword ('.tran' removes every .tran)."""
    _, t = _sch_mutate(sheet, f"remove sim directive {key}")
    output({"removed": sim_mod.remove_directive(t, key)})


@sim_group.command("model")
@click.argument("ref")
@click.option("--device", default=None, help="Sim.Device: V, I, R, C, L, D, NPN, PNP, NMOS, PMOS, SUBCKT, SPICE ...")
@click.option("--type", "type_", default=None, help="Sim.Type: DC, SIN, PULSE, EXP, PWL, AC ...")
@click.option("--params", default=None, help="Sim.Params, e.g. 'dc=0 ampl=1 f=1k' or "
                                            "'y1=0 y2=5 td=0 tr=1n tf=1n tw=1m per=2m' (KiCad names)")
@click.option("--pins", default=None, help="Sim.Pins, e.g. '1=+ 2=-'")
@click.option("--library", default=None, help="Sim.Library: model file (relative to the project)")
@click.option("--name", default=None, help="Sim.Name: model / subcircuit name in the library")
@click.option("--clear", is_flag=True, help="Remove all Sim.* fields first")
@SHEET_OPT
@handle_error
def sim_model(ref, device, type_, params, pins, library, name, clear, sheet):
    """Set a symbol's simulation model (the Sim.* fields KiCad's netlister reads)."""
    _, t = _sch_mutate(sheet, f"sim model {ref}")
    syms = sch_mod.find_symbols(t, ref) or [sch_mod.find_symbol(t, ref)]
    res = None
    for sym in syms:
        res = sim_mod.set_model(sym, device, type_, params, pins, library, name, clear)
    output(res)


@sim_group.command("models")
@SHEET_OPT
@handle_error
def sim_models(sheet):
    """Symbols with their Sim.* fields (empty = KiCad infers R/C/L from the value)."""
    _, t = _sch_tree(sheet)
    rows = []
    for s in sch_mod.placed_symbols(t):
        ref = sch_mod.sym_ref(s)
        if ref.startswith("#"):
            continue
        value = next((str(p[2]) for p in s.find_all("property") if str(p[1]) == "Value"), "")
        rows.append({"reference": ref, "lib_id": s.value("lib_id"), "value": value,
                     "sim_fields": sim_mod.sim_fields(s)})
    output(rows)


# ═════════════════════════════════════════════════════════════════════════════
# gui (live KiCad through the IPC API)
# ═════════════════════════════════════════════════════════════════════════════

@cli.group("gui")
def gui_group():
    """Live KiCad GUI via its IPC API (optional kicad-python): status, selection, reload, actions."""


@gui_group.command("status")
@handle_error
def gui_status():
    """Is a KiCad GUI reachable? Version and open documents (never fails: see 'connected')."""
    output(gui_mod.status())


@gui_group.command("selection")
@handle_error
def gui_selection():
    """Items selected in the PCB editor (type, uuid, reference, net, position)."""
    output(gui_mod.selection())


@gui_group.command("select")
@click.argument("refs", nargs=-1)
@click.option("--clear", is_flag=True, help="Clear the selection first")
@handle_error
def gui_select(refs, clear):
    """Select footprints by reference in the PCB editor (show the user what you mean)."""
    output(gui_mod.select(list(refs), clear))


@gui_group.command("reload")
@handle_error
def gui_reload():
    """Re-read the board from disk in the open editor (run after kce changed the file)."""
    output(gui_mod.reload())


@gui_group.command("save")
@handle_error
def gui_save():
    """Save the board open in the editor (before editing it with kce)."""
    output(gui_mod.save())


@gui_group.command("refill")
@handle_error
def gui_refill():
    """Refill all zones in the open editor."""
    output(gui_mod.refill())


@gui_group.command("action")
@click.argument("name")
@handle_error
def gui_action(name):
    """Run a KiCad tool action, e.g. common.Control.zoomFitScreen, pcbnew.InteractiveSelection.ClearSelection."""
    output(gui_mod.action(name))


# ═════════════════════════════════════════════════════════════════════════════
# panelization
# ═════════════════════════════════════════════════════════════════════════════

@pcb_group.command("panelize")
@click.option("--rows", type=int, required=True)
@click.option("--cols", type=int, required=True)
@click.option("--spacing", type=float, default=2.0, help="Gap between boards (0 = butted, V-score)")
@click.option("--mode", type=click.Choice(["mousebites", "vscore"]), default=None,
              help="Default: mousebites if spacing > 0, else vscore")
@click.option("--rails", type=click.Choice(["tb", "lr", "all", "none"]), default="tb",
              help="Breakaway rails: top+bottom, left+right, full frame, none")
@click.option("--rail-width", type=float, default=5.0)
@click.option("--tab-width", type=float, default=3.0)
@click.option("--tabs", type=int, default=None, help="Tabs per edge (default: 1 per 40 mm, max 4)")
@click.option("--bite-drill", type=float, default=0.5)
@click.option("--bite-pitch", type=float, default=0.8)
@click.option("--no-fiducials", is_flag=True)
@click.option("--no-tooling-holes", is_flag=True)
@click.option("--vscore-layer", default="Cmts.User")
@click.option("--output", "-o", "out_path", default=None,
              help="Panel .kicad_pcb (default <project>/outputs/panel/<name>-panel.kicad_pcb)")
@click.option("--engine", type=click.Choice(["builtin", "kikit"]), default="builtin")
@click.option("--drc", is_flag=True, help="Run KiCad's DRC on the panel and summarise it")
@click.option("--strict", is_flag=True, help="Reject vscore spacing coercion before writing (exit 2).")
@handle_error
def pcb_panelize(rows, cols, spacing, mode, rails, rail_width, tab_width, tabs, bite_drill, bite_pitch, no_fiducials,
                 no_tooling_holes, vscore_layer, out_path, engine, drc, strict):
    """Panelize the board: N x M copies with tabs + mouse bites or V-scores, rails, fiducials, tooling holes."""
    output(panel_mod.panelize(get_session(), rows, cols, spacing, mode, rails, rail_width, tab_width, tabs, bite_drill,
                              bite_pitch, not no_fiducials, not no_tooling_holes, vscore_layer, out_path, engine, drc, strict))


# ═════════════════════════════════════════════════════════════════════════════
# project import (Altium, Eagle, CADSTAR, PADS, ...)
# ═════════════════════════════════════════════════════════════════════════════

@project_group.command("import")
@click.argument("source", type=click.Path(exists=True, dir_okay=False))
@click.option("--dir", "directory", default=None, help="New project directory (default <source>-kicad next to it)")
@click.option("--name", default=None, help="Project name (default: source file name)")
@click.option("--format", "fmt", default="auto",
              type=click.Choice(["auto", "altium", "eagle", "cadstar", "pads", "pcad", "fabmaster", "solidworks"]))
@click.option("--overwrite", is_flag=True)
@handle_error
def project_import(source, directory, name, fmt, overwrite):
    """Import a foreign board (Altium .PcbDoc, Eagle .brd, CADSTAR, PADS, ...) into a new kce project."""
    output(importer_mod.import_board(source, directory, name, fmt, overwrite))


# ═════════════════════════════════════════════════════════════════════════════
# KiCad 10 design variants
# ═════════════════════════════════════════════════════════════════════════════

def _sheet_trees(desc: Optional[str] = None):
    """Every schematic file of the hierarchy (snapshotted and marked dirty when ``desc`` is given)."""
    proj = _proj()
    sess = get_session()
    files = []
    for h in sch_mod.hierarchy(sess.tree, proj.sch_path):
        if os.path.exists(h["file"]) or sess.has_doc(h["file"]):
            if h["file"] not in files:
                files.append(h["file"])
    if desc:
        sess.snapshot(desc, files + [proj.pro_path])
    trees = {f: sess.tree(f) for f in files}
    if desc:
        for f in files:
            sess.doc(f).mark_dirty()
    return trees


@project_group.group("variant")
def variant_group():
    """Design variants (KiCad 10): list, add, remove; per-symbol overrides with `kce sch variant`."""


@variant_group.command("list")
@handle_error
def variant_list():
    """Variants of the project and each symbol's overrides (DNP, BOM, board, fields)."""
    output(variants_mod.list_variants(get_session(), _sheet_trees()))


@variant_group.command("add")
@click.argument("name")
@click.option("--description", default="")
@handle_error
def variant_add(name, description):
    """Create a variant; export it with e.g. `kce sch export bom --variant NAME`."""
    output(variants_mod.add_variant(get_session(), name, description))


@variant_group.command("remove")
@click.argument("name")
@handle_error
def variant_remove(name):
    """Delete a variant and every symbol override that belongs to it."""
    output(variants_mod.remove_variant(get_session(), name, _sheet_trees(f"remove variant {name}")))


YESNO = click.Choice(["yes", "no"])


@sch_group.command("variant")
@click.argument("ref")
@click.argument("variant")
@click.option("--dnp", type=YESNO, default=None, help="Do not populate in this variant")
@click.option("--in-bom", type=YESNO, default=None)
@click.option("--on-board", type=YESNO, default=None)
@click.option("--in-pos-files", type=YESNO, default=None)
@click.option("--exclude-from-sim", type=YESNO, default=None)
@click.option("--field", "fields", multiple=True, help="NAME=VALUE override, e.g. Value=10k (repeatable)")
@click.option("--clear", is_flag=True, help="Drop this symbol's existing overrides for the variant first")
@SHEET_OPT
@handle_error
def sch_variant(ref, variant, dnp, in_bom, on_board, in_pos_files, exclude_from_sim, fields, clear, sheet):
    """Override a symbol in one variant (DNP, BOM/board/position-file flags, field values)."""
    _, t = _sch_mutate(sheet, f"variant {variant} {ref}")
    flags = {"dnp": _yes(dnp), "in_bom": _yes(in_bom), "on_board": _yes(on_board),
             "in_pos_files": _yes(in_pos_files), "exclude_from_sim": _yes(exclude_from_sim)}
    output(variants_mod.set_symbol_variant(get_session(), t, ref, variant, flags, parse_kv(fields), clear))
