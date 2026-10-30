#!/usr/bin/env python3
"""pcbnew worker — executed by the interpreter that can ``import pcbnew``.

Protocol: ``python worker.py request.json result.json``.  The request is
``{"op": name, "params": {...}}``; the result is ``{"ok": true, "result": ...}``
or ``{"ok": false, "error": msg, "traceback": tb}``.

Only the standard library and KiCad's SWIG ``pcbnew`` module are used here so
the worker runs in KiCad's interpreter independent of the CLI's environment.
All user-facing units are millimetres and degrees.
"""

import heapq
import json
import math
import os
import re
import sys
import traceback
import time
import warnings

warnings.filterwarnings("ignore")

import pcbnew  # noqa: E402

MM = pcbnew.FromMM


def mm(v):
    return round(pcbnew.ToMM(v), 6)


def vec(x, y):
    return pcbnew.VECTOR2I(MM(float(x)), MM(float(y)))


def pt(v):
    return [mm(v.x), mm(v.y)]


def layer_id(board, name):
    lid = board.GetLayerID(name)
    if lid < 0:
        raise ValueError(f"Unknown layer {name!r}. Enabled: {enabled_layers(board)}")
    return lid


def enabled_layers(board):
    return [board.GetLayerName(l) for l in board.GetEnabledLayers().Seq()]


def load(path):
    if not os.path.exists(path):
        raise FileNotFoundError(f"Board not found: {path}")
    b = pcbnew.LoadBoard(path)
    if b is None:
        raise RuntimeError(f"pcbnew failed to load {path}")
    return b


def save(board, out):
    board.BuildConnectivity()
    ok = pcbnew.SaveBoard(out, board, True)
    if ok is False:
        raise RuntimeError(f"pcbnew failed to save {out}")
    return out


# Items removed from a board stay referenced until the process ends: letting Python free a removed
# zone while the board's caches still know it corrupts the SWIG session (seen with teardrop zones).
_GRAVEYARD = []


def _remove(board, item):
    board.Remove(item)
    _GRAVEYARD.append(item)


def get_fp(board, ref):
    fp = board.FindFootprintByReference(ref)
    if fp is None:
        refs = sorted(f.GetReference() for f in board.GetFootprints())
        raise KeyError(f"No footprint {ref!r} on board. Footprints: {refs[:80]}")
    return fp


def get_net(board, name, create=False):
    if name in (None, ""):
        return board.FindNet(0)
    n = board.FindNet(name)
    if n is None:
        if not create:
            raise KeyError(f"Net {name!r} not on board. Use `kce pcb update` or check `kce pcb nets`.")
        n = pcbnew.NETINFO_ITEM(board, name)
        board.Add(n)
    return n


def get_pad(board, spec):
    """'REF:PAD' or 'REF.PAD'"""
    m = re.match(r"^(.+?)[:.]([^:.]+)$", spec)
    if not m:
        raise ValueError(f"Expected REF:PAD, got {spec!r}")
    fp = get_fp(board, m.group(1))
    pads = [p for p in fp.Pads() if p.GetNumber() == m.group(2)]
    if not pads:
        raise KeyError(f"{m.group(1)} has no pad {m.group(2)!r}; pads: {sorted({p.GetNumber() for p in fp.Pads()})}")
    return pads[0]


def side_of(fp):
    return "bottom" if fp.IsFlipped() else "top"


def fp_dict(fp, pads=False):
    d = {
        "reference": fp.GetReference(), "value": fp.GetValue(), "fpid": fp.GetFPIDAsString(),
        "x": mm(fp.GetPosition().x), "y": mm(fp.GetPosition().y),
        "rotation": round(fp.GetOrientationDegrees(), 4), "side": side_of(fp), "locked": fp.IsLocked(),
        "layer": fp.GetLayerName(), "pad_count": fp.GetPadCount(), "path": fp.GetPath().AsString(),
        "uuid": fp.m_Uuid.AsString(),
        "dnp": bool(fp.IsDNP()) if hasattr(fp, "IsDNP") else False,
        "exclude_from_bom": bool(fp.IsExcludedFromBOM()) if hasattr(fp, "IsExcludedFromBOM") else False,
        "exclude_from_pos": bool(fp.IsExcludedFromPosFiles()) if hasattr(fp, "IsExcludedFromPosFiles") else False,
    }
    try:
        bb = fp.GetCourtyard(pcbnew.B_CrtYd if fp.IsFlipped() else pcbnew.F_CrtYd).BBox()
        if bb.GetWidth() > 0:
            d["courtyard"] = {"min": [mm(bb.GetX()), mm(bb.GetY())],
                              "max": [mm(bb.GetRight()), mm(bb.GetBottom())]}
    except Exception:
        pass
    bb = fp.GetBoundingBox(False)
    d["bbox"] = {"min": [mm(bb.GetX()), mm(bb.GetY())], "max": [mm(bb.GetRight()), mm(bb.GetBottom())]}
    if pads:
        d["pads"] = [pad_dict(p) for p in fp.Pads()]
        d["fields"] = {f.GetName(): f.GetText() for f in fp.GetFields()}
    return d


def pad_dict(p):
    lay = p.GetPrincipalLayer()
    return {"number": p.GetNumber(), "net": p.GetNetname(), "x": mm(p.GetPosition().x), "y": mm(p.GetPosition().y),
            "size": pt(p.GetSize(lay)), "drill": pt(p.GetDrillSize()), "attribute": int(p.GetAttribute()),
            "type": {0: "thru_hole", 1: "smd", 2: "connect", 3: "np_thru_hole"}.get(int(p.GetAttribute()), "?"),
            "layers": [l for l in [p.GetBoard().GetLayerName(x) for x in p.GetLayerSet().Seq()]] if p.GetBoard() else [],
            "pinfunction": p.GetPinFunction(), "pintype": p.GetPinType()}


# ---------------------------------------------------------------------------
# Read-only operations
# ---------------------------------------------------------------------------


def op_info(p):
    b = load(p["path"])
    b.BuildConnectivity()
    c = b.GetConnectivity()
    bb = b.GetBoardEdgesBoundingBox()
    ds = b.GetDesignSettings()
    nets = [n for n in b.GetNetsByName().values() if n.GetNetCode() > 0]
    return {
        "path": p["path"], "copper_layers": b.GetCopperLayerCount(), "enabled_layers": enabled_layers(b),
        "footprints": len(b.GetFootprints()), "tracks": sum(1 for t in b.GetTracks() if t.GetClass() != "PCB_VIA"),
        "vias": sum(1 for t in b.GetTracks() if t.GetClass() == "PCB_VIA"), "zones": len(b.Zones()),
        "drawings": len(b.GetDrawings()), "nets": len(nets),
        "unrouted": c.GetUnconnectedCount(False),
        "outline_bbox": {"min": [mm(bb.GetX()), mm(bb.GetY())], "max": [mm(bb.GetRight()), mm(bb.GetBottom())],
                         "width": mm(bb.GetWidth()), "height": mm(bb.GetHeight())} if bb.GetWidth() > 0 else None,
        "thickness": mm(ds.GetBoardThickness()),
        "design": {"min_track_width": mm(ds.m_TrackMinWidth), "min_clearance": mm(ds.m_MinClearance),
                   "min_via_diameter": mm(ds.m_ViasMinSize), "min_through_drill": mm(ds.m_MinThroughDrill),
                   "copper_edge_clearance": mm(ds.m_CopperEdgeClearance), "hole_clearance": mm(ds.m_HoleClearance)},
        "pcbnew_version": pcbnew.Version(),
    }


def op_footprints(p):
    b = load(p["path"])
    return [fp_dict(fp, pads=p.get("pads", False)) for fp in b.GetFootprints()]


def op_footprint(p):
    b = load(p["path"])
    return fp_dict(get_fp(b, p["ref"]), pads=True)


def _effective_netclass(b, n):
    try:
        nc = b.GetDesignSettings().m_NetSettings.GetEffectiveNetClass(n.GetNetname())
        return nc.GetName()
    except Exception:
        return n.GetNetClassName()


def op_nets(p):
    b = load(p["path"])
    try:
        b.SynchronizeNetsAndNetClasses(True)
    except Exception:
        pass
    b.BuildConnectivity()
    c = b.GetConnectivity()
    pads_by_net = {}
    for fp in b.GetFootprints():
        for pad in fp.Pads():
            pads_by_net.setdefault(pad.GetNetCode(), []).append(f"{fp.GetReference()}:{pad.GetNumber()}")
    track_len = {}
    for t in b.GetTracks():
        if t.GetClass() != "PCB_VIA":
            track_len[t.GetNetCode()] = track_len.get(t.GetNetCode(), 0) + t.GetLength()
    out = []
    for name, n in b.GetNetsByName().items():
        code = n.GetNetCode()
        if code <= 0:
            continue
        out.append({"name": str(name), "code": code, "netclass": _effective_netclass(b, n),
                    "pads": pads_by_net.get(code, []), "track_length": mm(track_len.get(code, 0))})
    return out


def op_tracks(p):
    b = load(p["path"])
    out = []
    for t in b.GetTracks():
        if p.get("net") and t.GetNetname() != p["net"]:
            continue
        d = {"type": "via" if t.GetClass() == "PCB_VIA" else ("arc" if t.GetClass() == "PCB_ARC" else "track"),
             "net": t.GetNetname(), "uuid": t.m_Uuid.AsString(), "locked": t.IsLocked()}
        if d["type"] == "via":
            d.update({"at": pt(t.GetPosition()), "diameter": mm(t.GetWidth(pcbnew.F_Cu)), "drill": mm(t.GetDrillValue()),
                      "layers": [b.GetLayerName(t.TopLayer()), b.GetLayerName(t.BottomLayer())],
                      "via_type": int(t.GetViaType())})
        else:
            d.update({"start": pt(t.GetStart()), "end": pt(t.GetEnd()), "width": mm(t.GetWidth()),
                      "layer": t.GetLayerName(), "length": mm(t.GetLength())})
        out.append(d)
    return out


def op_zones(p):
    b = load(p["path"])
    out = []
    for z in b.Zones():
        o = z.Outline()
        pts = []
        if o.OutlineCount():
            ch = o.Outline(0)
            pts = [pt(ch.CPoint(i)) for i in range(ch.PointCount())]
        out.append({"uuid": z.m_Uuid.AsString(), "net": z.GetNetname(), "name": z.GetZoneName(),
                    "layers": [b.GetLayerName(l) for l in z.GetLayerSet().Seq()], "priority": z.GetAssignedPriority(),
                    "filled": z.IsFilled(), "rule_area": z.GetIsRuleArea(), "outline": pts,
                    "min_thickness": mm(z.GetMinThickness()), "clearance": mm(z.GetLocalClearance() or 0)
                    if not hasattr(z.GetLocalClearance(), "value") else None,
                    "filled_area_mm2": round(z.GetFilledArea() / 1e12, 3) if hasattr(z, "GetFilledArea") else None})
    return out


def op_drawings(p):
    b = load(p["path"])
    out = []
    for d in b.GetDrawings():
        e = {"class": d.GetClass(), "layer": d.GetLayerName(), "uuid": d.m_Uuid.AsString()}
        if d.GetClass() == "PCB_SHAPE":
            e["shape"] = d.ShowShape() if hasattr(d, "ShowShape") else int(d.GetShape())
            e["start"], e["end"] = pt(d.GetStart()), pt(d.GetEnd())
            e["width"] = mm(d.GetWidth())
        elif hasattr(d, "GetText"):
            e["text"] = d.GetText()
            e["at"] = pt(d.GetPosition())
        if p.get("layer") and d.GetLayer() != layer_id(b, p["layer"]):
            continue
        out.append(e)
    return out


def op_layers(p):
    b = load(p["path"])
    out = []
    for l in b.GetEnabledLayers().Seq():
        out.append({"id": int(l), "name": b.GetLayerName(l), "standard_name": pcbnew.LayerName(l)
                    if hasattr(pcbnew, "LayerName") else b.GetStandardLayerName(l),
                    "copper": pcbnew.IsCopperLayer(l), "type": int(b.GetLayerType(l)) if pcbnew.IsCopperLayer(l) else None})
    return out


def _clusters(board):
    """Group pads of each net into copper-connected clusters."""
    board.BuildConnectivity()
    c = board.GetConnectivity()
    res = {}
    for fp in board.GetFootprints():
        for pad in fp.Pads():
            if pad.GetNetCode() <= 0:
                continue
            res.setdefault(pad.GetNetCode(), []).append(pad)
    clusters = {}
    for code, pads in res.items():
        # File/UUID order must not choose MST ties or the direction of an autorouted connection.
        remaining = sorted(pads, key=lambda p: (p.GetPosition().y, p.GetPosition().x,
                           p.GetParentFootprint().GetReference(), p.GetNumber(), p.GetPrincipalLayer()))
        groups = []
        while remaining:
            seed = remaining.pop(0)
            group = [seed]
            try:
                conn = c.GetConnectedItems(seed)
                ids = {x.m_Uuid.AsString() for x in conn if x.GetClass() == "PAD"}
            except Exception:
                ids = set()
            keep = []
            for q in remaining:
                if q.m_Uuid.AsString() in ids:
                    group.append(q)
                else:
                    keep.append(q)
            remaining = keep
            groups.append(group)
        clusters[code] = groups
    return clusters


def op_connectivity(p):
    b = load(p["path"])
    cl = _clusters(b)
    c = b.GetConnectivity()
    nets = []
    for code, groups in cl.items():
        if len(groups) > 1:
            n = b.FindNet(code)
            nets.append({"net": n.GetNetname(), "clusters": [[f"{x.GetParentFootprint().GetReference()}:{x.GetNumber()}"
                                                              for x in g] for g in groups],
                         "missing_connections": len(groups) - 1})
    nets.sort(key=lambda d: d["net"])
    return {"unrouted_connections": c.GetUnconnectedCount(False), "nets_incomplete": len(nets), "nets": nets,
            "fully_routed": c.GetUnconnectedCount(False) == 0}


def op_ratsnest(p):
    """Minimum-spanning connections still to route (pad pairs, between clusters)."""
    b = load(p["path"])
    return _ratsnest(b, p.get("net"))


def _footprint_id(fp):
    """Keep annotated references; identify anonymous footprints by persistent board UUID."""
    return fp.GetReference() or "uuid:" + fp.m_Uuid.AsString()


def _ratsnest(b, only_net=None):
    cl = _clusters(b)
    out = []
    for code, groups in cl.items():
        if len(groups) < 2:
            continue
        n = b.FindNet(code)
        if only_net and n.GetNetname() != only_net:
            continue
        # Prim over clusters, distance = closest pad pair
        connected = [groups[0]]
        rest = groups[1:]
        while rest:
            best = None
            for gi, g in enumerate(rest):
                for a in [x for grp in connected for x in grp]:
                    for q in g:
                        d = (a.GetPosition() - q.GetPosition()).EuclideanNorm()
                        if best is None or d < best[0]:
                            best = (d, gi, a, q)
            d, gi, a, q = best
            out.append({"net": n.GetNetname(),
                        "from": f"{_footprint_id(a.GetParentFootprint())}:{a.GetNumber()}",
                        "to": f"{_footprint_id(q.GetParentFootprint())}:{q.GetNumber()}",
                        "from_xy": pt(a.GetPosition()), "to_xy": pt(q.GetPosition()), "length": mm(d)})
            connected.append(rest.pop(gi))
    out.sort(key=lambda e: (round(e["length"], 3), e["net"], e["from"], e["to"]))
    return out


def _cross(o, a, b_):
    return (a[0] - o[0]) * (b_[1] - o[1]) - (a[1] - o[1]) * (b_[0] - o[0])


def _crossings(segs):
    """Pairs of segments that properly cross (a shared endpoint is not a crossing)."""
    out = []
    for i, s in enumerate(segs):
        p1, p2 = tuple(s["from_xy"]), tuple(s["to_xy"])
        for t in segs[i + 1:]:
            q1, q2 = tuple(t["from_xy"]), tuple(t["to_xy"])
            if len({p1, p2, q1, q2}) < 4:
                continue
            d1, d2 = _cross(q1, q2, p1), _cross(q1, q2, p2)
            d3, d4 = _cross(p1, p2, q1), _cross(p1, p2, q2)
            if (d1 > 0) != (d2 > 0) and (d3 > 0) != (d4 > 0):
                out.append({"a": f"{s['net']} {s['from']}-{s['to']}",
                            "b": f"{t['net']} {t['from']}-{t['to']}"})
    return out


def op_score(p):
    """Board measurements only (no judgment), in mm, every list ordered deterministically.

    HPWL per net and total (half the perimeter of the pads' bounding box), courtyard overlap pairs
    with their overlap area, footprints outside the outline, crossing pairs of unrouted ratsnest
    connections, and ratsnest plus track length binned on a grid (density = length / bin area).
    """
    b = load(p["path"])
    b.BuildConnectivity()
    only = p.get("net")
    bin_size = float(p.get("bin_size") or 5.0)
    if not math.isfinite(bin_size) or bin_size <= 0:
        raise ValueError("--bin-size must be finite and > 0")
    names = {n.GetNetCode(): n.GetNetname() for n in b.GetNetsByName().values() if n.GetNetCode() > 0}
    pads = {}
    for fp in b.GetFootprints():
        for q in fp.Pads():
            if q.GetNetCode() > 0:
                pads.setdefault(q.GetNetCode(), []).append(q)
    hpwl = []
    for code in sorted(pads, key=lambda c: names.get(c, "")):
        name = names.get(code)
        if not name or (only and name != only):
            continue
        xs = [mm(q.GetPosition().x) for q in pads[code]]
        ys = [mm(q.GetPosition().y) for q in pads[code]]
        hpwl.append({"net": name, "pads": len(pads[code]),
                     "hpwl": round((max(xs) - min(xs)) + (max(ys) - min(ys)), 4),
                     "min": [min(xs), min(ys)], "max": [max(xs), max(ys)]})
    fps = sorted(b.GetFootprints(), key=lambda fp: (_refkey(fp), _footprint_id(fp)))
    rects = [(fp, _fp_rect(fp)) for fp in fps]
    overlaps = []
    for i, (fa, ra) in enumerate(rects):
        for fb, rb in rects[i + 1:]:
            if not _overlaps(ra, rb):
                continue
            poly = _fp_poly(fa)
            poly.BooleanIntersection(_fp_poly(fb))
            area = poly.Area() / float(MM(1)) ** 2
            if area > 1e-9:
                overlaps.append({"a": _footprint_id(fa), "b": _footprint_id(fb), "area": round(area, 4)})
    outline = _board_outline(b)
    outside = [{"reference": _footprint_id(fp), "bbox": [round(v, 4) for v in r]}
               for fp, r in rects if outline is None or not _rect_inside(outline, r)]
    segs = _ratsnest(b, only)
    crossings = _crossings(segs)
    bb = outline.BBox() if outline is not None else b.GetBoardEdgesBoundingBox()
    x0, y0 = mm(bb.GetX()), mm(bb.GetY())
    width, height = mm(bb.GetWidth()), mm(bb.GetHeight())
    nx, ny = max(1, int(math.ceil(width / bin_size))), max(1, int(math.ceil(height / bin_size)))
    lengths = [0.0] * (nx * ny)

    def add_segment(a_, b_):
        d = math.hypot(b_[0] - a_[0], b_[1] - a_[1])
        if d <= 0:
            return
        steps = max(1, int(math.ceil(d / (bin_size / 4))))
        for k in range(steps):
            t = (k + 0.5) / steps
            x, y = a_[0] + (b_[0] - a_[0]) * t, a_[1] + (b_[1] - a_[1]) * t
            i = min(nx - 1, max(0, int((x - x0) / bin_size)))
            j = min(ny - 1, max(0, int((y - y0) / bin_size)))
            lengths[j * nx + i] += d / steps

    for s in segs:
        add_segment(s["from_xy"], s["to_xy"])
    for t in b.GetTracks():
        if only and t.GetNetname() != only:
            continue
        add_segment(pt(t.GetStart()), pt(t.GetEnd()))
    bins = [{"x": round(x0 + (k % nx + 0.5) * bin_size, 3), "y": round(y0 + (k // nx + 0.5) * bin_size, 3),
             "length": round(v, 4), "density": round(v / (bin_size * bin_size), 5)}
            for k, v in enumerate(lengths) if v > 0]
    bins.sort(key=lambda e: (-e["density"], e["x"], e["y"]))
    total = sum(lengths)
    area = max(width * height, 1e-9)
    return {"path": p["path"], "schema_version": "1.0", "units": "mm", "net_filter": only,
            "outline": {"present": any(g.GetLayer() == pcbnew.Edge_Cuts for g in b.GetDrawings()),
                        "closed": outline is not None, "method": "KiCad GetBoardPolygonOutlines"},
            "error": None if outline is not None else "No closed board outline; create one with pcb outline rect",
            "courtyard_fallback_refs": [_footprint_id(fp) for fp in fps if not fp_dict(fp).get("courtyard")],
            "hpwl": {"total": round(sum(e["hpwl"] for e in hpwl), 4), "nets": hpwl},
            "courtyard_overlaps": {"count": len(overlaps), "pairs": overlaps,
                                    "method": "courtyard polygon intersection; body bounding box fallback"},
            "outside_outline": {"count": len(outside), "footprints": outside},
            "ratsnest_crossings": {"count": len(crossings), "pairs": crossings},
            "congestion": {"bin_size": bin_size, "grid": [nx, ny], "total_length": round(total, 4),
                           "max_density": bins[0]["density"] if bins else 0.0,
                           "mean_density": round(total / area, 5), "hot_bins": len(bins),
                           "hottest": bins[:10]}}


def op_stackup(p):
    b = load(p["path"])
    ds = b.GetDesignSettings()
    return {"copper_layers": b.GetCopperLayerCount(), "thickness": mm(ds.GetBoardThickness())}


# ---------------------------------------------------------------------------
# Mutations (batched): op "edit" with a list of actions
# ---------------------------------------------------------------------------


def _add_shape(board, layer, shape, width, **kw):
    s = pcbnew.PCB_SHAPE(board)
    s.SetShape(shape)
    s.SetLayer(layer_id(board, layer))
    s.SetWidth(MM(width))
    if "start" in kw:
        s.SetStart(vec(*kw["start"]))
    if "end" in kw:
        s.SetEnd(vec(*kw["end"]))
    if "center" in kw:
        s.SetCenter(vec(*kw["center"]))
    if "points" in kw:
        s.SetPolyPoints([vec(*q) for q in kw["points"]])
    if kw.get("filled"):
        s.SetFilled(True)
    board.Add(s)
    return s


def a_outline_rect(b, a):
    x, y, w, h = a["x"], a["y"], a["width"], a["height"]
    r = a.get("corner_radius", 0) or 0
    lw = a.get("line_width", 0.1)
    if a.get("replace", True):
        a_clear_outline(b, {})
    if r <= 0:
        pts = [(x, y), (x + w, y), (x + w, y + h), (x, y + h), (x, y)]
        for s, e in zip(pts, pts[1:]):
            _add_shape(b, "Edge.Cuts", pcbnew.SHAPE_T_SEGMENT, lw, start=s, end=e)
    else:
        segs = [((x + r, y), (x + w - r, y)), ((x + w, y + r), (x + w, y + h - r)),
                ((x + w - r, y + h), (x + r, y + h)), ((x, y + h - r), (x, y + r))]
        for s, e in segs:
            _add_shape(b, "Edge.Cuts", pcbnew.SHAPE_T_SEGMENT, lw, start=s, end=e)
        corners = [((x + r, y + r), (x, y + r)), ((x + w - r, y + r), (x + w - r, y)),
                   ((x + w - r, y + h - r), (x + w, y + h - r)), ((x + r, y + h - r), (x + r, y + h))]
        for c, st in corners:
            s = pcbnew.PCB_SHAPE(b)
            s.SetShape(pcbnew.SHAPE_T_ARC)
            s.SetLayer(pcbnew.Edge_Cuts)
            s.SetWidth(MM(lw))
            s.SetCenter(vec(*c))
            s.SetStart(vec(*st))
            s.SetArcAngleAndEnd(pcbnew.EDA_ANGLE(90, pcbnew.DEGREES_T), True)
            b.Add(s)
    return {"outline": "rect", "x": x, "y": y, "width": w, "height": h, "corner_radius": r}


def a_outline_polygon(b, a):
    if a.get("replace", True):
        a_clear_outline(b, {})
    pts = [tuple(q) for q in a["points"]]
    if pts[0] != pts[-1]:
        pts.append(pts[0])
    for s, e in zip(pts, pts[1:]):
        _add_shape(b, "Edge.Cuts", pcbnew.SHAPE_T_SEGMENT, a.get("line_width", 0.1), start=s, end=e)
    return {"outline": "polygon", "points": len(pts) - 1}


def a_outline_circle(b, a):
    if a.get("replace", True):
        a_clear_outline(b, {})
    cx, cy, r = a["x"], a["y"], a["radius"]
    _add_shape(b, "Edge.Cuts", pcbnew.SHAPE_T_CIRCLE, a.get("line_width", 0.1), start=(cx, cy), end=(cx + r, cy))
    return {"outline": "circle", "center": [cx, cy], "radius": r}


def a_clear_outline(b, a):
    n = 0
    for d in list(b.GetDrawings()):
        if d.GetLayer() == pcbnew.Edge_Cuts:
            _remove(b, d)
            n += 1
    return {"removed_edge_items": n}


def a_add_graphic(b, a):
    kind = a["shape"]
    m = {"line": pcbnew.SHAPE_T_SEGMENT, "rect": pcbnew.SHAPE_T_RECTANGLE if hasattr(pcbnew, "SHAPE_T_RECTANGLE") else pcbnew.SHAPE_T_RECT,
         "circle": pcbnew.SHAPE_T_CIRCLE, "polygon": pcbnew.SHAPE_T_POLY}
    if kind not in m:
        raise ValueError("graphic shape must be line|rect|circle|polygon")
    kw = {}
    if kind in ("line", "rect"):
        kw = {"start": a["start"], "end": a["end"]}
    elif kind == "circle":
        kw = {"start": a["center"], "end": [a["center"][0] + a["radius"], a["center"][1]]}
    else:
        kw = {"points": a["points"]}
    s = _add_shape(b, a.get("layer", "F.SilkS"), m[kind], a.get("width", 0.15), filled=a.get("filled", False), **kw)
    return {"added": kind, "uuid": s.m_Uuid.AsString(), "layer": a.get("layer", "F.SilkS")}


def a_add_text(b, a):
    t = pcbnew.PCB_TEXT(b)
    t.SetText(a["text"])
    t.SetPosition(vec(a["x"], a["y"]))
    t.SetLayer(layer_id(b, a.get("layer", "F.SilkS")))
    size = MM(a.get("size", 1.0))
    t.SetTextSize(pcbnew.VECTOR2I(size, size))
    t.SetTextThickness(MM(a.get("thickness", 0.15)))
    if a.get("angle"):
        t.SetTextAngleDegrees(a["angle"])
    if a.get("layer", "F.SilkS").startswith("B."):
        t.SetMirrored(True)
    b.Add(t)
    return {"text": a["text"], "uuid": t.m_Uuid.AsString()}


def a_move_fp(b, a):
    fp = get_fp(b, a["ref"])
    if fp.IsLocked() and not a.get("force"):
        raise RuntimeError(f"{a['ref']} is locked; unlock it first or pass --force")
    pos = fp.GetPosition()
    x = a["x"] if a.get("x") is not None else mm(pos.x)
    y = a["y"] if a.get("y") is not None else mm(pos.y)
    x += a.get("dx", 0) or 0
    y += a.get("dy", 0) or 0
    fp.SetPosition(vec(x, y))
    if a.get("rotation") is not None:
        fp.SetOrientationDegrees(float(a["rotation"]))
    return fp_dict(fp)


def a_rotate_fp(b, a):
    fp = get_fp(b, a["ref"])
    if a.get("angle") is not None:
        fp.SetOrientationDegrees(float(a["angle"]))
    else:
        fp.SetOrientationDegrees(fp.GetOrientationDegrees() + float(a.get("by", 90)))
    return fp_dict(fp)


def a_flip_fp(b, a):
    fp = get_fp(b, a["ref"])
    want = a.get("side")
    if want is None or (want == "bottom") != fp.IsFlipped():
        fp.Flip(fp.GetPosition(), pcbnew.FLIP_DIRECTION_LEFT_RIGHT)
    return fp_dict(fp)


def a_lock_fp(b, a):
    refs = a["refs"]
    out = []
    for r in refs:
        fp = get_fp(b, r)
        fp.SetLocked(bool(a.get("locked", True)))
        out.append(r)
    return {"locked" if a.get("locked", True) else "unlocked": out}


def a_set_fp_field(b, a):
    fp = get_fp(b, a["ref"])
    name, val = a["field"], a["value"]
    if name == "Reference":
        fp.SetReference(val)
    elif name == "Value":
        fp.SetValue(val)
    else:
        f = fp.GetField(name) if fp.HasField(name) else None
        if f is None:
            f = pcbnew.PCB_FIELD(fp, fp.GetNextFieldOrdinal(), name)
            f.SetVisible(False)
            f.SetLayer(pcbnew.F_Fab)
            fp.Add(f)
        f.SetText(val)
    if a.get("visible") is not None:
        fld = fp.GetField(name) if fp.HasField(name) else None
        if fld is not None:
            fld.SetVisible(bool(a["visible"]))
    return {"reference": fp.GetReference(), "field": name, "value": val}


def a_set_fp_attr(b, a):
    fp = get_fp(b, a["ref"])
    attr = a["attr"]
    v = bool(a["value"])
    if attr == "dnp":
        fp.SetDNP(v)
    elif attr == "exclude_from_bom":
        fp.SetExcludedFromBOM(v)
    elif attr == "exclude_from_pos":
        fp.SetExcludedFromPosFiles(v)
    elif attr in ("smd", "through_hole", "virtual"):
        fl = fp.GetAttributes() & ~(pcbnew.FP_SMD | pcbnew.FP_THROUGH_HOLE)
        if attr == "smd" and v:
            fl |= pcbnew.FP_SMD
        if attr == "through_hole" and v:
            fl |= pcbnew.FP_THROUGH_HOLE
        fp.SetAttributes(fl)
    else:
        raise ValueError("attr must be dnp|exclude_from_bom|exclude_from_pos|smd|through_hole")
    return fp_dict(fp)


def a_delete_fp(b, a):
    fp = get_fp(b, a["ref"])
    _remove(b, fp)
    return {"deleted": a["ref"]}


def _load_fp(lib_path, name):
    fp = pcbnew.FootprintLoad(lib_path, name)
    if fp is None:
        raise KeyError(f"Footprint {name!r} not found in {lib_path}")
    return fp


def a_add_fp(b, a):
    fp = _load_fp(a["lib_path"], a["name"])
    lib_nick = a.get("lib_nickname")
    if lib_nick:
        fp.SetFPIDAsString(f"{lib_nick}:{a['name']}")
    fp.SetReference(a.get("ref", "REF**"))
    if a.get("value") is not None:
        fp.SetValue(a["value"])
    b.Add(fp)
    fp.SetPosition(vec(a.get("x", 0), a.get("y", 0)))
    if a.get("rotation"):
        fp.SetOrientationDegrees(float(a["rotation"]))
    if a.get("side") == "bottom":
        fp.Flip(fp.GetPosition(), pcbnew.FLIP_DIRECTION_LEFT_RIGHT)
    if a.get("path"):
        fp.SetPath(pcbnew.KIID_PATH(a["path"]))
    for pn, net in (a.get("pad_nets") or {}).items():
        for pad in fp.Pads():
            if pad.GetNumber() == pn:
                pad.SetNet(get_net(b, net, create=True))
    return fp_dict(fp)


def a_add_track(b, a):
    pts = a["points"]
    lay = layer_id(b, a.get("layer", "F.Cu"))
    net = get_net(b, a.get("net"), create=True) if a.get("net") else None
    out = []
    for s, e in zip(pts, pts[1:]):
        t = pcbnew.PCB_TRACK(b)
        t.SetStart(vec(*s))
        t.SetEnd(vec(*e))
        t.SetWidth(MM(a.get("width", 0.25)))
        t.SetLayer(lay)
        if net is not None:
            t.SetNet(net)
        b.Add(t)
        out.append(t.m_Uuid.AsString())
    return {"segments": out, "net": a.get("net"), "layer": a.get("layer", "F.Cu")}


def a_add_via(b, a):
    v = pcbnew.PCB_VIA(b)
    v.SetPosition(vec(a["x"], a["y"]))
    vt = {"through": pcbnew.VIATYPE_THROUGH, "blind": pcbnew.VIATYPE_BLIND, "buried": pcbnew.VIATYPE_BURIED,
          "micro": pcbnew.VIATYPE_MICROVIA}[a.get("via_type", "through")]
    v.SetViaType(vt)
    v.SetLayerPair(layer_id(b, a.get("top", "F.Cu")), layer_id(b, a.get("bottom", "B.Cu")))
    v.SetWidth(MM(a.get("diameter", 0.6)))
    v.SetDrill(MM(a.get("drill", 0.3)))
    if a.get("net"):
        v.SetNet(get_net(b, a["net"], create=True))
    b.Add(v)
    return {"via": v.m_Uuid.AsString(), "at": [a["x"], a["y"]], "net": a.get("net")}


def a_delete_tracks(b, a):
    n = 0
    uuids = set(a.get("uuids") or [])
    for t in list(b.GetTracks()):
        if uuids and t.m_Uuid.AsString() not in uuids:
            continue
        if a.get("net") and t.GetNetname() != a["net"]:
            continue
        if a.get("layer") and t.GetClass() != "PCB_VIA" and t.GetLayerName() != a["layer"]:
            continue
        if a.get("vias_only") and t.GetClass() != "PCB_VIA":
            continue
        if t.IsLocked() and not a.get("include_locked"):
            continue
        _remove(b, t)
        n += 1
    return {"deleted": n}


def a_delete_item(b, a):
    uid = a["uuid"]
    for coll in (b.GetTracks(), b.GetDrawings(), b.Zones(), b.GetFootprints()):
        for it in list(coll):
            if it.m_Uuid.AsString() == uid:
                _remove(b, it)
                return {"deleted": uid, "class": it.GetClass()}
    raise KeyError(f"No board item with uuid {uid}")


def a_route(b, a):
    """Route a pad-to-pad connection as an orthogonal/45° polyline on one layer."""
    pa, pb = get_pad(b, a["from"]), get_pad(b, a["to"])
    if pa.GetNetCode() != pb.GetNetCode() and not a.get("force"):
        raise ValueError(f"{a['from']} ({pa.GetNetname()}) and {a['to']} ({pb.GetNetname()}) are on different nets")
    s, e = pa.GetPosition(), pb.GetPosition()
    sx, sy, ex, ey = mm(s.x), mm(s.y), mm(e.x), mm(e.y)
    mode = a.get("mode", "45")
    pts = [(sx, sy)]
    for v in a.get("via_points") or []:
        pts.append(tuple(v))
    if mode == "direct":
        pts.append((ex, ey))
    else:
        last = pts[-1]
        dx, dy = ex - last[0], ey - last[1]
        if abs(dx) < 1e-6 or abs(dy) < 1e-6:
            pass
        elif mode == "45":
            d = min(abs(dx), abs(dy))
            if abs(dx) > abs(dy):
                pts.append((last[0] + math.copysign(abs(dx) - d, dx), last[1]))
            else:
                pts.append((last[0], last[1] + math.copysign(abs(dy) - d, dy)))
        elif mode == "hv":
            pts.append((ex, last[1]))
        elif mode == "vh":
            pts.append((last[0], ey))
        pts.append((ex, ey))
    net = pa.GetNetname()
    lay = a.get("layer")
    if lay is None:
        lay = "F.Cu" if pa.IsOnLayer(pcbnew.F_Cu) and pb.IsOnLayer(pcbnew.F_Cu) else "B.Cu"
    width = a.get("width")
    if width is None:
        settings = b.GetDesignSettings()
        net_settings = settings.m_NetSettings
        nc = (net_settings.GetEffectiveNetClass(pa.GetNetname())
              if hasattr(net_settings, "GetEffectiveNetClass") else net_settings.GetDefaultNetclass())
        width = mm(nc.GetTrackWidth()) if nc is not None else 0
        if not math.isfinite(width) or width <= 0:
            tracks = [t for t in b.GetTracks() if t.GetNetname() == net and not isinstance(t, pcbnew.PCB_VIA)]
            tracks.sort(key=lambda t: (t.GetStart().x - pa.GetPosition().x) ** 2 +
                                     (t.GetStart().y - pa.GetPosition().y) ** 2)
            width = mm(tracks[0].GetWidth()) if tracks else mm(b.GetDesignSettings().m_TrackMinWidth)
        if not math.isfinite(width) or width <= 0:
            raise ValueError("No positive route width configured; pass --width MM or set the net class track width")
    res = a_add_track(b, {"points": pts, "layer": lay, "net": net, "width": width})
    res.update({"from": a["from"], "to": a["to"], "points": [list(q) for q in pts], "width": width})
    return res


def _zone_outline_points(b, a):
    if a.get("points"):
        return [tuple(q) for q in a["points"]]
    if a.get("rect"):
        x, y, w, h = a["rect"]
        return [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]
    bb = b.GetBoardEdgesBoundingBox()
    if bb.GetWidth() <= 0:
        raise ValueError("No board outline: give --rect/--points or create an outline first")
    m = a.get("margin", 0.0)
    x0, y0, x1, y1 = mm(bb.GetX()) - m, mm(bb.GetY()) - m, mm(bb.GetRight()) + m, mm(bb.GetBottom()) + m
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


def a_add_zone(b, a):
    z = pcbnew.ZONE(b)
    layers = a.get("layers") or [a.get("layer", "F.Cu")]
    if len(layers) == 1:
        z.SetLayer(layer_id(b, layers[0]))
    else:
        ls = pcbnew.LSET()
        for l in layers:
            ls.AddLayer(layer_id(b, l))
        z.SetLayerSet(ls)
    if a.get("keepout"):
        z.SetIsRuleArea(True)
        z.SetDoNotAllowTracks(bool(a.get("no_tracks", True)))
        z.SetDoNotAllowVias(bool(a.get("no_vias", True)))
        z.SetDoNotAllowPads(bool(a.get("no_pads", False)))
        z.SetDoNotAllowFootprints(bool(a.get("no_footprints", False)))
        z.SetDoNotAllowZoneFills(bool(a.get("no_fills", True)))
    else:
        if a.get("net"):
            z.SetNet(get_net(b, a["net"]))
        z.SetMinThickness(MM(a.get("min_width", 0.25)))
        if a.get("clearance") is not None:
            z.SetLocalClearance(MM(a["clearance"]))
        conn = {"thermal": pcbnew.ZONE_CONNECTION_THERMAL, "solid": pcbnew.ZONE_CONNECTION_FULL,
                "none": pcbnew.ZONE_CONNECTION_NONE, "tht_thermal": pcbnew.ZONE_CONNECTION_THT_THERMAL,
                "auto": pcbnew.ZONE_CONNECTION_THT_THERMAL}
        z.SetPadConnection(conn[a.get("pad_connection", "auto")])
        z.SetThermalReliefGap(MM(a.get("thermal_gap", 0.5)))
        z.SetThermalReliefSpokeWidth(MM(a.get("thermal_width", 0.5)))
        z.SetIslandRemovalMode({"always": pcbnew.ISLAND_REMOVAL_MODE_ALWAYS, "never": pcbnew.ISLAND_REMOVAL_MODE_NEVER,
                                "area": pcbnew.ISLAND_REMOVAL_MODE_AREA}[a.get("island_removal", "always")])
    z.SetAssignedPriority(int(a.get("priority", 0)))
    if a.get("name"):
        z.SetZoneName(a["name"])
    o = z.Outline()
    o.NewOutline()
    for x, y in _zone_outline_points(b, a):
        o.Append(MM(x), MM(y))
    z.SetBorderDisplayStyle(pcbnew.ZONE_BORDER_DISPLAY_STYLE_DIAGONAL_EDGE, pcbnew.ZONE.GetDefaultHatchPitch(), True)
    b.Add(z)
    res = {"zone": z.m_Uuid.AsString(), "net": a.get("net"), "layers": layers, "keepout": bool(a.get("keepout"))}
    if not a.get("keepout"):
        mode = a.get("pad_connection", "auto")
        res["pad_connection_resolved"] = {"smd": "solid", "tht": "thermal"} if mode in ("auto", "tht_thermal") else {"smd": mode, "tht": mode}
    if a.get("fill", True) and not a.get("keepout"):
        res["fill"] = a_fill_zones(b, {})
    return res


def a_fill_zones(b, a):
    b.BuildConnectivity()
    filler = pcbnew.ZONE_FILLER(b)
    zones = b.Zones()
    ok = filler.Fill(zones)
    b.BuildConnectivity()
    return {"filled": len(zones), "ok": bool(ok),
            "zones": [{"net": z.GetNetname(), "filled": z.IsFilled()} for z in zones]}


def a_unfill_zones(b, a):
    n = 0
    for z in b.Zones():
        z.UnFill()
        n += 1
    return {"unfilled": n}


def a_delete_zones(b, a):
    n = 0
    for z in list(b.Zones()):
        if a.get("net") and z.GetNetname() != a["net"]:
            continue
        if a.get("uuid") and z.m_Uuid.AsString() != a["uuid"]:
            continue
        _remove(b, z)
        n += 1
    return {"deleted": n}


def a_set_copper_layers(b, a):
    n = int(a["count"])
    if n < 1 or n > 32 or (n > 1 and n % 2):
        raise ValueError("copper layer count must be 1 or an even number 2..32")
    b.SetCopperLayerCount(n)
    ls = b.GetEnabledLayers()
    for l in pcbnew.LSET.AllCuMask(n).Seq():
        ls.AddLayer(l)
    b.SetEnabledLayers(ls)
    return {"copper_layers": b.GetCopperLayerCount(), "enabled": enabled_layers(b)}


def a_rename_layer(b, a):
    lid = layer_id(b, a["layer"])
    b.SetLayerName(lid, a["name"])
    return {"layer": a["layer"], "name": a["name"]}


def a_set_pad_net(b, a):
    pad = get_pad(b, a["pad"])
    pad.SetNet(get_net(b, a["net"], create=True))
    return {"pad": a["pad"], "net": a["net"]}


def a_set_board_thickness(b, a):
    b.GetDesignSettings().SetBoardThickness(MM(a["thickness"]))
    return {"thickness": a["thickness"]}


def a_set_origin(b, a):
    ds = b.GetDesignSettings()
    if a.get("kind", "aux") == "grid":
        ds.SetGridOrigin(vec(a["x"], a["y"]))
    else:
        ds.SetAuxOrigin(vec(a["x"], a["y"]))
    return {"origin": a.get("kind", "aux"), "at": [a["x"], a["y"]]}


# ---------------------------------------------------------------------------
# Placement geometry (place grid, legalize, place near)
# ---------------------------------------------------------------------------

def _refkey(fp):
    m_ = re.match(r"([A-Za-z#]+)(\d*)", fp.GetReference())
    return (m_.group(1), int(m_.group(2) or 0)) if m_ else (fp.GetReference(), 0)


def _fp_rect(fp):
    """(minx, miny, maxx, maxy) in mm: the footprint's courtyard, else its body bounding box."""
    layer = pcbnew.B_CrtYd if fp.IsFlipped() else pcbnew.F_CrtYd
    try:
        c = fp.GetCourtyard(layer)
        if c is not None and c.OutlineCount():
            bb = c.BBox()
            if bb.GetWidth() > 0 and bb.GetHeight() > 0:
                return (mm(bb.GetX()), mm(bb.GetY()), mm(bb.GetRight()), mm(bb.GetBottom()))
    except Exception:
        pass
    bb = fp.GetBoundingBox(False)
    return (mm(bb.GetX()), mm(bb.GetY()), mm(bb.GetRight()), mm(bb.GetBottom()))


def _fp_poly(fp):
    """The footprint's courtyard outline, else its body bounding box, as a SHAPE_POLY_SET."""
    layer = pcbnew.B_CrtYd if fp.IsFlipped() else pcbnew.F_CrtYd
    try:
        c = fp.GetCourtyard(layer)
        if c is not None and c.OutlineCount():
            return pcbnew.SHAPE_POLY_SET(c)
    except Exception:
        pass
    return _rect_poly_mm(_fp_rect(fp))


def _rect_poly_mm(rect):
    poly = pcbnew.SHAPE_POLY_SET()
    poly.NewOutline()
    for x, y in ((rect[0], rect[1]), (rect[2], rect[1]), (rect[2], rect[3]), (rect[0], rect[3])):
        poly.Append(MM(x), MM(y))
    return poly


def _grow(rect, d):
    return (rect[0] - d, rect[1] - d, rect[2] + d, rect[3] + d)


def _overlaps(a, b):
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def _rect_inside(poly, rect, margin=0.0):
    """Is the whole rectangle inside the outline, including its cutouts, by ``margin``?"""
    outside = _rect_poly_mm(_grow(rect, margin))
    outside.BooleanSubtract(poly)
    return outside.Area() == 0


class _FreeSpace:
    """Free space inside the board outline as a cell grid: 0 = free, 1 = occupied or outside.

    A rectangle fits when every cell it covers is free, so one footprint's courtyard keeps the
    next one out with a single byte per cell instead of pairwise geometry.
    """

    def __init__(self, poly, cell=0.5, max_cells=250000):
        bb = poly.BBox()
        self.x0, self.y0 = mm(bb.GetX()), mm(bb.GetY())
        w, h = max(mm(bb.GetWidth()), 1e-3), max(mm(bb.GetHeight()), 1e-3)
        while (w / cell + 2) * (h / cell + 2) > max_cells:
            cell *= 1.25
        self.cell = cell
        self.nx, self.ny = int(math.ceil(w / cell)) + 2, int(math.ceil(h / cell)) + 2
        self.bad = bytearray(self.nx * self.ny)
        for j in range(self.ny):
            for i in range(self.nx):
                if not poly.Contains(self.point(i, j)):
                    self.bad[j * self.nx + i] = 1

    def point(self, i, j):
        return pcbnew.VECTOR2I(MM(self.x0 + (i + 0.5) * self.cell), MM(self.y0 + (j + 0.5) * self.cell))

    def span(self, rect):
        i0 = int(math.floor((rect[0] - self.x0) / self.cell))
        j0 = int(math.floor((rect[1] - self.y0) / self.cell))
        i1 = int(math.ceil((rect[2] - self.x0) / self.cell))
        j1 = int(math.ceil((rect[3] - self.y0) / self.cell))
        return i0, j0, i1, j1

    def free(self, rect, accept=None):
        i0, j0, i1, j1 = self.span(rect)
        if i0 < 0 or j0 < 0 or i1 > self.nx or j1 > self.ny:
            return False
        for j in range(j0, j1):
            base = j * self.nx
            if 1 in self.bad[base + i0:base + i1]:
                return False
        return accept is None or accept(rect)

    def occupy(self, rect):
        i0, j0, i1, j1 = self.span(rect)
        i0, j0, i1, j1 = max(i0, 0), max(j0, 0), min(i1, self.nx), min(j1, self.ny)
        for j in range(j0, j1):
            base = j * self.nx
            self.bad[base + i0:base + i1] = b"\x01" * (i1 - i0)

    def first_free(self, rect, accept=None):
        """First cell-aligned min corner of ``rect`` (rows then columns) that is free and accepted."""
        i0, j0, i1, j1 = self.span(rect)
        wc, hc = i1 - i0, j1 - j0
        for j in range(self.ny - hc + 1):
            for i in range(self.nx - wc + 1):
                if not self._span_free(i, j, wc, hc):
                    continue
                corner = (self.x0 + i * self.cell, self.y0 + j * self.cell)
                if accept is None or accept((corner[0], corner[1], corner[0] + wc * self.cell,
                                             corner[1] + hc * self.cell)):
                    return corner
        return None

    def _span_free(self, i, j, wc, hc):
        for jj in range(j, j + hc):
            base = jj * self.nx
            if 1 in self.bad[base + i:base + i + wc]:
                return False
        return True


def a_place_grid(b, a):
    """Place footprints inside the outline without overlapping any standing footprint.

    Every footprint that is not being placed keeps its courtyard (body bbox when it has none) in
    the occupancy grid, grown by half the spacing; each placed footprint claims its own cells the
    same way. Footprints that fit nowhere inside the outline go in a row below the board and are
    listed in ``unplaced_inside_outline``. Footprints not in ``refs`` never move.
    """
    poly = _board_outline(b)
    if a.get("area"):
        x, y, w, h = a["area"]
        poly = _rect_poly_mm((x, y, x + w, y + h))
    if poly is None:
        raise ValueError("No board outline; pass --area x,y,w,h")
    spacing = float(a.get("spacing", 1.0))
    margin = float(a.get("margin", 0.5))
    refs = a.get("refs")
    moving = [fp for fp in b.GetFootprints()
              if (not refs or fp.GetReference() in refs) and not fp.IsLocked()]
    if a.get("only_unplaced"):
        moving = [fp for fp in moving if not _rect_inside(poly, _fp_rect(fp))]
    if not moving:
        return {"placed": [], "unplaced_inside_outline": []}
    if a.get("order", "ref") == "size":
        sizes = {fp.GetReference(): _fp_rect(fp) for fp in moving}
        moving.sort(key=lambda f: (-(sizes[f.GetReference()][2] - sizes[f.GetReference()][0])
                                   * (sizes[f.GetReference()][3] - sizes[f.GetReference()][1]), _refkey(f)))
    else:
        moving.sort(key=_refkey)
    # pcbnew hands out a fresh Python proxy per iteration: compare footprints by reference, never by
    # identity, or every footprint counts as "standing" (even the ones being placed).
    moving_refs = {fp.GetReference() for fp in moving}
    standing = [fp for fp in b.GetFootprints() if fp.GetReference() not in moving_refs]
    if not standing:
        return _row_layout(b, moving, a, poly)
    grid = _FreeSpace(poly, cell=float(a.get("cell", 0.5)))
    for fp in standing:
        grid.occupy(_grow(_fp_rect(fp), spacing))
    for t in b.GetTracks():  # routed copper: a new part may not land on a track or via
        tb = t.GetBoundingBox()
        grid.occupy((mm(tb.GetX()) - spacing, mm(tb.GetY()) - spacing,
                     mm(tb.GetRight()) + spacing, mm(tb.GetBottom()) + spacing))
    placed, unplaced = [], []
    bb = poly.BBox()
    cursor_x, cursor_y, row_h = mm(bb.GetX()), mm(bb.GetBottom()) + margin + 2.0, 0.0
    for fp in moving:
        rect = _fp_rect(fp)
        w, h = rect[2] - rect[0], rect[3] - rect[1]
        px, py = mm(fp.GetPosition().x), mm(fp.GetPosition().y)
        ox, oy = rect[0] - px, rect[1] - py  # footprint origin -> courtyard min corner
        corner = grid.first_free(rect, accept=lambda r: _rect_inside(poly, r, margin))
        if corner is not None:
            fp.SetPosition(vec(round(corner[0] - ox, 4), round(corner[1] - oy, 4)))
            grid.occupy(_grow(_fp_rect(fp), spacing))
            placed.append({"reference": fp.GetReference(), "x": mm(fp.GetPosition().x),
                           "y": mm(fp.GetPosition().y), "inside_outline": True})
            continue
        if cursor_x + w > mm(bb.GetRight()):
            cursor_x = mm(bb.GetX())
            cursor_y += row_h + spacing
            row_h = 0.0
        fp.SetPosition(vec(round(cursor_x - ox, 4), round(cursor_y - oy, 4)))
        placed.append({"reference": fp.GetReference(), "x": mm(fp.GetPosition().x),
                       "y": mm(fp.GetPosition().y), "inside_outline": False})
        unplaced.append(fp.GetReference())
        cursor_x += w + spacing
        row_h = max(row_h, h)
    return {"placed": placed, "unplaced_inside_outline": unplaced}


def _row_layout(b, fps, a, poly):
    """Keep the initial row layout for parts that fit; put overflow below the outline."""
    bb = b.GetBoardEdgesBoundingBox()
    if a.get("area"):
        x0, y0, w, h = a["area"]
    else:
        if bb.GetWidth() <= 0:
            raise ValueError("No board outline; pass --area x,y,w,h")
        m = float(a.get("margin", 2.0))
        x0, y0 = mm(bb.GetX()) + m, mm(bb.GetY()) + m
        w, h = mm(bb.GetWidth()) - 2 * m, mm(bb.GetHeight()) - 2 * m
    gap = float(a.get("spacing", 1.0))

    def size(fp):
        layer = pcbnew.B_CrtYd if fp.IsFlipped() else pcbnew.F_CrtYd
        try:
            c = fp.GetCourtyard(layer).BBox()
            if c.GetWidth() > 0:
                return (mm(c.GetWidth()), mm(c.GetHeight()), mm(c.Centre().x - fp.GetPosition().x),
                        mm(c.Centre().y - fp.GetPosition().y))
        except Exception:
            pass
        fb = fp.GetBoundingBox(False)
        return (mm(fb.GetWidth()), mm(fb.GetHeight()), mm(fb.Centre().x - fp.GetPosition().x),
                mm(fb.Centre().y - fp.GetPosition().y))

    if a.get("order", "ref") != "ref":
        sizes = {fp.GetReference(): size(fp) for fp in fps}
        fps = sorted(fps, key=lambda f: (-sizes[f.GetReference()][0] * sizes[f.GetReference()][1], _refkey(f)))
    x, y, row_h = x0, y0, 0.0
    outline_bb = poly.BBox()
    outside_x = mm(outline_bb.GetX())
    outside_y = mm(outline_bb.GetBottom()) + float(a.get("margin", 2.0)) + 2.0
    outside_row_h = 0.0
    placed, unplaced = [], []
    for fp in fps:
        w_, h_, cx, cy = size(fp)
        if x + w_ > x0 + w and x > x0:
            x, y, row_h = x0, y + row_h + gap, 0.0
        fp.SetPosition(vec(round(x + w_ / 2 - cx, 3), round(y + h_ / 2 - cy, 3)))
        inside = _rect_inside(poly, _fp_rect(fp))
        if not inside:
            if outside_x + w_ > mm(outline_bb.GetRight()):
                outside_x = mm(outline_bb.GetX())
                outside_y += outside_row_h + gap
                outside_row_h = 0.0
            fp.SetPosition(vec(round(outside_x + w_ / 2 - cx, 3),
                               round(outside_y + h_ / 2 - cy, 3)))
            unplaced.append(fp.GetReference())
            outside_x += w_ + gap
            outside_row_h = max(outside_row_h, h_)
        placed.append({"reference": fp.GetReference(), "x": mm(fp.GetPosition().x),
                       "y": mm(fp.GetPosition().y), "inside_outline": inside})
        x += w_ + gap
        row_h = max(row_h, h_)
    return {"placed": placed, "unplaced_inside_outline": unplaced, "overflow": bool(unplaced)}


def _legal_spot(poly, rect, others, clearance):
    """No overlap with ``others`` [(fp, rect)] plus ``clearance``, and inside the outline."""
    grown = _grow(rect, clearance)
    for _, ob in others:
        if _overlaps(grown, ob):
            return False
    return _rect_inside(poly, rect)


def _placement_settings(a, command):
    values = (float(a.get("clearance", 0.2)), float(a.get("grid", 0.5)),
              float(a.get("max_distance", 10.0)))
    for flag, value, positive in zip(("--clearance", "--grid", "--max-dist"), values, (False, True, False)):
        if not math.isfinite(value) or (value <= 0 if positive else value < 0):
            requirement = "positive" if positive else "non-negative"
            raise ValueError(f"{flag} must be finite and {requirement}; see `kce {command} --help`.")
    return values


def _ring_deltas(grid, max_distance, include_origin=False):
    """(dx, dy, distance) candidates on ``grid`` around the origin, nearest first, deterministic."""
    n = int(math.floor(max_distance / grid + 1e-9))
    out = []
    for j in range(-n, n + 1):
        for i in range(-n, n + 1):
            dx, dy = round(i * grid, 6), round(j * grid, 6)
            d = math.hypot(dx, dy)
            if d <= max_distance + 1e-9 and (include_origin or d > 0):
                out.append((dx, dy, d))
    out.sort(key=lambda t: (round(t[2], 9), t[1], t[0]))
    return out


def a_legalize(b, a):
    """Visit footprints in reference order and accept their nearest legal grid position.

    All other footprints remain obstacles, including locked parts and parts that cannot move.
    Each search stays within max_distance of the original position; accepted moves cannot create
    a new overlap with an earlier or later footprint.
    """
    clearance, grid, max_distance = _placement_settings(a, "pcb legalize")
    poly = _board_outline(b)
    if poly is None:
        raise ValueError("No board outline; create one with `kce pcb outline rect`.")
    fps = sorted(b.GetFootprints(), key=lambda fp: (_refkey(fp), fp.GetReference()))
    rects = {fp.GetReference(): _fp_rect(fp) for fp in fps}
    selected = set(a["refs"]) if a.get("refs") else {fp.GetReference() for fp in fps}
    for ref in selected:
        get_fp(b, ref)
    candidates = _ring_deltas(grid, max_distance)
    moves = []
    for fp in fps:
        ref = fp.GetReference()
        rect = rects[ref]
        others = [(o, rects[o.GetReference()]) for o in fps if o.GetReference() != ref]
        if ref not in selected or fp.IsLocked() or _legal_spot(poly, rect, others, clearance):
            continue
        start = pt(fp.GetPosition())
        for dx, dy, _ in candidates:
            cand = (rect[0] + dx, rect[1] + dy, rect[2] + dx, rect[3] + dy)
            if not _legal_spot(poly, cand, others, clearance):
                continue
            fp.SetPosition(vec(start[0] + dx, start[1] + dy))
            rects[ref] = _fp_rect(fp)
            end = pt(fp.GetPosition())
            moves.append({"reference": ref, "from": start, "to": end,
                          "distance": round(math.hypot(end[0] - start[0], end[1] - start[1]), 4)})
            break
    unmoved = []
    for fp in fps:
        ref = fp.GetReference()
        others = [(o, rects[o.GetReference()]) for o in fps if o.GetReference() != ref]
        if not _legal_spot(poly, rects[ref], others, clearance):
            reason = "locked" if fp.IsLocked() else f"no legal position within {max_distance:g} mm"
            unmoved.append({"reference": ref, "reason": reason})
    if a.get("strict") and unmoved:
        raise PlacementBlocked("Placement legalization exhausted its search; enlarge --max-dist or restore a checkpoint", {
            "ref": None, "target": None, "radius": max_distance,
            "locked_blockers": [f['reference'] for f in unmoved if f['reason'] == 'locked'],
            "reason_code": "legalization_incomplete", "searched_area": None,
            "unplaced_refs": [f['reference'] for f in unmoved],
            "protected_refs": [fp.GetReference() for fp in fps if fp.IsLocked()], "unmoved": unmoved})
    return {"moves": moves, "moved": [dict(ref=m["reference"], **{k: v for k, v in m.items() if k != "reference"}) for m in moves],
            "unmoved": unmoved, "clearance": clearance, "grid": grid}


def _target_point(b, spec, target_pad=None):
    """Target position in mm for 'REF' (courtyard centre) or 'REF:PAD'/'REF.PAD' (pad position)."""
    ref, sep, pad = (spec.partition(":") if ":" in spec else spec.partition("."))
    fp = get_fp(b, ref)
    if sep:
        pads = [p for p in fp.Pads() if p.GetNumber() == pad]
        if not pads:
            pads = [p for p in fp.Pads() if p.GetPinFunction() in (pad, f"{pad}_{p.GetNumber()}")]
            if len({p.GetNumber() for p in pads}) > 1:
                raise ValueError(f"Pin name {pad!r} is ambiguous on {ref}; use a pad number. "
                                 f"See `kce pcb footprint {ref}`.")
        if not pads:
            pads = [get_pad(b, f"{ref}:{target_pad or pad}")]
        p = min(pads, key=lambda p: (p.GetPosition().x, p.GetPosition().y)).GetPosition()
        return (mm(p.x), mm(p.y)), f"{ref}:{pad}"
    r = _fp_rect(fp)
    return ((r[0] + r[2]) / 2, (r[1] + r[3]) / 2), ref


class PlacementBlocked(RuntimeError):
    def __init__(self, message, details):
        super().__init__(message)
        self.details = details


def a_place_near(b, a):
    """Put a footprint at the nearest legal spot to a target footprint or pad.

    Legal means what `legalize` means: no courtyard overlap (body bounding box when a footprint has
    no courtyard) with any other footprint plus ``clearance``, and fully inside the outline. The
    search walks a ``grid`` ring around the target, nearest first, and stops at ``max_distance``;
    the target is a footprint's courtyard centre or a pad's position.
    """
    ref, target = a["ref"], a["target"]
    fp = get_fp(b, ref)
    target_ref = target.split(":")[0].split(".")[0]
    if ref == target_ref:
        raise ValueError(f"{ref} cannot be placed near itself; pick another --to target")
    if fp.IsLocked():
        raise RuntimeError(f"{ref} is locked; unlock it with `kce pcb lock {ref} --unlock`")
    poly = _board_outline(b)
    if poly is None:
        raise ValueError("No board outline; create one with `kce pcb outline rect`.")
    clearance, grid, max_distance = _placement_settings(a, "pcb place near")
    want = a.get("side")
    if want in ("front", "back") and (want == "back") != fp.IsFlipped():
        fp.Flip(fp.GetPosition(), pcbnew.FLIP_DIRECTION_LEFT_RIGHT)
    (tx, ty), label = _target_point(b, target, a.get("target_pad"))
    start = pt(fp.GetPosition())
    current_rotation = fp.GetOrientationDegrees()
    rotation = a.get("rotation")
    angles = [current_rotation] if rotation is None else ([current_rotation, 0, 90, 180, 270] if rotation == "auto" else [float(rotation)])
    if any(not math.isfinite(angle) for angle in angles):
        raise ValueError("--rotation must be auto or a finite angle")
    shapes = []
    for angle in dict.fromkeys(angles):
        fp.SetOrientationDegrees(angle)
        rect = _fp_rect(fp)
        shapes.append((angle, rect[2] - rect[0], rect[3] - rect[1], rect[0] - start[0], rect[1] - start[1]))
    others = [(o, _fp_rect(o)) for o in b.GetFootprints() if o.GetReference() != ref]
    for dx, dy, d in _ring_deltas(grid, max_distance, include_origin=True):
        for angle, w, h, ox, oy in shapes:
            corner = (round(tx + dx - w / 2, 6), round(ty + dy - h / 2, 6))
            cand = (corner[0], corner[1], corner[0] + w, corner[1] + h)
            if not _legal_spot(poly, cand, others, clearance):
                continue
            fp.SetOrientationDegrees(angle)
            fp.SetPosition(vec(corner[0] - ox, corner[1] - oy))
            end = pt(fp.GetPosition())
            return {"reference": ref, "target": label, "target_at": [tx, ty], "from": start, "to": end,
                    "moved": round(math.hypot(end[0] - start[0], end[1] - start[1]), 4),
                    "distance": round(d, 4), "side": "back" if fp.IsFlipped() else "front", "rotation": angle,
                    "clearance": clearance, "grid": grid}
    raise PlacementBlocked(f"No legal spot for {ref} within {max_distance:g} mm of {label}: every candidate "
                     f"overlaps another footprint or leaves the board outline (clearance {clearance:g} mm). "
                     f"Try `kce pcb place near {ref} --to {label} --max-dist {max_distance * 2:g}` or free space "
                     f"with `kce pcb legalize`.",
        {"ref": ref, "target": label, "radius": max_distance,
         "locked_blockers": sorted(o.GetReference() for o, r in others
                                    if o.IsLocked() and _overlaps(_grow((tx, ty, tx, ty), max_distance + max(w, h)), r)),
         "reason_code": "search_exhausted", "searched_area": [tx - max_distance, ty - max_distance, tx + max_distance, ty + max_distance],
         "unplaced_refs": [ref], "protected_refs": sorted(o.GetReference() for o, _ in others if o.IsLocked())})



def _pad_positions(fp):
    return {p.GetNumber(): {"at": pt(p.GetPosition()), "net": p.GetNetname()}
            for p in fp.Pads() if p.GetNumber()}


def _moved_pads(before, fp):
    after = _pad_positions(fp)
    result = []
    for pad in sorted(before.keys() | after.keys()):
        old, new = before.get(pad), after.get(pad)
        if old is None or new is None or old["at"] != new["at"]:
            a, b = old["at"] if old else None, new["at"] if new else None
            result.append({"pad": pad, "net": old["net"] if old else new["net"], "from": a, "to": b,
                           "dist_mm": round(math.dist(a, b), 6) if a and b else None})
    return result

def a_update_from_netlist(b, a):
    """Netlist -> board sync (KiCad 'Update PCB from Schematic').

    ``components``: [{ref, value, footprint, path, lib_path, fp_name, fields, dnp, exclude_from_bom,
    exclude_from_board}], ``nets``: [{name, nodes:[{ref,pin}]}].
    """
    comps = a["components"]
    report = {"added": [], "removed": [], "updated": [], "replaced": [], "nets_assigned": 0,
              "unmatched_pads": [], "missing_footprints": [], "kept_extra": [], "adopted": [],
              "tracks_ending_on_old_pads": [], "unmatched_pins": []}
    original = {fp.m_Uuid.AsString(): fp_dict(fp) for fp in b.GetFootprints()}
    refs = [fp.GetReference() for fp in b.GetFootprints() if fp.GetReference()]
    if len(refs) != len(set(refs)):
        raise ValueError("PCB sync needs unique footprint references; resolve duplicates before pcb update")
    by_path = {fp.GetPath().AsString(): fp for fp in b.GetFootprints() if fp.GetPath().AsString() not in ("", "/")}
    by_ref = {fp.GetReference(): fp for fp in b.GetFootprints()}
    keep = set()
    origin = a.get("place_origin", [0, 0])
    px, py = origin
    for c in comps:
        if c.get("exclude_from_board"):
            continue
        fp = by_path.get(c["path"]) or (by_ref.get(c["ref"]) if a.get("match_by_ref", True) else None)
        if fp is not None and fp.GetPath().AsString() in ("", "/"):
            report["adopted"].append({"ref": c["ref"], "uuid": fp.m_Uuid.AsString(), "matched_by": "reference"})
        want_fpid = c.get("footprint") or ""
        if fp is not None and want_fpid and fp.GetFPIDAsString() != want_fpid and a.get("replace_footprints", True):
            if c.get("lib_path"):
                new = _load_fp(c["lib_path"], c["fp_name"])
                new.SetFPIDAsString(want_fpid)
                old_id = fp.GetFPIDAsString()
                old_pads = _pad_positions(fp)
                _exchange_fp(b, fp, new)
                moved = _moved_pads(old_pads, new)
                if moved and (a.get("lock_existing") or new.IsLocked()):
                    raise RuntimeError(f"Footprint {c['ref']} is locked; footprint replacement moves pads. Review --dry-run without --lock-existing first")
                report["replaced"].append({"ref": c["ref"], "old": old_id, "new": want_fpid, "pads_moved": moved})
                for pad in moved:
                    if pad["from"] is not None:
                        for track in b.GetTracks():
                            if pad["from"] in (pt(track.GetStart()), pt(track.GetEnd())):
                                report["tracks_ending_on_old_pads"].append({"ref": c["ref"], "pad": pad["pad"],
                                                                          "net": track.GetNetname(), "at": pad["from"],
                                                                          "uuid": track.m_Uuid.AsString()})
                fp = new
        if fp is None:
            if not want_fpid:
                report["missing_footprints"].append({"ref": c["ref"], "reason": "no footprint assigned in schematic"})
                continue
            if not c.get("lib_path"):
                report["missing_footprints"].append({"ref": c["ref"], "footprint": want_fpid,
                                                     "reason": c.get("lib_error", "library not found")})
                continue
            fp = _load_fp(c["lib_path"], c["fp_name"])
            fp.SetFPIDAsString(want_fpid)
            b.Add(fp)
            fp.SetPosition(vec(px, py))
            px += 5
            report["added"].append({"ref": c["ref"], "footprint": want_fpid})
        changed = []
        if fp.GetReference() != c["ref"]:
            changed.append(f"ref {fp.GetReference()}->{c['ref']}")
            fp.SetReference(c["ref"])
        if fp.GetValue() != c.get("value", ""):
            changed.append(f"value {fp.GetValue()}->{c.get('value', '')}")
            fp.SetValue(c.get("value", ""))
        if fp.GetPath().AsString() != c["path"]:
            fp.SetPath(pcbnew.KIID_PATH(c["path"]))
            changed.append("path")
        if c.get("sheetname") is not None and hasattr(fp, "SetSheetname"):
            fp.SetSheetname(c["sheetname"])
            fp.SetSheetfile(c.get("sheetfile", ""))
        for k, v in (c.get("fields") or {}).items():
            if k in ("Reference", "Value", "Footprint"):
                continue
            if fp.HasField(k):
                if fp.GetField(k).GetText() != v:
                    fp.GetField(k).SetText(v)
                    changed.append(f"field {k}")
            elif v:
                f = pcbnew.PCB_FIELD(fp, fp.GetNextFieldOrdinal(), k)
                f.SetText(v)
                f.SetVisible(False)
                f.SetLayer(pcbnew.F_Fab)
                fp.Add(f)
                changed.append(f"field {k}")
        if hasattr(fp, "SetDNP") and bool(c.get("dnp")) != bool(fp.IsDNP()):
            fp.SetDNP(bool(c.get("dnp")))
            changed.append("dnp")
        if hasattr(fp, "SetExcludedFromBOM") and bool(c.get("exclude_from_bom")) != bool(fp.IsExcludedFromBOM()):
            fp.SetExcludedFromBOM(bool(c.get("exclude_from_bom")))
            changed.append("exclude_from_bom")
        if _sync_fp_variants(fp, c.get("variants") or {}, [v["name"] for v in a.get("variants") or []]):
            changed.append("variants")
        if changed and c["ref"] not in [x["ref"] for x in report["added"]]:
            report["updated"].append({"ref": c["ref"], "changes": changed})
        keep.add(id(fp))
        keep.add(fp.m_Uuid.AsString())
    # KiCad 10 design variants: names on the board, overrides per footprint (above)
    for v in a.get("variants") or []:
        if hasattr(b, "HasVariant") and not b.HasVariant(v["name"]):
            b.AddVariant(v["name"])
            report.setdefault("variants_added", []).append(v["name"])
        if v.get("description") and hasattr(b, "SetVariantDescription"):
            b.SetVariantDescription(v["name"], v["description"])
    # nets
    pin_net = {}
    for n in a.get("nets", []):
        for nd in n["nodes"]:
            pin_net[(nd["ref"], nd["pin"])] = n["name"]
    for fp in b.GetFootprints():
        ref = fp.GetReference()
        for pad in fp.Pads():
            if not pad.GetNumber():
                continue
            key = (ref, pad.GetNumber())
            if key in pin_net:
                name = pin_net[key]
                if pad.GetNetname() != name:
                    pad.SetNet(get_net(b, name, create=True))
                    report["nets_assigned"] += 1
            elif fp.m_Uuid.AsString() in keep and pad.GetAttribute() != pcbnew.PAD_ATTRIB_NPTH:
                if pad.GetNetCode() > 0 and a.get("clear_unconnected_pads", True):
                    pad.SetNet(b.FindNet(0))
                report["unmatched_pads"].append(f"{ref}:{pad.GetNumber()}")
    # removal of footprints no longer in schematic
    for fp in list(b.GetFootprints()):
        if fp.m_Uuid.AsString() in keep:
            continue
        if not fp.GetPath().AsString() or fp.GetPath().AsString() == "/":
            report["kept_extra"].append(fp.GetReference())  # board-only items (mounting holes, logos)
            continue
        if a.get("delete_extra", True) and not fp.IsLocked():
            report["removed"].append(fp.GetReference())
            _remove(b, fp)
        else:
            report["kept_extra"].append(fp.GetReference())
    # nets with tracks but no pads anymore are left for DRC to report
    b.BuildConnectivity()
    report["unrouted"] = b.GetConnectivity().GetUnconnectedCount(False)
    pad_keys = {(fp.GetReference(), p.GetNumber()) for fp in b.GetFootprints() for p in fp.Pads()}
    report["unmatched_pins"] = sorted(f"{ref}:{pin}" for ref, pin in pin_net if (ref, pin) not in pad_keys)
    current = {fp.m_Uuid.AsString(): fp_dict(fp) for fp in b.GetFootprints()}
    changed = [current[key]["reference"] for key in sorted(original.keys() & current.keys())
               if any(original[key][k] != current[key][k] for k in ("x", "y", "rotation", "side"))]
    if changed and a.get("lock_existing"):
        raise RuntimeError(f"Footprint {changed[0]} is locked; --lock-existing forbids moving an existing part")
    report["existing_moved"] = len(changed)
    report["existing_unchanged"] = len(original.keys() & current.keys()) - len(changed)
    report["would_remove"] = list(report["removed"])
    report["unrouted_connections"] = _ratsnest(b)
    report["noise"] = bool(report["updated"]) and all(x["changes"] == ["path"] for x in report["updated"]) and not any(
        report[k] for k in ("added", "removed", "replaced", "nets_assigned"))
    if report["adopted"]:
        report["hint"] = "Imported board: run with --dry-run --keep-extra first; inspect adopted, unmatched_pins, pads_moved and tracks_ending_on_old_pads; then check drc"
    return report


def _sync_fp_variants(fp, variants, known=()):
    """Make a footprint's variant overrides match the schematic's (DNP, BOM, position files, fields).

    SWIG does not wrap the variant maps (GetVariants/GetVariantNames), so stale overrides are found
    by probing the variant names the project knows (``known``)."""
    if not hasattr(fp, "AddVariant"):
        return False
    changed = False
    current = {n for n in set(known) | set(variants) if fp.HasVariant(n)}
    for name in current - set(variants):
        fp.DeleteVariant(name)
        changed = True
    for name, d in variants.items():
        v = fp.GetVariant(name) or fp.AddVariant(name)
        want = {"SetDNP": bool(d.get("dnp")), "SetExcludedFromBOM": bool(d.get("exclude_from_bom")),
                "SetExcludedFromPosFiles": bool(d.get("exclude_from_pos_files"))}
        have = {"SetDNP": v.GetDNP(), "SetExcludedFromBOM": v.GetExcludedFromBOM(),
                "SetExcludedFromPosFiles": v.GetExcludedFromPosFiles()}
        for setter, val in want.items():
            if have[setter] != val:
                getattr(v, setter)(val)
                changed = True
        for fname, fval in (d.get("fields") or {}).items():
            if not v.HasFieldValue(fname) or v.GetFieldValue(fname) != fval:
                v.SetFieldValue(fname, fval)
                changed = True
        changed = changed or name not in current
    return changed


def a_script(b, a):
    """Escape hatch: run arbitrary Python with ``board`` and ``pcbnew`` in scope."""
    env = {"board": b, "pcbnew": pcbnew, "mm": mm, "vec": vec, "FromMM": MM, "result": None,
           "params": a.get("params", {})}
    exec(compile(a["code"], a.get("filename", "<kce-script>"), "exec"), env)
    r = env.get("result")
    try:
        json.dumps(r)
    except TypeError:
        r = repr(r)
    return {"result": r, "modified": bool(env.get("modified", True))}


# --- built-in maze router ----------------------------------------------------


def a_autoroute_builtin(b, a):
    """Simple 2-layer grid (Lee/A*) router for small boards.

    KiCad 10 ships no autorouter; this router only *proposes* geometry —
    tracks/vias are real pcbnew objects and the result must pass KiCad DRC.
    """
    if a.get("grid") == "auto":
        started = time.monotonic()
        routed, tried = [], []
        for grid in (0.25, 0.15, 0.1, 0.05):
            tried.append(grid)
            result = a_autoroute_builtin(b, dict(a, grid=grid))
            routed.extend(result["routed"])
            if not result["failed"]:
                break
        result.update(routed=routed, grid_tried=tried, seconds=round(time.monotonic() - started, 3))
        return result
    grid = float(a.get("grid", 0.25))
    if not math.isfinite(grid) or grid <= 0:
        raise ValueError("--grid must be auto or a finite positive number")
    width = float(a.get("width", 0.25))
    clearance = float(a.get("clearance", 0.2))
    via_d = float(a.get("via_diameter", 0.6))
    via_drill = float(a.get("via_drill", 0.3))
    layers = a.get("layers") or ["F.Cu", "B.Cu"]
    lids = [layer_id(b, l) for l in layers]
    via_cost = float(a.get("via_cost", 8))
    edge_clear = float(a.get("edge_clearance", 0.5))
    bb = b.GetBoardEdgesBoundingBox()
    if bb.GetWidth() <= 0:
        raise ValueError("Board has no outline (Edge.Cuts); create one with `kce pcb outline rect`")
    x0, y0 = mm(bb.GetX()), mm(bb.GetY())
    xmax, ymax = x0 + mm(bb.GetWidth()), y0 + mm(bb.GetHeight())
    if a.get("area"):
        area = a["area"]
        if len(area) != 4 or any(not math.isfinite(v) for v in area) or area[2] <= 0 or area[3] <= 0:
            raise ValueError("--area needs finite x,y,w,h with positive width and height")
        x0, y0, xmax, ymax = max(x0, area[0]), max(y0, area[1]), min(xmax, area[0] + area[2]), min(ymax, area[1] + area[3])
        if xmax <= x0 or ymax <= y0:
            raise ValueError("--area does not intersect the board")
    nx, ny = int((xmax - x0) / grid) + 1, int((ymax - y0) / grid) + 1
    if nx * ny * len(lids) > 6_000_000:
        raise ValueError(f"Grid too large ({nx}x{ny}); increase --grid")
    L = len(lids)
    FREE = -1
    BLOCK = -2
    occ = [[FREE] * (nx * ny) for _ in range(L)]

    def cell(x, y):
        return int(round((x - x0) / grid)), int(round((y - y0) / grid))

    def mark_rect(li, xa, ya, xb, yb, val):
        i0, j0 = cell(xa, ya)
        i1, j1 = cell(xb, yb)
        for j in range(max(0, min(j0, j1)), min(ny, max(j0, j1) + 1)):
            row = occ[li]
            base = j * nx
            for i in range(max(0, min(i0, i1)), min(nx, max(i0, i1) + 1)):
                cur = row[base + i]
                if cur == FREE or val == BLOCK:
                    row[base + i] = val
                elif cur != val:
                    row[base + i] = BLOCK

    halo = width / 2 + clearance
    # board edge keepout
    for li in range(L):
        mark_rect(li, x0, y0, x0 + mm(bb.GetWidth()), y0 + edge_clear, BLOCK)
        mark_rect(li, x0, y0 + mm(bb.GetHeight()) - edge_clear, x0 + mm(bb.GetWidth()), y0 + mm(bb.GetHeight()), BLOCK)
        mark_rect(li, x0, y0, x0 + edge_clear, y0 + mm(bb.GetHeight()), BLOCK)
        mark_rect(li, x0 + mm(bb.GetWidth()) - edge_clear, y0, x0 + mm(bb.GetWidth()), y0 + mm(bb.GetHeight()), BLOCK)
    # pads
    for fp in b.GetFootprints():
        for pad in fp.Pads():
            pb = pad.GetBoundingBox()
            for li, lid in enumerate(lids):
                if pad.IsOnLayer(lid) or pad.GetAttribute() in (pcbnew.PAD_ATTRIB_PTH, pcbnew.PAD_ATTRIB_NPTH):
                    code = pad.GetNetCode() if pad.GetNetCode() > 0 else BLOCK
                    mark_rect(li, mm(pb.GetX()) - halo, mm(pb.GetY()) - halo, mm(pb.GetRight()) + halo,
                              mm(pb.GetBottom()) + halo, code)
    # existing tracks / vias
    for t in b.GetTracks():
        code = t.GetNetCode() if t.GetNetCode() > 0 else BLOCK
        tb = t.GetBoundingBox()
        for li, lid in enumerate(lids):
            if t.GetClass() == "PCB_VIA" or t.GetLayer() == lid:
                mark_rect(li, mm(tb.GetX()) - halo, mm(tb.GetY()) - halo, mm(tb.GetRight()) + halo,
                          mm(tb.GetBottom()) + halo, code)
    # keepout zones
    for z in b.Zones():
        if z.GetIsRuleArea() and z.GetDoNotAllowTracks():
            zb = z.GetBoundingBox()
            for li, lid in enumerate(lids):
                if z.IsOnLayer(lid):
                    mark_rect(li, mm(zb.GetX()), mm(zb.GetY()), mm(zb.GetRight()), mm(zb.GetBottom()), BLOCK)

    via_halo_cells = int(math.ceil((via_d / 2 + clearance) / grid))
    selected = set(a.get("refs") or [])
    for ref in selected:
        get_fp(b, ref)
    conns = _ratsnest(b, a.get("net"))
    if selected:
        conns = [c for c in conns if c["from"].split(":")[0] in selected or c["to"].split(":")[0] in selected]
    if a.get("skip_nets"):
        conns = [c for c in conns if c["net"] not in a["skip_nets"]]
    routed, failed = [], []
    dirs = [(1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1)]

    def passable(li, i, j, code):
        if i < 0 or j < 0 or i >= nx or j >= ny:
            return False
        v = occ[li][j * nx + i]
        return v == FREE or v == code

    def via_ok(i, j, code):
        for li in range(L):
            for dj in range(-via_halo_cells, via_halo_cells + 1):
                for di in range(-via_halo_cells, via_halo_cells + 1):
                    if not passable(li, i + di, j + dj, code):
                        return False
        return True

    max_nodes = int(a.get("max_nodes", 400000))
    for c in conns:
        pa, pb_ = get_pad(b, c["from"]), get_pad(b, c["to"])
        code = pa.GetNetCode()
        si, sj = cell(*c["from_xy"])
        ti, tj = cell(*c["to_xy"])
        if not (0 <= si < nx and 0 <= sj < ny and 0 <= ti < nx and 0 <= tj < ny):
            failed.append(dict(c, reason="endpoint outside routing area"))
            continue
        starts = [li for li, lid in enumerate(lids) if pa.IsOnLayer(lid)]
        goals = {(li, ti, tj) for li, lid in enumerate(lids) if pb_.IsOnLayer(lid)}
        if not starts or not goals:
            failed.append(dict(c, reason="pads not on routing layers"))
            continue
        openq = []
        came = {}
        g = {}
        for li in starts:
            s = (li, si, sj)
            g[s] = 0.0
            heapq.heappush(openq, (0.0, s))
            came[s] = None
        found = None
        expanded = 0
        while openq and expanded < max_nodes:
            f, cur = heapq.heappop(openq)
            if cur in goals:
                found = cur
                break
            expanded += 1
            li, i, j = cur
            base = g[cur]
            for di, dj in dirs:
                ni, nj = i + di, j + dj
                if not passable(li, ni, nj, code):
                    continue
                if di and dj and not (passable(li, i + di, j, code) and passable(li, i, j + dj, code)):
                    continue
                cost = base + (1.414 if di and dj else 1.0)
                # discourage direction changes a little (fewer segments)
                prev = came.get(cur)
                if prev is not None and prev[0] == li and (i - prev[1], j - prev[2]) != (di, dj):
                    cost += 0.3
                nxt = (li, ni, nj)
                if cost < g.get(nxt, 1e18):
                    g[nxt] = cost
                    came[nxt] = cur
                    h = math.hypot(ti - ni, tj - nj)
                    heapq.heappush(openq, (cost + h, nxt))
            if L > 1 and via_ok(i, j, code):
                for lj in range(L):
                    if lj == li:
                        continue
                    nxt = (lj, i, j)
                    cost = base + via_cost
                    if cost < g.get(nxt, 1e18):
                        g[nxt] = cost
                        came[nxt] = cur
                        heapq.heappush(openq, (cost + math.hypot(ti - i, tj - j), nxt))
        if found is None:
            failed.append(dict(c, reason="no path" if expanded < max_nodes else "search limit"))
            continue
        path = []
        n = found
        while n is not None:
            path.append(n)
            n = came[n]
        path.reverse()
        # emit segments: compress collinear runs, vias on layer change
        net = pa.GetNet()
        segs = 0
        vias = 0

        def xy(nd):
            return (round(x0 + nd[1] * grid, 4), round(y0 + nd[2] * grid, 4))

        run = [path[0]]
        pieces = []
        for nd in path[1:]:
            if nd[0] != run[-1][0]:
                pieces.append(run)
                pieces.append(("via", nd))
                run = [nd]
            else:
                run.append(nd)
        pieces.append(run)
        for k, pc in enumerate(pieces):
            if isinstance(pc, tuple):
                v = pcbnew.PCB_VIA(b)
                v.SetPosition(vec(*xy(pc[1])))
                v.SetViaType(pcbnew.VIATYPE_THROUGH)
                v.SetLayerPair(lids[0], lids[-1])
                v.SetWidth(MM(via_d))
                v.SetDrill(MM(via_drill))
                v.SetNet(net)
                b.Add(v)
                vias += 1
                continue
            pts = [xy(pc[0])]
            for a_, b_, c_ in zip(pc, pc[1:], pc[2:]):
                if (b_[1] - a_[1], b_[2] - a_[2]) != (c_[1] - b_[1], c_[2] - b_[2]):
                    pts.append(xy(b_))
            if len(pc) > 1:
                pts.append(xy(pc[-1]))
            # snap endpoints exactly onto pad centres
            if k == 0:
                pts[0] = tuple(c["from_xy"])
            if k == len(pieces) - 1:
                pts[-1] = tuple(c["to_xy"])
            for s, e in zip(pts, pts[1:]):
                if s == e:
                    continue
                t = pcbnew.PCB_TRACK(b)
                t.SetStart(vec(*s))
                t.SetEnd(vec(*e))
                t.SetWidth(MM(width))
                t.SetLayer(lids[pc[0][0]])
                t.SetNet(net)
                b.Add(t)
                segs += 1
        # occupy the routed cells for later nets (with clearance halo)
        hc = int(math.ceil(halo / grid))
        for li, i, j in path:
            for dj in range(-hc, hc + 1):
                for di in range(-hc, hc + 1):
                    ii, jj = i + di, j + dj
                    if 0 <= ii < nx and 0 <= jj < ny:
                        cur = occ[li][jj * nx + ii]
                        if cur == FREE:
                            occ[li][jj * nx + ii] = code
        routed.append({"net": c["net"], "from": c["from"], "to": c["to"], "segments": segs, "vias": vias})
    b.BuildConnectivity()
    for failure in failed:
        failure["hint"] = "retry with --grid 0.1, or --grid auto; check DRC after routing"
    return {"routed": routed, "failed": failed, "connections": len(conns),
            "unrouted_after": b.GetConnectivity().GetUnconnectedCount(False), "grid": grid}


# ---------------------------------------------------------------------------
# Advanced board features: geometry helpers
# ---------------------------------------------------------------------------

# A one-zone board carrying TEARDROP_TYPE::TD_VIAPAD: the enum class is not exported by SWIG, so
# the value is taken from a zone KiCad's own parser created (pcbnew/teardrop/teardrop_types.h)
_TD_TEMPLATE = """(kicad_pcb (version 20260206) (generator "kce")
  (zone (layer "F.Cu") (uuid "00000000-0000-0000-0000-000000000001") (hatch edge 0.5)
    (attr (teardrop (type padvia))) (connect_pads yes (clearance 0)) (min_thickness 0.0254)
    (fill (thermal_gap 0.5) (thermal_bridge_width 0.5)) (polygon (pts (xy 0 0) (xy 1 0) (xy 1 1)))))
"""
_td_cache = {}


def _td_viapad():
    if "type" not in _td_cache:
        import tempfile
        d = tempfile.mkdtemp(prefix="kce-td-")
        path = os.path.join(d, "td.kicad_pcb")
        with open(path, "w", encoding="utf-8") as f:
            f.write(_TD_TEMPLATE)
        board = pcbnew.LoadBoard(path)
        import shutil
        shutil.rmtree(d, ignore_errors=True)
        _td_cache["board"] = board  # keep the owner alive
        _td_cache["type"] = board.Zones()[0].GetTeardropAreaType()
    return _td_cache["type"]

# first priority of teardrop zones (MAGIC_TEARDROP_ZONE_ID in pcbnew/teardrop/teardrop.cpp)
TEARDROP_PRIORITY = 30000


def _unit(dx, dy):
    n = math.hypot(dx, dy)
    return (dx / n, dy / n) if n > 1e-9 else (0.0, 0.0)


def _convex_hull(points):
    """Andrew's monotone chain; points as (x, y) tuples, counter-clockwise result."""
    pts = sorted(set((round(x), round(y)) for x, y in points))
    if len(pts) < 3:
        return pts

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower, upper = [], []
    for q in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], q) <= 0:
            lower.pop()
        lower.append(q)
    for q in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], q) <= 0:
            upper.pop()
        upper.append(q)
    return lower[:-1] + upper[:-1]


def _clip_halfplane(poly, nx, ny, c):
    """Part of a polygon with ``nx*x + ny*y <= c`` (one Sutherland-Hodgman pass)."""
    out = []
    for i, cur in enumerate(poly):
        prev = poly[i - 1]
        dc, dp = nx * cur[0] + ny * cur[1] - c, nx * prev[0] + ny * prev[1] - c
        if dc <= 0:
            if dp > 0:
                t = dp / (dp - dc)
                out.append((prev[0] + t * (cur[0] - prev[0]), prev[1] + t * (cur[1] - prev[1])))
            out.append(cur)
        elif dp <= 0:
            t = dp / (dp - dc)
            out.append((prev[0] + t * (cur[0] - prev[0]), prev[1] + t * (cur[1] - prev[1])))
    return out


def _poly_points(spoly):
    """Outline 0 of a SHAPE_POLY_SET as [(x, y)] in IU."""
    if spoly.OutlineCount() == 0:
        return []
    ch = spoly.Outline(0)
    return [(ch.CPoint(i).x, ch.CPoint(i).y) for i in range(ch.PointCount())]


def _is_via(item):
    return item.GetClass() == "PCB_VIA"


def _copper_layers(item):
    return [l for l in item.GetLayerSet().Seq() if pcbnew.IsCopperLayer(l)]


def _item_width(item, layer):
    """Teardrop reference size (TEARDROP_MANAGER::GetWidth): via diameter, smaller pad side."""
    if _is_via(item):
        try:
            return item.GetWidth(layer)
        except TypeError:
            return item.GetWidth()
    s = item.GetSize(layer)
    return min(s.x, s.y)


def _is_round(item, layer):
    if _is_via(item):
        return True
    s = item.GetSize(layer)
    shape = item.GetShape(layer)
    return shape == pcbnew.PAD_SHAPE_CIRCLE or (shape == pcbnew.PAD_SHAPE_OVAL and s.x == s.y)


def _item_outline(item, layer):
    """Copper outline of a pad/via on ``layer`` as [(x, y)] in IU."""
    c = item.GetPosition()
    if _is_round(item, layer):
        r = _item_width(item, layer) / 2
        return [(c.x + r * math.cos(k * math.pi / 16), c.y + r * math.sin(k * math.pi / 16)) for k in range(32)]
    poly = pcbnew.SHAPE_POLY_SET()
    item.TransformShapeToPolygon(poly, layer, 0, MM(0.005), pcbnew.ERROR_INSIDE)
    return _poly_points(poly)


def _inside(item, x, y):
    return item.HitTest(pcbnew.VECTOR2I(int(round(x)), int(round(y))), 0)


def _board_outline(b):
    poly = pcbnew.SHAPE_POLY_SET()
    try:
        ok = b.GetBoardPolygonOutlines(poly, True)
    except TypeError:
        ok = b.GetBoardPolygonOutlines(poly)
    return poly if ok and poly.OutlineCount() else None


def _circle_inside(poly, x, y, r):
    """Is a disc fully inside ``poly`` (center + 8 rim points)?"""
    for k in range(9):
        px, py = (x, y) if k == 8 else (x + r * math.cos(k * math.pi / 4), y + r * math.sin(k * math.pi / 4))
        if not poly.Contains(pcbnew.VECTOR2I(int(px), int(py))):
            return False
    return True


class _Obstacles:
    """Copper items (tracks, vias, pads incl. NPTH holes) for clearance probes, bbox-prefiltered."""

    def __init__(self, b, include_zones=True, skip=None):
        skip = skip or set()
        self.items = []
        for t in b.GetTracks():
            if t.m_Uuid.AsString() not in skip:
                self.items.append(t)
        for fp in b.GetFootprints():
            for p in fp.Pads():
                self.items.append(p)
        self.zones = [z for z in b.Zones() if include_zones and not z.GetIsRuleArea()]
        self.keepouts = [z for z in b.Zones() if z.GetIsRuleArea()]
        # barcodes need a clear margin around them (KiCad 10 DRC "barcode visual separation", 1 mm)
        self.barcodes = []
        for d in b.GetDrawings():
            if d.GetClass() in ("BARCODE", "PCB_BARCODE"):
                bb = d.GetBoundingBox()
                m = MM(1.0)
                self.barcodes.append((bb.GetX() - m, bb.GetY() - m, bb.GetRight() + m, bb.GetBottom() + m))
        self.boxes = []
        for it in self.items:
            bb = it.GetBoundingBox()
            self.boxes.append((bb.GetX(), bb.GetY(), bb.GetRight(), bb.GetBottom()))

    def add(self, item):
        self.items.append(item)
        bb = item.GetBoundingBox()
        self.boxes.append((bb.GetX(), bb.GetY(), bb.GetRight(), bb.GetBottom()))

    def hit(self, x, y, r, layers, ok_net=None, any_net_pads=False, via_like=True, pad_margin=0):
        """True if a disc (x, y, r) on ``layers`` touches copper of another net (or a keepout).

        ``any_net_pads`` also keeps the disc off same-net pads, by ``pad_margin`` more: the room a
        pour's thermal relief (gap + spokes) needs around them."""
        pos = pcbnew.VECTOR2I(int(x), int(y))
        lay = set(layers)
        for x0, y0, x1, y1 in self.barcodes:
            if x0 - r <= x <= x1 + r and y0 - r <= y <= y1 + r:
                return True
        for (x0, y0, x1, y1), it in zip(self.boxes, self.items):
            is_pad = it.GetClass() == "PAD"
            same = ok_net is not None and it.GetNetCode() == ok_net
            probe = r + (pad_margin if same and is_pad and any_net_pads else 0)
            if x < x0 - probe or x > x1 + probe or y < y0 - probe or y > y1 + probe:
                continue
            if same and not (is_pad and any_net_pads) and not (_is_via(it) and via_like):
                continue
            if is_pad and it.GetAttribute() == pcbnew.PAD_ATTRIB_NPTH:
                pass  # holes collide on every layer
            elif not lay.intersection(_copper_layers(it)):
                continue
            if it.HitTest(pos, int(probe)):
                return True
        for z in self.zones:
            if ok_net is not None and z.GetNetCode() == ok_net:
                continue
            for l in lay:
                if z.IsOnLayer(l) and z.HitTestFilledArea(l, pos, int(r)):
                    return True
        for z in self.keepouts:
            if via_like and z.GetDoNotAllowVias() and lay.intersection(z.GetLayerSet().Seq()) \
                    and z.Outline().Contains(pos):
                return True
            if not via_like and z.GetDoNotAllowTracks() and lay.intersection(z.GetLayerSet().Seq()) \
                    and z.Outline().Contains(pos):
                return True
        return False


def _new_via(b, x, y, net, diameter, drill, free=True):
    v = pcbnew.PCB_VIA(b)
    v.SetPosition(pcbnew.VECTOR2I(int(x), int(y)))
    v.SetViaType(pcbnew.VIATYPE_THROUGH)
    v.SetLayerPair(pcbnew.F_Cu, pcbnew.B_Cu)
    v.SetWidth(int(diameter))
    v.SetDrill(int(drill))
    v.SetNet(net)
    if free and hasattr(v, "SetIsFree"):
        v.SetIsFree(True)  # "free" vias keep their net when the board is updated from the schematic
    b.Add(v)
    return v


def _thermal_room(b, netcode):
    """Gap + spoke width of the widest thermal relief among the net's pours (0 without pours)."""
    return max([z.GetThermalReliefGap() + z.GetThermalReliefSpokeWidth() for z in b.Zones()
                if z.GetNetCode() == netcode and not z.GetIsRuleArea() and not z.IsTeardropArea()] or [0])


def _clearance(b, a):
    if a.get("clearance") is not None:
        return MM(a["clearance"])
    return max(b.GetDesignSettings().m_MinClearance, MM(0.2))


# ---------------------------------------------------------------------------
# Teardrops
# ---------------------------------------------------------------------------

TD_DEFAULTS = {"length_ratio": 0.5, "max_length": 1.0, "width_ratio": 1.0, "max_width": 2.0,
               "filter_ratio": 0.9, "curved": False, "two_tracks": True, "pads_in_zones": False}


def _td_params(a):
    prm = dict(TD_DEFAULTS)
    prm.update({k: v for k, v in (a.get("params") or {}).items() if v is not None})
    return prm


def _set_td_settings(item, prm, enabled):
    """Per-pad/via teardrop settings: what KiCad's GUI uses when it regenerates teardrops."""
    item.SetTeardropsEnabled(enabled)
    item.SetTeardropBestLengthRatio(float(prm["length_ratio"]))
    item.SetTeardropMaxLength(MM(prm["max_length"]))
    item.SetTeardropBestWidthRatio(float(prm["width_ratio"]))
    item.SetTeardropMaxWidth(MM(prm["max_width"]))
    item.SetTeardropMaxTrackWidth(float(prm["filter_ratio"]))
    item.SetTeardropCurved(bool(prm["curved"]))
    item.SetTeardropAllowSpanTwoTracks(bool(prm["two_tracks"]))
    item.SetTeardropPreferZoneConnections(not prm["pads_in_zones"])


def _teardrop_polygon(item, track, layer, prm):
    """Straight teardrop outline for a track ending inside a pad/via (port of KiCad's
    TEARDROP_MANAGER::computeAnchorPoints / computeTeardropPolygon, without curved edges)."""
    s, e = track.GetStart(), track.GetEnd()
    s_in, e_in = _inside(item, s.x, s.y), _inside(item, e.x, e.y)
    if s_in == e_in:
        return None, "track does not end inside the pad/via"
    pin, pout = (s, e) if s_in else (e, s)
    size, w = _item_width(item, layer), track.GetWidth()
    if w >= size * prm["filter_ratio"]:
        return None, "track too wide for the pad/via (filter ratio)"
    seg = math.hypot(pout.x - pin.x, pout.y - pin.y)
    dx, dy = _unit(pout.x - pin.x, pout.y - pin.y)
    lo, hi = 0.0, seg  # where the track leaves the copper: bisection along the centreline
    for _ in range(40):
        mid = (lo + hi) / 2
        if _inside(item, pin.x + dx * mid, pin.y + dy * mid):
            lo = mid
        else:
            hi = mid
    ix, iy = pin.x + dx * hi, pin.y + dy * hi
    target = size * prm["length_ratio"]
    if prm["max_length"] > 0:
        target = min(target, MM(prm["max_length"]))
    length = min(target, seg - hi)
    if length <= MM(0.01):
        return None, "track ends at the pad/via edge"
    px, py = ix + dx * length, iy + dy * length
    nx, ny = -dy, dx
    hw = w / 2
    height = size * prm["width_ratio"]
    if prm["max_width"] > 0:
        height = min(height, MM(prm["max_width"]))
    height = max(height, w)
    shape = _item_outline(item, layer)
    k = nx * px + ny * py
    shape = _clip_halfplane(shape, nx, ny, k + height / 2)
    shape = _clip_halfplane(shape, -nx, -ny, -k + height / 2)
    if not shape:
        return None, "no anchor points on the pad/via"
    hull = _convex_hull([(px + nx * hw, py + ny * hw), (px - nx * hw, py - ny * hw)] + shape)
    return (hull, None) if len(hull) >= 3 else (None, "degenerate teardrop")


def _teardrop_zone(b, pts, track, priority):
    z = pcbnew.ZONE(b)
    z.SetTeardropAreaType(_td_viapad())
    z.SetLayer(track.GetLayer())
    z.SetNetCode(track.GetNetCode())
    z.SetLocalClearance(0)
    z.SetMinThickness(MM(0.0254))
    z.SetPadConnection(pcbnew.ZONE_CONNECTION_FULL)
    z.SetIslandRemovalMode(pcbnew.ISLAND_REMOVAL_MODE_NEVER)
    z.SetBorderDisplayStyle(pcbnew.ZONE_BORDER_DISPLAY_STYLE_INVISIBLE_BORDER, 0, False)
    z.SetAssignedPriority(priority)
    o = z.Outline()
    o.NewOutline()
    for x, y in pts:
        o.Append(int(round(x)), int(round(y)))
    b.Add(z)
    return z


def _teardrop_zones(b, net=None):
    return [z for z in b.Zones() if z.IsTeardropArea() and (net is None or z.GetNetname() == net)]


def a_teardrops(b, a):
    """Add (regenerate) or remove teardrops on vias and pads (KiCad's Edit > Teardrops)."""
    nets = set(a.get("nets") or [])
    removed = 0
    for z in _teardrop_zones(b):
        if not nets or z.GetNetname() in nets:
            _remove(b, z)
            removed += 1
    targets = set(a.get("targets") or ["vias", "pth", "smd"])
    prm = _td_params(a)
    items = []
    if "vias" in targets:
        items += [t for t in b.GetTracks() if _is_via(t)]
    for fp in b.GetFootprints():
        for p in fp.Pads():
            attr = p.GetAttribute()
            if (attr == pcbnew.PAD_ATTRIB_PTH and "pth" in targets) or \
                    (attr in (pcbnew.PAD_ATTRIB_SMD, pcbnew.PAD_ATTRIB_CONN) and "smd" in targets):
                items.append(p)
    items = [i for i in items if i.GetNetCode() > 0 and (not nets or i.GetNetname() in nets)]
    if a.get("mode") == "remove":
        if not a.get("keep_settings"):
            for it in items:
                it.SetTeardropsEnabled(False)
        return {"removed": removed, "disabled_on": 0 if a.get("keep_settings") else len(items)}
    if a.get("round_only"):
        items = [i for i in items if all(_is_round(i, l) for l in _copper_layers(i))]
    tracks_by_net = {}
    for t in b.GetTracks():
        if t.GetClass() == "PCB_TRACK":
            tracks_by_net.setdefault(t.GetNetCode(), []).append(t)
    pour = [z for z in b.Zones() if not z.GetIsRuleArea() and not z.IsTeardropArea()]
    added, skipped, prio = [], [], TEARDROP_PRIORITY
    for it in items:
        _set_td_settings(it, prm, True)
        owner = f"{it.GetParentFootprint().GetReference()}:{it.GetNumber()}" if not _is_via(it) else \
            "via@" + ",".join(str(v) for v in pt(it.GetPosition()))
        for t in tracks_by_net.get(it.GetNetCode(), []):
            lay = t.GetLayer()
            if not it.IsOnLayer(lay):
                continue
            if not prm["pads_in_zones"] and any(z.GetNetCode() == it.GetNetCode() and z.IsOnLayer(lay)
                                                and z.HitTestFilledArea(lay, it.GetPosition(), 0) for z in pour):
                continue  # KiCad's default: no teardrops for pads/vias inside a same-net pour
            poly, why = _teardrop_polygon(it, t, lay, prm)
            if poly is None:
                if why != "track does not end inside the pad/via":
                    skipped.append({"item": owner, "net": it.GetNetname(), "reason": why})
                continue
            _teardrop_zone(b, poly, t, prio)
            prio += 1
            added.append({"item": owner, "net": it.GetNetname(), "layer": b.GetLayerName(lay)})
    fill = a_fill_zones(b, {}) if added else None  # the filler clips teardrops against other nets
    return {"added": len(added), "removed": removed, "teardrops": added[:200], "skipped": skipped[:100],
            "settings": prm, "filled": bool(fill and fill["ok"])}


# ---------------------------------------------------------------------------
# Via stitching / via fence
# ---------------------------------------------------------------------------


def a_via_stitch(b, a):
    """Fill copper pours of one net with a grid of stitching vias (inside the fill, clear of other nets)."""
    net = get_net(b, a["net"])
    code = net.GetNetCode()
    a_fill_zones(b, {})
    zones = [z for z in b.Zones() if z.GetNetCode() == code and not z.GetIsRuleArea() and not z.IsTeardropArea()]
    if a.get("zone"):
        zones = [z for z in zones if a["zone"] in (z.GetZoneName(), z.m_Uuid.AsString())]
    if not zones:
        raise KeyError(f"No copper zone on net {a['net']!r}. Add one first with "
                       f"`kce pcb zone add --net {a['net']} --layer F.Cu --layer B.Cu`.")
    zone_layers = sorted({l for z in zones for l in z.GetLayerSet().Seq() if pcbnew.IsCopperLayer(l)})
    need = [layer_id(b, l) for l in a["layers"]] if a.get("layers") else zone_layers
    if len(need) < 2:
        raise ValueError(f"Net {a['net']} has a pour on one copper layer only ({[b.GetLayerName(l) for l in need]}); "
                         "stitching needs pours on at least two layers.")
    pitch, dia, drill = MM(a.get("pitch", 2.0)), MM(a.get("diameter", 0.6)), MM(a.get("drill", 0.3))
    clr = _clearance(b, a)
    margin = MM(a["margin"]) if a.get("margin") is not None else clr
    r = dia / 2
    if a.get("rect"):
        x, y, w, h = a["rect"]
        x0, y0, x1, y1 = MM(x), MM(y), MM(x + w), MM(y + h)
    else:
        boxes = [z.GetBoundingBox() for z in zones]
        x0, y0 = min(bb.GetX() for bb in boxes), min(bb.GetY() for bb in boxes)
        x1, y1 = max(bb.GetRight() for bb in boxes), max(bb.GetBottom() for bb in boxes)
    obs = _Obstacles(b, include_zones=True)
    room = _thermal_room(b, code)
    placed = [(v.GetPosition().x, v.GetPosition().y) for v in b.GetTracks() if _is_via(v)]

    def in_fill(x, y):
        for l in need:
            lz = [z for z in zones if z.IsOnLayer(l)]
            for k in range(9):
                px, py = (x, y) if k == 8 else (x + (r + margin) * math.cos(k * math.pi / 4),
                                                y + (r + margin) * math.sin(k * math.pi / 4))
                if not any(z.HitTestFilledArea(l, pcbnew.VECTOR2I(int(px), int(py)), 0) for z in lz):
                    return False
        return True

    added, new_vias, candidates, row = [], [], 0, 0
    yy = y0 + pitch / 2
    while yy < y1:
        xx = x0 + pitch / 2 + (pitch / 2 if a.get("pattern") == "staggered" and row % 2 else 0)
        while xx < x1:
            candidates += 1
            ok = in_fill(xx, yy) and all(math.hypot(xx - px, yy - py) >= dia + clr for px, py in placed) \
                and not obs.hit(xx, yy, r + clr, need, ok_net=code, any_net_pads=True, pad_margin=room)
            if ok:
                v = _new_via(b, xx, yy, net, dia, drill)
                placed.append((xx, yy))
                added.append(pt(v.GetPosition()))
                new_vias.append({"uuid": v.m_Uuid.AsString(), "at": pt(v.GetPosition())})
            xx += pitch
        yy += pitch
        row += 1
    fill = a_fill_zones(b, {})
    return {"net": a["net"], "added": len(added), "candidates": candidates, "pitch": mm(pitch),
            "diameter": mm(dia), "drill": mm(drill), "layers": [b.GetLayerName(l) for l in need],
            "vias": added[:200], "filled": fill["ok"], "_new_vias": new_vias}


def a_via_fence(b, a):
    """Shield a signal net: rows of vias (on the fence net) both sides of its tracks."""
    sig = get_net(b, a["net"])
    fence = get_net(b, a.get("fence_net") or "GND")
    pitch, dia, drill = MM(a.get("pitch", 1.0)), MM(a.get("diameter", 0.6)), MM(a.get("drill", 0.3))
    clr = _clearance(b, a)
    r = dia / 2
    layers = {layer_id(b, l) for l in a["layers"]} if a.get("layers") else None
    tracks = [t for t in b.GetTracks() if t.GetClass() == "PCB_TRACK" and t.GetNetCode() == sig.GetNetCode()
              and (layers is None or t.GetLayer() in layers)]
    if not tracks:
        raise KeyError(f"Net {a['net']!r} has no tracks to fence. Route it first (`kce pcb route` / `kce pcb autoroute`).")
    outline = _board_outline(b)
    edge = b.GetDesignSettings().m_CopperEdgeClearance
    obs = _Obstacles(b, include_zones=False)
    room = _thermal_room(b, fence.GetNetCode())
    placed = [(v.GetPosition().x, v.GetPosition().y) for v in b.GetTracks() if _is_via(v)]
    cu = [l for l in b.GetEnabledLayers().Seq() if pcbnew.IsCopperLayer(l)]
    added, new_vias, rejected = [], [], 0
    for t in tracks:
        s, e = t.GetStart(), t.GetEnd()
        ln = math.hypot(e.x - s.x, e.y - s.y)
        dx, dy = _unit(e.x - s.x, e.y - s.y)
        nx, ny = -dy, dx
        off = MM(a["offset"]) if a.get("offset") is not None else t.GetWidth() / 2 + clr + r + MM(0.05)
        n = max(1, int(ln // pitch))
        start = (ln - (n - 1) * pitch) / 2 if n > 1 else ln / 2
        for i in range(n):
            cx, cy = s.x + dx * (start + i * pitch), s.y + dy * (start + i * pitch)
            for side in (1, -1):
                x, y = cx + nx * off * side, cy + ny * off * side
                if outline is not None and not _circle_inside(outline, x, y, r + edge):
                    rejected += 1
                    continue
                if any(math.hypot(x - px, y - py) < max(pitch * 0.75, dia + clr) for px, py in placed):
                    rejected += 1
                    continue
                if obs.hit(x, y, r + clr, cu, ok_net=fence.GetNetCode(), any_net_pads=True, pad_margin=room):
                    rejected += 1
                    continue
                v = _new_via(b, x, y, fence, dia, drill)
                obs.add(v)
                placed.append((x, y))
                added.append(pt(v.GetPosition()))
                new_vias.append({"uuid": v.m_Uuid.AsString(), "at": pt(v.GetPosition())})
    fill = a_fill_zones(b, {}) if len(b.Zones()) else None
    return {"net": a["net"], "fence_net": fence.GetNetname(), "added": len(added), "rejected": rejected,
            "pitch": mm(pitch), "vias": added[:200], "filled": bool(fill and fill["ok"]),
            "_new_vias": new_vias}


def a_filter_vias(b, a):
    candidates, keep = set(a["candidates"]), set(a["keep"])
    for via in list(b.GetTracks()):
        uid = via.m_Uuid.AsString()
        if _is_via(via) and uid in candidates and uid not in keep:
            _remove(b, via)
    return a_fill_zones(b, {})


# ---------------------------------------------------------------------------
# Rule areas, dimensions, barcodes, holes, fiducials
# ---------------------------------------------------------------------------

PLACEMENT_SOURCES = {"sheet": "PLACEMENT_SOURCE_T_SHEETNAME", "component_class": "PLACEMENT_SOURCE_T_COMPONENT_CLASS",
                     "group": "PLACEMENT_SOURCE_T_GROUP_PLACEMENT", "design_block": "PLACEMENT_SOURCE_T_DESIGN_BLOCK"}


def a_rule_area(b, a):
    """Rule area (KiCad 10): keepout flags, placement source, a name for custom DRC rules."""
    z = pcbnew.ZONE(b)
    ls = pcbnew.LSET()
    for l in a.get("layers") or ["F.Cu", "B.Cu"]:
        ls.AddLayer(layer_id(b, l))
    z.SetLayerSet(ls)
    z.SetIsRuleArea(True)
    flags = set(a.get("disallow") or [])
    unknown = flags - {"tracks", "vias", "pads", "footprints", "zone_fills"}
    if unknown:
        raise ValueError(f"Unknown keepout flags {sorted(unknown)}; use tracks, vias, pads, footprints, zone_fills")
    z.SetDoNotAllowTracks("tracks" in flags)
    z.SetDoNotAllowVias("vias" in flags)
    z.SetDoNotAllowPads("pads" in flags)
    z.SetDoNotAllowFootprints("footprints" in flags)
    z.SetDoNotAllowZoneFills("zone_fills" in flags)
    if a.get("placement"):
        kind, _, source = a["placement"].partition("=")
        if kind not in PLACEMENT_SOURCES or not source:
            raise ValueError("placement must be sheet=NAME, component_class=NAME, group=NAME or design_block=NAME")
        z.SetPlacementAreaEnabled(True)
        z.SetPlacementAreaSourceType(getattr(pcbnew, PLACEMENT_SOURCES[kind]))
        z.SetPlacementAreaSource(source)
    if a.get("name"):
        z.SetZoneName(a["name"])
    o = z.Outline()
    o.NewOutline()
    for x, y in _zone_outline_points(b, a):
        o.Append(MM(x), MM(y))
    z.SetBorderDisplayStyle(pcbnew.ZONE_BORDER_DISPLAY_STYLE_DIAGONAL_EDGE, pcbnew.ZONE.GetDefaultHatchPitch(), True)
    b.Add(z)
    return {"rule_area": z.m_Uuid.AsString(), "name": z.GetZoneName(), "disallow": sorted(flags),
            "layers": [b.GetLayerName(l) for l in z.GetLayerSet().Seq()], "placement": a.get("placement")}


DIM_KINDS = {"aligned": "PCB_DIM_ALIGNED", "orthogonal": "PCB_DIM_ORTHOGONAL", "leader": "PCB_DIM_LEADER",
             "center": "PCB_DIM_CENTER", "radial": "PCB_DIM_RADIAL"}
DIM_UNITS = {"mm": "DIM_UNITS_MODE_MM", "in": "DIM_UNITS_MODE_INCH", "mils": "DIM_UNITS_MODE_MILS",
             "auto": "DIM_UNITS_MODE_AUTOMATIC"}


def a_add_dimension(b, a):
    kind = a.get("kind", "aligned")
    if kind not in DIM_KINDS:
        raise ValueError(f"dimension kind must be one of {sorted(DIM_KINDS)}")
    d = getattr(pcbnew, DIM_KINDS[kind])(b)
    d.SetLayer(layer_id(b, a.get("layer", "Cmts.User")))
    d.SetStart(vec(*a["start"]))
    d.SetEnd(vec(*a["end"]))
    if kind == "orthogonal":
        d.SetOrientation(pcbnew.PCB_DIM_ORTHOGONAL.DIR_VERTICAL if a.get("vertical")
                         else pcbnew.PCB_DIM_ORTHOGONAL.DIR_HORIZONTAL)
    if kind in ("aligned", "orthogonal"):
        d.SetHeight(MM(a.get("height", 5.0)))
    if kind == "radial" and a.get("leader_length") is not None:
        d.SetLeaderLength(MM(a["leader_length"]))
    if kind != "center":
        d.SetUnitsMode(getattr(pcbnew, DIM_UNITS[a.get("units", "mm")]))
        prec = a.get("precision")
        if prec is not None:
            d.SetPrecision(getattr(pcbnew, "DIM_PRECISION_X_" + "X" * int(prec)) if int(prec) else
                           pcbnew.DIM_PRECISION_X)
        if a.get("text"):
            d.SetOverrideTextEnabled(True)
            d.SetOverrideText(a["text"])
        if a.get("prefix"):
            d.SetPrefix(a["prefix"])
        if a.get("suffix"):
            d.SetSuffix(a["suffix"])
        size = MM(a.get("text_size", 1.0))
        d.SetTextSize(pcbnew.VECTOR2I(size, size))
    d.SetLineThickness(MM(a.get("line_width", 0.1)))
    d.Update()
    b.Add(d)
    res = {"dimension": kind, "uuid": d.m_Uuid.AsString(), "layer": d.GetLayerName()}
    if kind not in ("center", "leader"):
        res["value"] = mm(d.GetMeasuredValue()) if hasattr(d, "GetMeasuredValue") else None
    if kind != "center":
        res["text"] = d.GetShownText(False) if hasattr(d, "GetShownText") else d.GetText()
    return res


BARCODES = {"qr": "BARCODE_T_QR_CODE", "microqr": "BARCODE_T_MICRO_QR_CODE", "datamatrix": "BARCODE_T_DATA_MATRIX",
            "code39": "BARCODE_T_CODE_39", "code128": "BARCODE_T_CODE_128"}


def a_add_barcode(b, a):
    kind = a.get("kind", "qr")
    if kind not in BARCODES:
        raise ValueError(f"barcode kind must be one of {sorted(BARCODES)}")
    bc = pcbnew.PCB_BARCODE(b)
    bc.SetKind(getattr(pcbnew, BARCODES[kind]))
    bc.SetText(a["text"])
    bc.SetLayer(layer_id(b, a.get("layer", "F.SilkS")))
    bc.SetWidth(MM(a.get("size", 5.0)))
    bc.SetHeight(MM(a.get("height") or a.get("size", 5.0)))
    if a.get("ecc") and hasattr(bc, "SetErrorCorrection"):
        bc.SetErrorCorrection(getattr(pcbnew, "BARCODE_ECC_T_" + a["ecc"].upper()))
    if a.get("show_text") is not None and hasattr(bc, "SetShowText"):
        bc.SetShowText(bool(a["show_text"]))
    if a.get("knockout"):
        bc.SetIsKnockout(True)
    bc.SetPosition(vec(a["x"], a["y"]))
    if a.get("angle") and hasattr(bc, "SetOrientation"):
        bc.SetOrientation(pcbnew.EDA_ANGLE(float(a["angle"]), pcbnew.DEGREES_T))
    if hasattr(bc, "AssembleBarcode"):
        bc.AssembleBarcode()
    b.Add(bc)
    return {"barcode": kind, "uuid": bc.m_Uuid.AsString(), "text": a["text"], "layer": bc.GetLayerName()}


def _next_ref(b, prefix):
    used = {fp.GetReference() for fp in b.GetFootprints()}
    n = 1
    while f"{prefix}{n}" in used:
        n += 1
    return f"{prefix}{n}"


def _fp_circle(fp, layer, center, radius, width):
    s = pcbnew.PCB_SHAPE(fp)
    s.SetShape(pcbnew.SHAPE_T_CIRCLE)
    s.SetLayer(layer)
    s.SetWidth(MM(width))
    s.SetStart(center)
    s.SetEnd(pcbnew.VECTOR2I(center.x + int(radius), center.y))
    fp.Add(s)


def _board_only_fp(b, name, ref, value, x, y, exclude_pos=True):
    fp = pcbnew.FOOTPRINT(b)
    fp.SetFPIDAsString(name)  # no library nickname: board-only, not checked against a library
    fp.SetReference(ref)
    fp.SetValue(value)
    attrs = pcbnew.FP_EXCLUDE_FROM_BOM | pcbnew.FP_BOARD_ONLY
    if exclude_pos:
        attrs |= pcbnew.FP_EXCLUDE_FROM_POS_FILES
    fp.SetAttributes(attrs)
    fp.Reference().SetVisible(False)
    fp.Value().SetLayer(pcbnew.F_Fab)
    b.Add(fp)
    fp.SetPosition(vec(x, y))
    return fp


def a_add_hole(b, a):
    """Mounting hole as a board-only footprint (NPTH, or plated with an annular ring and optional net)."""
    drill = float(a.get("drill", 3.2))
    plated = not a.get("npth") and a.get("pad") is not None
    pad_d = float(a["pad"]) if plated else drill
    ref = a.get("ref") or _next_ref(b, "H")
    name = f"MountingHole_{drill:g}mm" + ("_Pad" if plated else "")
    fp = _board_only_fp(b, name, ref, name, a["x"], a["y"])
    p = pcbnew.PAD(fp)
    p.SetAttribute(pcbnew.PAD_ATTRIB_PTH if plated else pcbnew.PAD_ATTRIB_NPTH)
    p.SetShape(pcbnew.PAD_SHAPE_CIRCLE)
    p.SetSize(pcbnew.VECTOR2I(MM(pad_d), MM(pad_d)))
    p.SetDrillSize(pcbnew.VECTOR2I(MM(drill), MM(drill)))
    p.SetLayerSet(pcbnew.PAD.PTHMask() if plated else pcbnew.PAD.UnplatedHoleMask())
    p.SetNumber("1" if plated else "")
    fp.Add(p)
    p.SetPosition(fp.GetPosition())
    if plated and a.get("net"):
        p.SetNet(get_net(b, a["net"], create=True))
    _fp_circle(fp, pcbnew.F_CrtYd, fp.GetPosition(), MM(pad_d / 2 + 0.25), 0.05)
    _fp_circle(fp, pcbnew.Cmts_User, fp.GetPosition(), MM(drill / 2), 0.15)
    return {"reference": ref, "drill": drill, "pad": pad_d if plated else None, "plated": plated,
            "net": a.get("net") if plated else None, "at": [a["x"], a["y"]]}


def a_add_fiducial(b, a):
    """Fiducial: bare copper dot with a larger mask opening (board-only, kept in position files)."""
    copper, mask = float(a.get("copper", 1.0)), float(a.get("mask", 2.0))
    if mask <= copper:
        raise ValueError("--mask (opening diameter) must be larger than --copper")
    ref = a.get("ref") or _next_ref(b, "FID")
    name = f"Fiducial_{copper:g}mm_Mask{mask:g}mm"
    fp = _board_only_fp(b, name, ref, name, a["x"], a["y"], exclude_pos=False)
    p = pcbnew.PAD(fp)
    p.SetAttribute(pcbnew.PAD_ATTRIB_SMD)
    p.SetShape(pcbnew.PAD_SHAPE_CIRCLE)
    p.SetSize(pcbnew.VECTOR2I(MM(copper), MM(copper)))
    ls = pcbnew.LSET()
    ls.AddLayer(pcbnew.F_Cu)
    ls.AddLayer(pcbnew.F_Mask)
    p.SetLayerSet(ls)
    p.SetLocalSolderMaskMargin(MM((mask - copper) / 2))
    p.SetLocalClearance(MM((mask - copper) / 2))  # keeps pours out of the mask opening
    p.SetNumber("")
    fp.Add(p)
    p.SetPosition(fp.GetPosition())
    _fp_circle(fp, pcbnew.F_CrtYd, fp.GetPosition(), MM(mask / 2 + 0.25), 0.05)
    if a.get("side") == "bottom":
        fp.Flip(fp.GetPosition(), pcbnew.FLIP_DIRECTION_LEFT_RIGHT)
    return {"reference": ref, "copper": copper, "mask_opening": mask, "side": side_of(fp), "at": [a["x"], a["y"]]}


# ---------------------------------------------------------------------------
# Differential pairs, lengths, length tuning
# ---------------------------------------------------------------------------


def _net_lengths(b):
    """Per net: routed track length (segments + arcs), via count, pad-to-die length."""
    out = {}
    for t in b.GetTracks():
        d = out.setdefault(t.GetNetCode(), {"track": 0, "vias": 0, "segments": 0, "pad_to_die": 0})
        if _is_via(t):
            d["vias"] += 1
        else:
            d["track"] += t.GetLength()
            d["segments"] += 1
    for fp in b.GetFootprints():
        for p in fp.Pads():
            if p.GetNetCode() > 0 and p.GetPadToDieLength():
                out.setdefault(p.GetNetCode(), {"track": 0, "vias": 0, "segments": 0, "pad_to_die": 0})
                out[p.GetNetCode()]["pad_to_die"] += p.GetPadToDieLength()
    return out


def _len_entry(b, n, lens):
    d = lens.get(n.GetNetCode(), {"track": 0, "vias": 0, "segments": 0, "pad_to_die": 0})
    return {"net": n.GetNetname(), "netclass": _effective_netclass(b, n), "length": mm(d["track"] + d["pad_to_die"]),
            "track_length": mm(d["track"]), "pad_to_die": mm(d["pad_to_die"]), "vias": d["vias"],
            "segments": d["segments"]}


def _diff_pairs(b):
    pairs, seen = [], set()
    for name, n in sorted(b.GetNetsByName().items(), key=lambda kv: str(kv[0])):
        if n.GetNetCode() <= 0 or n.GetNetCode() in seen:
            continue
        other = b.DpCoupledNet(n)
        if other is None or other.GetNetCode() <= 0:
            continue
        seen.update({n.GetNetCode(), other.GetNetCode()})
        pairs.append(sorted([n, other], key=lambda x: _dp_polarity(x.GetNetname())))
    return pairs


def _dp_polarity(name):
    """0 for the positive member (P / +), 1 for the negative one (KiCad's BOARD::MatchDpSuffix)."""
    for ch in reversed(name):
        if ch.isdigit() or ch == "_":
            continue
        return 0 if ch in "+P" else 1
    return 1


def _dp_base(name):
    s = name
    i = len(s)
    while i and (s[i - 1].isdigit() or s[i - 1] == "_"):
        i -= 1
    return s[:i - 1] + s[i:] if i else s


def op_lengths(p):
    b = load(p["path"])
    lens = _net_lengths(b)
    import fnmatch
    nets = []
    for name, n in sorted(b.GetNetsByName().items(), key=lambda kv: str(kv[0])):
        if n.GetNetCode() <= 0:
            continue
        e = _len_entry(b, n, lens)
        if p.get("nets") and not any(fnmatch.fnmatchcase(e["net"], pat) for pat in p["nets"]):
            continue
        if p.get("netclass") and e["netclass"] != p["netclass"]:
            continue
        nets.append(e)
    classes = {}
    for e in nets:
        c = classes.setdefault(e["netclass"], {"netclass": e["netclass"], "nets": 0, "min": None, "max": None})
        c["nets"] += 1
        c["min"] = e["length"] if c["min"] is None else min(c["min"], e["length"])
        c["max"] = e["length"] if c["max"] is None else max(c["max"], e["length"])
    for c in classes.values():
        c["spread"] = round(c["max"] - c["min"], 6)
    pairs = []
    for pn, nn in _diff_pairs(b):
        ep, en = _len_entry(b, pn, lens), _len_entry(b, nn, lens)
        if p.get("nets") and not any(fnmatch.fnmatchcase(x, pat) for pat in p["nets"] for x in (ep["net"], en["net"])):
            continue
        pairs.append({"pair": _dp_base(ep["net"]), "p": ep["net"], "n": en["net"], "p_length": ep["length"],
                      "n_length": en["length"], "skew": round(abs(ep["length"] - en["length"]), 6),
                      "netclass": ep["netclass"]})
    return {"nets": nets, "netclasses": sorted(classes.values(), key=lambda c: c["netclass"]), "diff_pairs": pairs}


def op_diffpairs(p):
    b = load(p["path"])
    lens = _net_lengths(b)
    ns = b.GetDesignSettings().m_NetSettings
    out = []
    for pn, nn in _diff_pairs(b):
        ep, en = _len_entry(b, pn, lens), _len_entry(b, nn, lens)
        try:
            nc = ns.GetEffectiveNetClass(pn.GetNetname())
            width, gap = mm(nc.GetDiffPairWidth()), mm(nc.GetDiffPairGap())
        except Exception:
            width = gap = None
        out.append({"pair": _dp_base(ep["net"]), "p": ep["net"], "n": en["net"], "netclass": ep["netclass"],
                    "diff_pair_width": width, "diff_pair_gap": gap, "p_length": ep["length"],
                    "n_length": en["length"], "skew": round(abs(ep["length"] - en["length"]), 6),
                    "routed": bool(ep["segments"] and en["segments"])})
    return out


def _offset_polyline(pts, dist):
    """Polyline offset to the left (normal (-dy, dx)) by ``dist`` with mitred corners."""
    out = []
    n = len(pts)
    for i in range(n):
        if i == 0:
            dx, dy = _unit(pts[1][0] - pts[0][0], pts[1][1] - pts[0][1])
            out.append((pts[0][0] - dy * dist, pts[0][1] + dx * dist))
        elif i == n - 1:
            dx, dy = _unit(pts[-1][0] - pts[-2][0], pts[-1][1] - pts[-2][1])
            out.append((pts[-1][0] - dy * dist, pts[-1][1] + dx * dist))
        else:
            ax, ay = _unit(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1])
            bx, by = _unit(pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1])
            n1, n2 = (-ay, ax), (-by, bx)
            mx, my = _unit(n1[0] + n2[0], n1[1] + n2[1])
            cos = mx * n1[0] + my * n1[1]
            k = dist / cos if abs(cos) > 1e-6 else dist
            out.append((pts[i][0] + mx * k, pts[i][1] + my * k))
    return out


def _pad_on_net(fp, net):
    pads = [p for p in fp.Pads() if p.GetNetCode() == net.GetNetCode()]
    if not pads:
        raise KeyError(f"{fp.GetReference()} has no pad on net {net.GetNetname()!r}; "
                       f"pads: {sorted({f'{p.GetNumber()}={p.GetNetname()}' for p in fp.Pads()})}")
    return pads[0]


def a_diffpair_route(b, a):
    """Route a differential pair between two footprints as two coupled, parallel tracks."""
    pnet, nnet = get_net(b, a["p_net"]), get_net(b, a["n_net"])
    fa, fb = get_fp(b, a["from"]), get_fp(b, a["to"])
    ps, ns_, pe, ne = _pad_on_net(fa, pnet), _pad_on_net(fa, nnet), _pad_on_net(fb, pnet), _pad_on_net(fb, nnet)
    try:
        nc = b.GetDesignSettings().m_NetSettings.GetEffectiveNetClass(pnet.GetNetname())
        dw, dg = nc.GetDiffPairWidth(), nc.GetDiffPairGap()
    except Exception:
        dw = dg = 0
    width = MM(a["width"]) if a.get("width") else (dw or MM(0.2))
    gap = MM(a["gap"]) if a.get("gap") else (dg or MM(0.2))
    half = (gap + width) / 2
    P = lambda pad: (pad.GetPosition().x, pad.GetPosition().y)  # noqa: E731
    S = ((P(ps)[0] + P(ns_)[0]) / 2, (P(ps)[1] + P(ns_)[1]) / 2)
    E = ((P(pe)[0] + P(ne)[0]) / 2, (P(pe)[1] + P(ne)[1]) / 2)

    def escape(pp, pn, toward):
        """Unit direction leaving the pad pair perpendicular to its axis, on the side facing ``toward``."""
        ax, ay = _unit(pp[0] - pn[0], pp[1] - pn[1])
        dx, dy = -ay, ax
        mid = ((pp[0] + pn[0]) / 2, (pp[1] + pn[1]) / 2)
        if (toward[0] - mid[0]) * dx + (toward[1] - mid[1]) * dy < 0:
            dx, dy = -dx, -dy
        return dx, dy

    wp = [(MM(x), MM(y)) for x, y in (a.get("via_points") or [])]
    d0 = escape(P(ps), P(ns_), wp[0] if wp else E)
    d1 = escape(P(pe), P(ne), wp[-1] if wp else S)  # points from the end pads back towards the route
    # fan-in/out: each pad leaves at 45 degrees until the half pitch has converged to (gap + width) / 2
    fan_s = max(abs(math.hypot(P(ps)[0] - P(ns_)[0], P(ps)[1] - P(ns_)[1]) / 2 - half), MM(0.05))
    fan_e = max(abs(math.hypot(P(pe)[0] - P(ne)[0], P(pe)[1] - P(ne)[1]) / 2 - half), MM(0.05))
    lead = MM(a.get("lead", 0.5))
    s0 = (S[0] + d0[0] * fan_s, S[1] + d0[1] * fan_s)
    s1 = (s0[0] + d0[0] * lead, s0[1] + d0[1] * lead)
    e0 = (E[0] + d1[0] * fan_e, E[1] + d1[1] * fan_e)
    e1 = (e0[0] + d1[0] * lead, e0[1] + d1[1] * lead)
    center = [s0, s1] + wp
    last = center[-1]
    ddx, ddy = e1[0] - last[0], e1[1] - last[1]
    if abs(ddx) > 1 and abs(ddy) > 1:  # one 45-degree bend, like `kce pcb route`
        d = min(abs(ddx), abs(ddy))
        if abs(ddx) > abs(ddy):
            center.append((last[0] + math.copysign(abs(ddx) - d, ddx), last[1]))
        else:
            center.append((last[0], last[1] + math.copysign(abs(ddy) - d, ddy)))
    center += [e1, e0]
    center = [q for i, q in enumerate(center)
              if i == 0 or math.hypot(q[0] - center[i - 1][0], q[1] - center[i - 1][1]) > 1]
    if len(center) < 2 or math.hypot(e1[0] - s1[0], e1[1] - s1[1]) < 2 * lead:
        raise ValueError("Footprints are too close for a coupled route; move them apart or route with `kce pcb route`")
    d0 = _unit(center[1][0] - center[0][0], center[1][1] - center[0][1])
    d1 = _unit(center[-1][0] - center[-2][0], center[-1][1] - center[-2][1])

    def side(d, origin, pad):
        c = d[0] * (P(pad)[1] - origin[1]) - d[1] * (P(pad)[0] - origin[0])
        return 1 if c > 0 else -1

    sp, ep_ = side(d0, center[0], ps), side(d1, center[-1], pe)
    if sp != ep_:
        raise ValueError(f"{a['p_net']} is on the other side of the pair at {a['to']} (the pair would cross). "
                         "Swap the pins in the schematic, add --via-points, or route one net per layer with `kce pcb route`.")
    lay = a.get("layer") or ("F.Cu" if ps.IsOnLayer(pcbnew.F_Cu) and pe.IsOnLayer(pcbnew.F_Cu) else "B.Cu")
    res = {}
    for net, pa, pb_, sign in ((pnet, ps, pe, sp), (nnet, ns_, ne, -sp)):
        line = [P(pa)] + _offset_polyline(center, half * sign) + [P(pb_)]
        pts_mm = [(mm(x), mm(y)) for x, y in line]
        r = a_add_track(b, {"points": pts_mm, "layer": lay, "net": net.GetNetname(), "width": mm(width)})
        res[net.GetNetname()] = {"segments": len(r["segments"]), "points": [list(q) for q in pts_mm]}
    b.BuildConnectivity()
    lens = _net_lengths(b)
    lp, ln_ = lens[pnet.GetNetCode()]["track"], lens[nnet.GetNetCode()]["track"]
    return {"p": pnet.GetNetname(), "n": nnet.GetNetname(), "layer": lay, "width": mm(width), "gap": mm(gap),
            "tracks": res, "p_length": mm(lp), "n_length": mm(ln_), "skew": mm(abs(lp - ln_))}


def a_tune_length(b, a):
    """Add accordion meanders to a net's longest straight segments until it reaches a target length."""
    net = get_net(b, a["net"])
    code = net.GetNetCode()
    lens = _net_lengths(b)
    cur = lens.get(code, {"track": 0, "pad_to_die": 0})
    current = cur["track"] + cur["pad_to_die"]
    if a.get("match"):
        other = get_net(b, a["match"])
        o = lens.get(other.GetNetCode(), {"track": 0, "pad_to_die": 0})
        target = o["track"] + o["pad_to_die"]
    else:
        target = MM(a["target"])
    tol = MM(a.get("tolerance", 0.01))
    delta = target - current
    if delta <= tol:
        return {"net": a["net"], "length_before": mm(current), "length": mm(current), "target": mm(target),
                "added": 0, "note": "already at or above the target (meanders only add length)"}
    clr = _clearance(b, a)
    segs = sorted([t for t in b.GetTracks() if t.GetClass() == "PCB_TRACK" and t.GetNetCode() == code
                   and (not a.get("layer") or t.GetLayerName() == a["layer"])], key=lambda t: -t.GetLength())
    if not segs:
        raise KeyError(f"Net {a['net']!r} has no straight track segments. Route it first.")
    amp_max = MM(a.get("amplitude", 1.0))
    remaining, used = delta, []
    for t in segs:
        if remaining <= tol:
            break
        w = t.GetWidth()
        sp = MM(a["spacing"]) if a.get("spacing") else max(3 * w, w + clr)
        s, e = t.GetStart(), t.GetEnd()
        ln = t.GetLength()
        usable = ln - 2 * max(sp, w)
        kmax = int(usable // (2 * sp))
        if kmax < 1:
            continue
        k = min(kmax, math.ceil(remaining / (2 * amp_max)))
        amp = min(amp_max, remaining / (2 * k))
        dx, dy = _unit(e.x - s.x, e.y - s.y)
        m0 = (ln - (2 * k - 1) * sp) / 2
        obs = _Obstacles(b, include_zones=False, skip={t.m_Uuid.AsString()})
        placed = None
        for side in (1, -1):  # first side without collisions
            nx, ny = -dy * side, dx * side
            pts = [(s.x, s.y)]
            for i in range(k):
                bx, by = s.x + dx * (m0 + 2 * i * sp), s.y + dy * (m0 + 2 * i * sp)
                pts += [(bx, by), (bx + nx * amp, by + ny * amp), (bx + dx * sp + nx * amp, by + dy * sp + ny * amp),
                        (bx + dx * sp, by + dy * sp)]
            pts.append((e.x, e.y))
            ok = True
            for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
                L = math.hypot(x1 - x0, y1 - y0)
                for j in range(int(L // (w / 2)) + 1):
                    f = min(1.0, j * (w / 2) / L) if L else 0
                    if obs.hit(x0 + (x1 - x0) * f, y0 + (y1 - y0) * f, w / 2 + clr, [t.GetLayer()], ok_net=code,
                               via_like=False):
                        ok = False
                        break
                if not ok:
                    break
            if ok:
                placed = pts
                break
        if placed is None:
            continue
        lay, width = t.GetLayerName(), mm(w)
        _remove(b, t)
        a_add_track(b, {"points": [(mm(x), mm(y)) for x, y in placed], "layer": lay, "net": net.GetNetname(),
                        "width": width})
        used.append({"segment_length": mm(ln), "meanders": k, "amplitude": mm(amp), "spacing": mm(sp), "layer": lay})
        remaining -= 2 * k * amp
    b.BuildConnectivity()
    after = _net_lengths(b).get(code, {"track": 0, "pad_to_die": 0})
    final = after["track"] + after["pad_to_die"]
    res = {"net": a["net"], "length_before": mm(current), "length": mm(final), "target": mm(target),
           "error": mm(target - final), "tuned_segments": used}
    if target - final > tol:
        res["warning"] = ("Target not reached: no more straight segments with room for meanders. Use a larger "
                          "--amplitude, reroute with longer straight runs, or tune on another layer.")
    return res


# ---------------------------------------------------------------------------
# 3D models, footprints from library
# ---------------------------------------------------------------------------


def _model_dict(m):
    return {"file": m.m_Filename, "offset": [m.m_Offset.x, m.m_Offset.y, m.m_Offset.z],
            "rotation": [m.m_Rotation.x, m.m_Rotation.y, m.m_Rotation.z],
            "scale": [m.m_Scale.x, m.m_Scale.y, m.m_Scale.z], "show": bool(m.m_Show), "opacity": m.m_Opacity}


def op_models(p):
    b = load(p["path"])
    return [{"reference": fp.GetReference(), "fpid": fp.GetFPIDAsString(),
             "models": [_model_dict(m) for m in fp.Models()]} for fp in b.GetFootprints()]


def _copy_model(m):
    c = pcbnew.FP_3DMODEL()
    c.m_Filename = m.m_Filename
    c.m_Offset = pcbnew.VECTOR3D(m.m_Offset.x, m.m_Offset.y, m.m_Offset.z)
    c.m_Rotation = pcbnew.VECTOR3D(m.m_Rotation.x, m.m_Rotation.y, m.m_Rotation.z)
    c.m_Scale = pcbnew.VECTOR3D(m.m_Scale.x, m.m_Scale.y, m.m_Scale.z)
    c.m_Show = m.m_Show
    c.m_Opacity = m.m_Opacity
    return c


def a_set_model(b, a):
    """Add, replace, adjust or remove a footprint's 3D models (offset mm, rotation deg, scale)."""
    fp = get_fp(b, a["ref"])
    models = [_copy_model(m) for m in fp.Models()]
    mode = a.get("mode", "replace")
    idx = a.get("index")
    if mode == "remove":
        models = [m for i, m in enumerate(models) if idx is not None and i != idx]
    elif mode in ("replace", "add"):
        m = pcbnew.FP_3DMODEL()
        m.m_Filename = a["file"]
        m.m_Offset = pcbnew.VECTOR3D(*(a.get("offset") or (0, 0, 0)))
        m.m_Rotation = pcbnew.VECTOR3D(*(a.get("rotation") or (0, 0, 0)))
        m.m_Scale = pcbnew.VECTOR3D(*(a.get("scale") or (1, 1, 1)))
        m.m_Show = not a.get("hide")
        m.m_Opacity = float(a["opacity"]) if a.get("opacity") is not None else 1.0
        if mode == "add":
            models.append(m)
        elif idx is not None:
            if not 0 <= idx < len(models):
                raise IndexError(f"{a['ref']} has {len(models)} 3D model(s); --index {idx} is out of range")
            models[idx] = m
        else:
            models = [m]
    elif mode == "adjust":
        targets = [idx] if idx is not None else range(len(models))
        if not models:
            raise ValueError(f"{a['ref']} has no 3D model to adjust; add one with --file")
        for i in targets:
            m = models[i]
            if a.get("file"):
                m.m_Filename = a["file"]
            if a.get("offset") is not None:
                m.m_Offset = pcbnew.VECTOR3D(*a["offset"])
            if a.get("rotation") is not None:
                m.m_Rotation = pcbnew.VECTOR3D(*a["rotation"])
            if a.get("scale") is not None:
                m.m_Scale = pcbnew.VECTOR3D(*a["scale"])
            if a.get("hide") is not None:
                m.m_Show = not a["hide"]
            if a.get("opacity") is not None:
                m.m_Opacity = float(a["opacity"])
    else:
        raise ValueError("mode must be replace|add|adjust|remove")
    vm = fp.Models()
    vm.clear()
    for m in models:
        vm.push_back(m)
    return {"reference": fp.GetReference(), "models": [_model_dict(m) for m in fp.Models()]}


def _exchange_fp(b, old, new, reset_text=False, reset_models=False):
    """KiCad's PCB_EDIT_FRAME::ExchangeFootprint, minus the GUI: keep placement, ref, value,
    fields, pad nets and (unless reset) text placement and 3D models."""
    b.Add(new)
    if old.IsFlipped() != new.IsFlipped():
        new.Flip(new.GetPosition(), pcbnew.FLIP_DIRECTION_LEFT_RIGHT)
    new.SetOrientation(old.GetOrientation())
    new.SetPosition(old.GetPosition())
    new.SetReference(old.GetReference())
    new.SetValue(old.GetValue())
    new.SetPath(old.GetPath())
    if hasattr(new, "SetSheetname"):
        new.SetSheetname(old.GetSheetname())
        new.SetSheetfile(old.GetSheetfile())
    new.SetLocked(old.IsLocked())
    for attr in ("DNP", "ExcludedFromBOM", "ExcludedFromPosFiles"):
        getter = "Is" + attr if hasattr(old, "Is" + attr) else "Get" + attr
        if hasattr(new, "Set" + attr) and hasattr(old, getter):
            getattr(new, "Set" + attr)(getattr(old, getter)())
    for f in old.GetFields():
        name = f.GetName()
        if name in ("Reference", "Value", "Footprint", "Datasheet", "Description"):
            continue
        nf = new.GetField(name) if new.HasField(name) else None
        if nf is None:
            nf = pcbnew.PCB_FIELD(new, new.GetNextFieldOrdinal(), name)
            nf.SetLayer(f.GetLayer())
            nf.SetVisible(f.IsVisible())
            new.Add(nf)
        nf.SetText(f.GetText())
    if not reset_text:
        for getter in ("Reference", "Value"):
            of, nf = getattr(old, getter)(), getattr(new, getter)()
            nf.SetLayer(of.GetLayer())
            nf.SetVisible(of.IsVisible())
            nf.SetPosition(of.GetPosition())
            nf.SetTextSize(of.GetTextSize())
            nf.SetTextThickness(of.GetTextThickness())
            nf.SetTextAngle(of.GetTextAngle())
    if not reset_models:
        vm = new.Models()
        vm.clear()
        for m in old.Models():
            vm.push_back(_copy_model(m))
    old_pads = {}
    for p in old.Pads():
        old_pads.setdefault(p.GetNumber(), p)
    unmatched = []
    for p in new.Pads():
        op_ = old_pads.get(p.GetNumber())
        if op_ is not None:
            p.SetNet(op_.GetNet())
        elif p.GetNumber():
            unmatched.append(p.GetNumber())
    old_uuid = pcbnew.KIID(old.m_Uuid.AsString())
    _remove(b, old)
    new.SetUuid(old_uuid)
    return unmatched


def a_update_footprints(b, a):
    """Update footprints from their libraries (KiCad's Tools > Update Footprints from Library)."""
    updated, unchanged, problems = [], [], []
    for it in sorted(a["items"], key=lambda item: item["ref"]):
        fp = b.FindFootprintByReference(it["ref"])
        if fp is None:
            problems.append({"ref": it["ref"], "problem": "not on the board"})
            continue
        try:
            new = _load_fp(it["lib_path"], it["fp_name"])
        except KeyError as e:
            problems.append({"ref": it["ref"], "problem": str(e)})
            continue
        new.SetFPIDAsString(it["fpid"])
        if not a.get("force") and not fp.FootprintNeedsUpdate(new):
            unchanged.append(it["ref"])
            continue
        old_pads = _pad_positions(fp)
        unmatched = _exchange_fp(b, fp, new, a.get("reset_text"), a.get("reset_models"))
        entry = {"ref": it["ref"], "fpid": it["fpid"], "pads_moved": _moved_pads(old_pads, new)}
        if unmatched:
            entry["new_pads_without_net"] = unmatched
        updated.append(entry)
    b.BuildConnectivity()
    return {"updated": updated, "unchanged": unchanged, "problems": problems,
            "unrouted": b.GetConnectivity().GetUnconnectedCount(False)}


# ---------------------------------------------------------------------------
# KiCad 10: design variants, component classes, net ties
# ---------------------------------------------------------------------------


def op_board_extras(p):
    b = load(p["path"])
    fps = []
    ties = []
    for fp in b.GetFootprints():
        e = {"reference": fp.GetReference()}
        try:
            e["component_class"] = fp.GetComponentClassAsString()
        except Exception:
            e["component_class"] = ""
        e["variants"] = {}
        for name in p.get("variants") or []:
            v = fp.GetVariant(name) if hasattr(fp, "GetVariant") else None
            if v is not None:
                e["variants"][name] = {"dnp": v.GetDNP(), "exclude_from_bom": v.GetExcludedFromBOM(),
                                       "exclude_from_pos": v.GetExcludedFromPosFiles()}
        fps.append(e)
        if fp.IsNetTie():
            ties.append({"reference": fp.GetReference(), "fpid": fp.GetFPIDAsString(),
                         "pad_groups": [str(g) for g in fp.GetNetTiePadGroups()],
                         "nets": sorted({pd.GetNetname() for pd in fp.Pads() if pd.GetNetCode() > 0})})
    return {"variants": [n for n in p.get("variants") or [] if b.HasVariant(n)], "footprints": fps, "net_ties": ties}


ACTIONS = {
    "outline_rect": a_outline_rect, "outline_polygon": a_outline_polygon, "outline_circle": a_outline_circle,
    "clear_outline": a_clear_outline, "add_graphic": a_add_graphic, "add_text": a_add_text,
    "move_fp": a_move_fp, "rotate_fp": a_rotate_fp, "flip_fp": a_flip_fp, "lock_fp": a_lock_fp,
    "set_fp_field": a_set_fp_field, "set_fp_attr": a_set_fp_attr, "delete_fp": a_delete_fp, "add_fp": a_add_fp,
    "add_track": a_add_track, "add_via": a_add_via, "delete_tracks": a_delete_tracks, "delete_item": a_delete_item,
    "route": a_route, "add_zone": a_add_zone, "fill_zones": a_fill_zones, "unfill_zones": a_unfill_zones,
    "delete_zones": a_delete_zones, "set_copper_layers": a_set_copper_layers, "rename_layer": a_rename_layer,
    "set_pad_net": a_set_pad_net, "set_board_thickness": a_set_board_thickness, "set_origin": a_set_origin,
    "place_grid": a_place_grid, "legalize": a_legalize, "place_near": a_place_near,
    "update_from_netlist": a_update_from_netlist, "script": a_script,
    "autoroute_builtin": a_autoroute_builtin, "teardrops": a_teardrops, "via_stitch": a_via_stitch,
    "via_fence": a_via_fence, "filter_vias": a_filter_vias,
    "rule_area": a_rule_area, "add_dimension": a_add_dimension,
    "add_barcode": a_add_barcode, "add_hole": a_add_hole, "add_fiducial": a_add_fiducial,
    "diffpair_route": a_diffpair_route, "tune_length": a_tune_length, "set_model": a_set_model,
    "update_footprints": a_update_footprints,
}


def op_edit(p):
    b = load(p["path"])
    results = []
    for act in p["actions"]:
        name = act.get("action")
        fn = ACTIONS.get(name)
        if fn is None:
            raise ValueError(f"Unknown board action {name!r}; known: {sorted(ACTIONS)}")
        results.append({"action": name, "result": fn(b, act)})
    save(b, p["out"])
    return {"results": results, "out": p["out"]}


def op_new_board(p):
    b = pcbnew.CreateEmptyBoard() if hasattr(pcbnew, "CreateEmptyBoard") else pcbnew.NewBoard(p["path"])
    if p.get("copper_layers"):
        a_set_copper_layers(b, {"count": p["copper_layers"]})
    pcbnew.SaveBoard(p["path"], b, True)
    return {"path": p["path"]}


def op_export_dsn(p):
    b = load(p["path"])
    ok = pcbnew.ExportSpecctraDSN(b, p["out"])
    if not ok or not os.path.exists(p["out"]):
        raise RuntimeError("ExportSpecctraDSN failed (board needs an outline on Edge.Cuts)")
    return {"dsn": p["out"], "bytes": os.path.getsize(p["out"])}


def op_import_ses(p):
    b = load(p["path"])
    before = len(b.GetTracks())
    ok = pcbnew.ImportSpecctraSES(b, p["ses"])
    if not ok:
        raise RuntimeError(f"ImportSpecctraSES failed for {p['ses']}")
    save(b, p["out"])
    return {"tracks_before": before, "tracks_after": len(b.GetTracks()), "out": p["out"],
            "unrouted": b.GetConnectivity().GetUnconnectedCount(False)}


def op_query_script(p):
    b = load(p["path"])
    return a_script(b, p)


def _load_wizard(p):
    sys.path.insert(0, p["plugins_dir"])
    import importlib
    mod = importlib.import_module(p["module"])
    wiz = getattr(mod, p["class"])()
    return wiz


def op_footprint_wizard(p):
    """Run one of KiCad's bundled footprint wizards (scripting/plugins)."""
    wiz = _load_wizard(p)
    for k, v in (p.get("params") or {}).items():
        page, _, name = k.partition(".")
        param = wiz.GetParam(page, name)
        if param is None:
            raise KeyError(f"Wizard has no parameter {k!r}. Use `kce lib fp wizard-params`.")
        param.SetValue(v)
    wiz.BuildFootprint()
    if wiz.AnyErrors() or getattr(wiz, "module", None) is None:
        raise ValueError(getattr(wiz, "buildmessages", "wizard failed"))
    fp = wiz.module
    name = p.get("name") or fp.GetValue()
    fp.SetFPID(pcbnew.LIB_ID("", name))
    fp.SetValue(name)
    lib = p["library"]
    io = pcbnew.PCB_IO_KICAD_SEXPR()
    if not os.path.isdir(lib):
        io.CreateLibrary(lib)
    io.FootprintSave(lib, fp)
    return {"library": lib, "name": name, "pads": fp.GetPadCount(),
            "path": os.path.join(lib, name + ".kicad_mod"), "messages": getattr(wiz, "buildmessages", "")}


def op_wizard_params(p):
    wiz = _load_wizard(p)
    out = {}
    for prm in wiz.params:
        out[f"{prm.page}.{prm.name}"] = {"value": str(prm.raw_value), "units": str(prm.units),
                                         "hint": getattr(prm, "hint", ""), "designator": getattr(prm, "designator", "")}
    return {"name": wiz.GetName(), "description": wiz.GetDescription(), "params": out}


# ---------------------------------------------------------------------------
# Panelization (N x M copies, tabs + mouse bites or V-scores, rails, fiducials)
# ---------------------------------------------------------------------------


def _rect_poly_iu(x0, y0, x1, y1):
    sp = pcbnew.SHAPE_POLY_SET()
    sp.NewOutline()
    for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1)):
        sp.Append(int(round(x)), int(round(y)))
    return sp


def _chain_points(ch):
    return [(ch.CPoint(i).x, ch.CPoint(i).y) for i in range(ch.PointCount())]


def _edge_poly(b, pts):
    s = pcbnew.PCB_SHAPE(b)
    s.SetShape(pcbnew.SHAPE_T_POLY)
    s.SetLayer(pcbnew.Edge_Cuts)
    s.SetWidth(MM(0.1))
    s.SetPolyPoints([pcbnew.VECTOR2I(int(x), int(y)) for x, y in pts])
    b.Add(s)


def _hole_fp(b, name, ref, holes, drill):
    """One board-only footprint holding NPTH holes (mouse bites, tooling holes)."""
    fp = _board_only_fp(b, name, ref, name, mm(holes[0][0]), mm(holes[0][1]))
    for x, y in holes:
        p = pcbnew.PAD(fp)
        p.SetAttribute(pcbnew.PAD_ATTRIB_NPTH)
        p.SetShape(pcbnew.PAD_SHAPE_CIRCLE)
        p.SetSize(pcbnew.VECTOR2I(int(drill), int(drill)))
        p.SetDrillSize(pcbnew.VECTOR2I(int(drill), int(drill)))
        p.SetLayerSet(pcbnew.PAD.UnplatedHoleMask())
        p.SetNumber("")
        fp.Add(p)
        p.SetPosition(pcbnew.VECTOR2I(int(x), int(y)))
    return fp


def _dup(item):
    """A copy with a new UUID, outside any group (FOOTPRINT::Duplicate takes addToParentGroup)."""
    try:
        new = item.Duplicate(False)
    except TypeError:
        new = item.Duplicate()
    return new.Cast()


def _ungroup(item):
    """Detach an item from its group before it moves to another board (groups are not copied)."""
    g = item.GetParentGroup()
    if g is not None:
        g.RemoveItem(item)


def op_panelize(p):
    """Builtin panelizer: copies of the board on a grid, joined by tabs with mouse bites (spacing > 0)
    or butted with V-score lines (spacing 0), optional rails with fiducials and tooling holes."""
    rows, cols = int(p.get("rows", 2)), int(p.get("cols", 2))
    if rows < 1 or cols < 1 or rows * cols < 2:
        raise ValueError("A panel needs at least 2 boards (--rows/--cols)")
    space = MM(p.get("spacing", 2.0))
    mode = p.get("mode") or ("vscore" if space == 0 else "mousebites")
    if mode == "mousebites" and space <= 0:
        raise ValueError("Mouse bites need --spacing > 0 (use --mode vscore for butted boards)")
    if mode == "vscore":
        space = 0
    rail = p.get("rails", "tb")
    rail_w = MM(p.get("rail_width", 5.0))
    tab_w = MM(p.get("tab_width", 3.0))
    bite_d, bite_pitch = MM(p.get("bite_drill", 0.5)), MM(p.get("bite_pitch", 0.8))
    src = load(p["path"])
    outline = _board_outline(src)
    if outline is None:
        raise ValueError("The board has no closed outline on Edge.Cuts; add one with `kce pcb outline rect`.")
    bb = outline.BBox()
    bw, bh = bb.GetWidth(), bb.GetHeight()
    gap_frame = space if mode == "mousebites" else 0
    top = rail_w + gap_frame if rail in ("tb", "all") else 0
    left = rail_w + gap_frame if rail in ("lr", "all") else 0
    pw = 2 * left + cols * bw + (cols - 1) * space
    ph = 2 * top + rows * bh + (rows - 1) * space
    origin = (MM(p.get("origin_x", 100.0)), MM(p.get("origin_y", 100.0)))

    # the panel is the source board itself (keeps layers, stackup, design settings, net classes):
    # copies are duplicated first, then the originals are replaced by them
    panel = src
    originals = [list(src.GetFootprints()), list(src.GetTracks()), list(src.Zones()), list(src.GetDrawings())]
    copies, offsets = [], []
    for r in range(rows):
        for c in range(cols):
            k = r * cols + c + 1
            ox = origin[0] + left + c * (bw + space) - bb.GetX()
            oy = origin[1] + top + r * (bh + space) - bb.GetY()
            offsets.append((ox, oy))
            move = pcbnew.VECTOR2I(int(ox), int(oy))
            for fp in originals[0]:
                new = _dup(fp)
                new.Move(move)
                copies.append((new, k, [(pd.GetNetname(), pd.GetNetCode()) for pd in fp.Pads()]))
            for it in originals[1] + originals[2]:
                new = _dup(it)
                new.Move(move)
                copies.append((new, k, (it.GetNetname(), it.GetNetCode())))
            for d in originals[3]:
                if d.GetLayer() != pcbnew.Edge_Cuts:
                    new = _dup(d)
                    new.Move(move)
                    copies.append((new, k, None))
    for coll in originals:
        for it in coll:
            _ungroup(it)
            _remove(panel, it)

    def net_for(k, name, code):
        return get_net(panel, f"Board_{k}-{name}", create=True) if code > 0 and name else panel.FindNet(0)

    for new, k, nets in copies:
        panel.Add(new)
        if isinstance(nets, list):
            for pad, (name, code) in zip(new.Pads(), nets):
                pad.SetNet(net_for(k, name, code))
        elif nets is not None:
            new.SetNet(net_for(k, *nets))
    # solid panel material = boards + rails + tabs; Edge.Cuts = its outlines and holes
    solid = pcbnew.SHAPE_POLY_SET()
    boards = []
    for ox, oy in offsets:
        o = pcbnew.SHAPE_POLY_SET(outline)
        o.Move(pcbnew.VECTOR2I(int(ox), int(oy)))
        boards.append(o)
        solid.BooleanAdd(o)
    x0, y0 = origin
    rails = []
    if rail in ("tb", "all"):
        rails += [(x0, y0, x0 + pw, y0 + rail_w), (x0, y0 + ph - rail_w, x0 + pw, y0 + ph)]
    if rail in ("lr", "all"):
        rails += [(x0, y0, x0 + rail_w, y0 + ph), (x0 + pw - rail_w, y0, x0 + pw, y0 + ph)]
    for rr in rails:
        solid.BooleanAdd(_rect_poly_iu(*rr))
    tabs, bites, vscores = [], [], []
    if mode == "mousebites":
        def n_tabs(length):
            return int(p["tabs"]) if p.get("tabs") else max(1, min(4, int(mm(length) // 40) + 1))

        ov = MM(0.5)  # tabs overlap the board/rail so the union merges them
        for r in range(rows):
            for c in range(cols):
                bx = x0 + left + c * (bw + space)
                by = y0 + top + r * (bh + space)
                sides = []
                if c < cols - 1 or rail in ("lr", "all"):  # right edge -> next board or right rail
                    sides.append(("v", bx + bw, by, bh))
                if c == 0 and rail in ("lr", "all"):
                    sides.append(("v", bx - space, by, bh))
                if r < rows - 1 or rail in ("tb", "all"):
                    sides.append(("h", bx, by + bh, bw))
                if r == 0 and rail in ("tb", "all"):
                    sides.append(("h", bx, by - space, bw))
                for kind, sx, sy, length in sides:
                    n = n_tabs(length)
                    for i in range(n):
                        centre = (i + 0.5) * length / n
                        if kind == "v":
                            t = (sx - ov, sy + centre - tab_w / 2, sx + space + ov, sy + centre + tab_w / 2)
                            for ex in (sx, sx + space):  # bites along both cut lines
                                m = max(1, int(tab_w // bite_pitch))
                                bites += [(ex, sy + centre - (m - 1) * bite_pitch / 2 + j * bite_pitch) for j in range(m)]
                        else:
                            t = (sx + centre - tab_w / 2, sy - ov, sx + centre + tab_w / 2, sy + space + ov)
                            for ey in (sy, sy + space):
                                m = max(1, int(tab_w // bite_pitch))
                                bites += [(sx + centre - (m - 1) * bite_pitch / 2 + j * bite_pitch, ey) for j in range(m)]
                        tabs.append(t)
                        solid.BooleanAdd(_rect_poly_iu(*t))
    else:
        lay = layer_id(panel, p.get("vscore_layer", "Cmts.User"))
        for c in range(1, cols):
            x = x0 + left + c * bw
            vscores.append(((x, y0), (x, y0 + ph)))
        for r in range(1, rows):
            y = y0 + top + r * bh
            vscores.append(((x0, y), (x0 + pw, y)))
        if rail in ("tb", "all"):
            vscores += [((x0, y0 + rail_w), (x0 + pw, y0 + rail_w)), ((x0, y0 + ph - rail_w), (x0 + pw, y0 + ph - rail_w))]
        if rail in ("lr", "all"):
            vscores += [((x0 + rail_w, y0), (x0 + rail_w, y0 + ph)), ((x0 + pw - rail_w, y0), (x0 + pw - rail_w, y0 + ph))]
        for (ax, ay), (bx_, by_) in vscores:
            _add_shape(panel, panel.GetLayerName(lay), pcbnew.SHAPE_T_SEGMENT, 0.1, start=(mm(ax), mm(ay)),
                       end=(mm(bx_), mm(by_)))
            t = pcbnew.PCB_TEXT(panel)
            t.SetText("V-SCORE")
            t.SetLayer(lay)
            t.SetTextSize(pcbnew.VECTOR2I(MM(1), MM(1)))
            horiz = ay == by_
            t.SetPosition(pcbnew.VECTOR2I(int(ax + (MM(6) if horiz else 0)), int(ay - (MM(1) if horiz else 0)
                                                                             + (0 if horiz else MM(6)))))
            if not horiz:
                t.SetTextAngleDegrees(90)
            panel.Add(t)
    solid.Simplify()
    for i in range(solid.OutlineCount()):
        _edge_poly(panel, _chain_points(solid.Outline(i)))
        for j in range(solid.HoleCount(i)):
            _edge_poly(panel, _chain_points(solid.Hole(i, j)))
    if bites:
        _hole_fp(panel, "MouseBites", "MB1", bites, bite_d)
    fids, tooling = [], []
    if rails and p.get("fiducials", True):
        ry = [rr for rr in rails if rr[3] - rr[1] == rail_w] or rails
        a, b_ = ry[0], ry[-1]
        cy_a, cy_b = (a[1] + a[3]) / 2, (b_[1] + b_[3]) / 2
        if a[3] - a[1] == rail_w:  # horizontal rails
            fids = [(a[0] + MM(12), cy_a), (a[2] - MM(12), cy_a), (b_[0] + MM(12), cy_b)]
            tooling = [(a[0] + MM(5), cy_a), (a[2] - MM(5), cy_a), (b_[0] + MM(5), cy_b), (b_[2] - MM(5), cy_b)]
        else:
            cx_a, cx_b = (a[0] + a[2]) / 2, (b_[0] + b_[2]) / 2
            fids = [(cx_a, a[1] + MM(12)), (cx_a, a[3] - MM(12)), (cx_b, b_[1] + MM(12))]
            tooling = [(cx_a, a[1] + MM(5)), (cx_a, a[3] - MM(5)), (cx_b, b_[1] + MM(5)), (cx_b, b_[3] - MM(5))]
        for k, (fx, fy) in enumerate(fids, 1):
            a_add_fiducial(panel, {"x": mm(fx), "y": mm(fy), "ref": f"FID{k}", "copper": 1.0, "mask": 2.0})
        if p.get("tooling_holes", True):
            th = MM(p.get("tooling_drill", 2.0))
            if rail_w < th + MM(1):
                tooling = []
            if tooling:
                _hole_fp(panel, "ToolingHoles", "TH1", tooling, th)
    ds = panel.GetDesignSettings()
    ds.SetAuxOrigin(pcbnew.VECTOR2I(int(x0), int(y0 + ph)))
    if len(panel.Zones()):
        a_fill_zones(panel, {})  # pours must clear the new edges and mouse-bite holes
    panel.BuildConnectivity()
    save(panel, p["out"])
    return {"out": p["out"], "boards": rows * cols, "rows": rows, "cols": cols, "mode": mode,
            "board_size": [mm(bw), mm(bh)], "panel_size": [mm(pw), mm(ph)], "spacing": mm(space),
            "rails": rail if rails else "none", "rail_width": mm(rail_w) if rails else 0, "tabs": len(tabs),
            "mouse_bites": len(bites), "vscore_lines": len(vscores), "fiducials": len(fids),
            "tooling_holes": len(tooling) if rails and p.get("tooling_holes", True) else 0,
            "edge_cut_outlines": solid.OutlineCount(),
            "net_prefix": "Board_<n>-", "footprints": len(panel.GetFootprints())}


OPS = {
    "info": op_info, "footprints": op_footprints, "footprint": op_footprint, "nets": op_nets,
    "tracks": op_tracks, "zones": op_zones, "drawings": op_drawings, "layers": op_layers,
    "connectivity": op_connectivity, "ratsnest": op_ratsnest, "score": op_score, "stackup": op_stackup,
    "edit": op_edit,
    "new_board": op_new_board, "export_dsn": op_export_dsn, "import_ses": op_import_ses,
    "query_script": op_query_script, "footprint_wizard": op_footprint_wizard, "wizard_params": op_wizard_params,
    "panelize": op_panelize, "lengths": op_lengths, "diffpairs": op_diffpairs, "models": op_models, "board_extras": op_board_extras,
    "version": lambda p: {"pcbnew_version": pcbnew.Version(), "python": sys.version.split()[0]},
}


def serve_requests():
    """Read request/result paths until EOF; publish each response atomically."""
    for line in sys.stdin:
        message = json.loads(line)
        with open(message["request"], encoding="utf-8") as f:
            req = json.load(f)
        try:
            res = {"ok": True, "result": OPS[req["op"]](req.get("params") or {})}
        except Exception as e:
            res = {"ok": False, "error": f"{type(e).__name__}: {e}", "traceback": traceback.format_exc(),
                   "worker_type": type(e).__name__, "details": getattr(e, "details", {})}
        out = message["result"]
        with open(out + ".tmp", "w", encoding="utf-8") as f:
            json.dump(res, f, default=str)
        os.replace(out + ".tmp", out)


def main():
    if sys.argv[1:2] == ["--serve"]:
        serve_requests()
        return
    req_path, out_path = sys.argv[1], sys.argv[2]
    with open(req_path, encoding="utf-8") as f:
        req = json.load(f)
    try:
        fn = OPS[req["op"]]
        res = {"ok": True, "result": fn(req.get("params") or {})}
    except Exception as e:  # report everything back to the CLI
        res = {"ok": False, "error": f"{type(e).__name__}: {e}", "traceback": traceback.format_exc(),
                   "worker_type": type(e).__name__, "details": getattr(e, "details", {})}
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(res, f, default=str)


if __name__ == "__main__":
    main()
