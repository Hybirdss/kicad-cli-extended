"""Netlist-first input: an *intent* file that a schematic must realize.

An intent says which components exist (with their fields) and which pins belong on which
net.  It is the machine contract a caller can write without knowing anything about wire
geometry; ``kce sch apply`` builds the schematic to match it and ``kce sch verify`` compares
KiCad's own netlist against it.

Parsing happens once, at the file boundary: everything downstream works on the frozen
dataclasses below, so an unknown key or a bad value is an ``input`` error that names the JSON
path, not a surprise halfway through an edit.

Beyond components and nets, intent v1 accepts the three pin-level decisions an agent otherwise
has to make one command at a time: ``no_connect`` (pins that must carry a no-connect flag),
``power_flags`` (pins whose nets need a PWR_FLAG), and ``remove`` (references the design must not
contain).  They are optional, so every existing intent file keeps its meaning.
"""

from __future__ import annotations

import copy
import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from . import schematic as sch_mod
from . import sexpr
from .netlist import natural_key
from .. import COMMAND

INTENT_VERSION = 1

_INTENT_KEYS = ("version", "components", "nets", "no_connect", "power_flags", "remove")
_COMPONENT_KEYS = ("ref", "lib_id", "footprint", "value", "fields", "uuid", "at", "angle", "unit", "sheet",
                   "dnp", "in_bom", "on_board")
_NET_KEYS = ("name", "pins", "kind")
_POWER_FLAG_KEYS = ("pin",)
_ANGLES = (0, 90, 180, 270)


class IntentError(ValueError):
    """An intent file that cannot be used (bad JSON, unknown key, bad value)."""


@dataclass(frozen=True)
class ComponentIntent:
    ref: str
    lib_id: str
    footprint: Optional[str] = None
    value: Optional[str] = None
    fields: Dict[str, str] = field(default_factory=dict)
    uuid: Optional[str] = None
    at: Optional[Tuple[float, float]] = None
    angle: int = 0
    unit: int = 1
    sheet: str = ""
    dnp: Optional[bool] = None
    in_bom: Optional[bool] = None
    on_board: Optional[bool] = None

    def to_json(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"ref": self.ref, "lib_id": self.lib_id}
        for key in ("footprint", "value", "uuid", "dnp", "in_bom", "on_board"):
            if getattr(self, key) is not None:
                out[key] = getattr(self, key)
        if self.fields:
            out["fields"] = dict(self.fields)
        if self.at is not None:
            out["at"] = list(self.at)
        if self.angle:
            out["angle"] = self.angle
        if self.unit != 1:
            out["unit"] = self.unit
        if self.sheet:
            out["sheet"] = self.sheet
        return out


@dataclass(frozen=True)
class NetIntent:
    name: str
    pins: Tuple[str, ...]
    kind: str = "local"

    def to_json(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"name": self.name, "pins": list(self.pins)}
        if self.kind != "local":
            out["kind"] = self.kind
        return out


@dataclass(frozen=True)
class PowerFlagIntent:
    """A pin whose net needs a PWR_FLAG (the flag is placed on the pin itself)."""

    pin: str

    def to_json(self) -> Dict[str, Any]:
        return {"pin": self.pin}


@dataclass(frozen=True)
class Intent:
    components: Tuple[ComponentIntent, ...]
    nets: Tuple[NetIntent, ...]
    version: int = INTENT_VERSION
    no_connect: Tuple[str, ...] = ()
    power_flags: Tuple[PowerFlagIntent, ...] = ()
    remove: Tuple[str, ...] = ()

    def component(self, ref: str) -> Optional[ComponentIntent]:
        for c in self.components:
            if c.ref == ref:
                return c
        return None

    def to_json(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"version": self.version,
                               "components": [c.to_json() for c in self.components],
                               "nets": [n.to_json() for n in self.nets]}
        if self.no_connect:
            out["no_connect"] = list(self.no_connect)
        if self.power_flags:
            out["power_flags"] = [f.to_json() for f in self.power_flags]
        if self.remove:
            out["remove"] = list(self.remove)
        return out


# ---------------------------------------------------------------------------
# Parsing: every rejection names the JSON path it came from
# ---------------------------------------------------------------------------


def _where(source: str, path: str, msg: str) -> IntentError:
    return IntentError(f"{source}: {path}: {msg}")


def _obj(value: Any, source: str, path: str) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise _where(source, path, f"expected an object, got {_kind(value)}")
    return value


def _str(value: Any, source: str, path: str, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise _where(source, path, f"expected a string, got {_kind(value)}")
    if not value and not allow_empty:
        raise _where(source, path, "must not be empty")
    return value


def _int(value: Any, source: str, path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise _where(source, path, f"expected an integer, got {_kind(value)}")
    return value


def _bool(value: Any, source: str, path: str) -> bool:
    if not isinstance(value, bool):
        raise _where(source, path, f"expected a boolean, got {_kind(value)}")
    return value


def _num(value: Any, source: str, path: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _where(source, path, f"expected a number, got {_kind(value)}")
    return float(value)


def _kind(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def _keys(obj: Dict[str, Any], allowed: Tuple[str, ...], source: str, path: str) -> None:
    unknown = sorted(set(obj) - set(allowed))
    if unknown:
        raise _where(source, f"{path}.{unknown[0]}" if path else unknown[0],
                     f"unknown key (allowed: {', '.join(allowed)})")


def _lib_id(value: Any, source: str, path: str) -> str:
    text = _str(value, source, path)
    if ":" not in text or text.startswith(":") or text.endswith(":"):
        raise _where(source, path, f"expected LIBRARY:SYMBOL (e.g. Device:R), got {text!r}")
    return text


def _pin_ref(value: Any, source: str, path: str) -> str:
    text = _str(value, source, path)
    ref, _, pin = text.partition(":")
    if not ref or not pin:
        raise _where(source, path, f"expected REF:PIN (e.g. R1:2 or U1:VCC), got {text!r}")
    return text


def _uuid(value: Any, source: str, path: str) -> str:
    text = _str(value, source, path)
    try:
        import uuid as uuid_mod
        return str(uuid_mod.UUID(text))
    except ValueError:
        raise _where(source, path, f"expected a UUID (8-4-4-4-12 hex digits), got {text!r}")


def _component(value: Any, source: str, path: str) -> ComponentIntent:
    obj = _obj(value, source, path)
    _keys(obj, _COMPONENT_KEYS, source, path)
    for key in ("ref", "lib_id"):
        if key not in obj:
            raise _where(source, f"{path}.{key}", "required")
    at = None
    if "at" in obj:
        raw = obj["at"]
        if not isinstance(raw, list) or len(raw) != 2:
            raise _where(source, f"{path}.at", f"expected [x, y] in mm, got {_kind(raw)}")
        at = (_num(raw[0], source, f"{path}.at[0]"), _num(raw[1], source, f"{path}.at[1]"))
    angle = 0
    if "angle" in obj:
        angle = _int(obj["angle"], source, f"{path}.angle")
        if angle not in _ANGLES:
            raise _where(source, f"{path}.angle", f"must be one of {list(_ANGLES)}, got {angle}")
    unit = 1
    if "unit" in obj:
        unit = _int(obj["unit"], source, f"{path}.unit")
        if unit < 1:
            raise _where(source, f"{path}.unit", f"must be >= 1, got {unit}")
    fields: Dict[str, str] = {}
    if "fields" in obj:
        raw_fields = _obj(obj["fields"], source, f"{path}.fields")
        for name in sorted(raw_fields):
            if name in ("Reference", "Value", "Footprint"):
                raise _where(source, f"{path}.fields.{name}",
                             "use the top-level key for Reference/Value/Footprint")
            fields[name] = _str(raw_fields[name], source, f"{path}.fields.{name}")
    return ComponentIntent(
        ref=_str(obj["ref"], source, f"{path}.ref"),
        lib_id=_lib_id(obj["lib_id"], source, f"{path}.lib_id"),
        footprint=_str(obj["footprint"], source, f"{path}.footprint", allow_empty=True)
        if "footprint" in obj else None,
        value=_str(obj["value"], source, f"{path}.value", allow_empty=True) if "value" in obj else None,
        fields=fields,
        uuid=_uuid(obj["uuid"], source, f"{path}.uuid") if "uuid" in obj else None,
        at=at, angle=angle, unit=unit,
        sheet=_str(obj["sheet"], source, f"{path}.sheet") if "sheet" in obj else "",
        dnp=_bool(obj["dnp"], source, f"{path}.dnp") if "dnp" in obj else None,
        in_bom=_bool(obj["in_bom"], source, f"{path}.in_bom") if "in_bom" in obj else None,
        on_board=_bool(obj["on_board"], source, f"{path}.on_board") if "on_board" in obj else None)


def _net(value: Any, source: str, path: str) -> NetIntent:
    obj = _obj(value, source, path)
    _keys(obj, _NET_KEYS, source, path)
    for key in ("name", "pins"):
        if key not in obj:
            raise _where(source, f"{path}.{key}", "required")
    kind = "local"
    if "kind" in obj:
        kind = _str(obj["kind"], source, f"{path}.kind")
        if kind not in ("local", "global"):
            raise _where(source, f"{path}.kind", f"must be \"local\" or \"global\", got {kind!r}")
    raw_pins = obj["pins"]
    if not isinstance(raw_pins, list):
        raise _where(source, f"{path}.pins", f"expected an array of REF:PIN, got {_kind(raw_pins)}")
    if not raw_pins:
        raise _where(source, f"{path}.pins", "must list at least one pin")
    pins = tuple(_pin_ref(p, source, f"{path}.pins[{i}]") for i, p in enumerate(raw_pins))
    return NetIntent(name=_str(obj["name"], source, f"{path}.name"), pins=pins, kind=kind)


def _pin_ref_list(value: Any, source: str, path: str) -> Tuple[str, ...]:
    if not isinstance(value, list):
        raise _where(source, path, f"expected an array of REF:PIN, got {_kind(value)}")
    return tuple(_pin_ref(p, source, f"{path}[{i}]") for i, p in enumerate(value))


def _power_flag(value: Any, source: str, path: str) -> PowerFlagIntent:
    obj = _obj(value, source, path)
    _keys(obj, _POWER_FLAG_KEYS, source, path)
    if "pin" not in obj:
        raise _where(source, f"{path}.pin", "required")
    return PowerFlagIntent(pin=_pin_ref(obj["pin"], source, f"{path}.pin"))


def parse_intent_data(data: Any, source: str = "<intent>") -> Intent:
    obj = _obj(data, source, "")
    _keys(obj, _INTENT_KEYS, source, "")
    for key in ("version", "components", "nets"):
        if key not in obj:
            raise _where(source, key, "required")
    version = _int(obj["version"], source, "version")
    if version != INTENT_VERSION:
        raise _where(source, "version", f"unsupported intent version {version} (this kce reads {INTENT_VERSION})")
    raw_comps = obj["components"]
    if not isinstance(raw_comps, list):
        raise _where(source, "components", f"expected an array, got {_kind(raw_comps)}")
    raw_nets = obj["nets"]
    if not isinstance(raw_nets, list):
        raise _where(source, "nets", f"expected an array, got {_kind(raw_nets)}")
    components = tuple(_component(c, source, f"components[{i}]") for i, c in enumerate(raw_comps))
    nets = tuple(_net(n, source, f"nets[{i}]") for i, n in enumerate(raw_nets))
    seen_refs: Dict[str, str] = {}
    for i, c in enumerate(components):
        if c.ref in seen_refs:
            raise _where(source, f"components[{i}].ref", f"duplicate reference {c.ref!r} "
                                                        f"(also at {seen_refs[c.ref]})")
        seen_refs[c.ref] = f"components[{i}]"
    seen_nets: Dict[str, int] = {}
    pin_owner: Dict[str, str] = {}
    for i, n in enumerate(nets):
        if n.name in seen_nets:
            raise _where(source, f"nets[{i}].name", f"duplicate net name {n.name!r} "
                                                    f"(also at nets[{seen_nets[n.name]}])")
        seen_nets[n.name] = i
        for j, pin in enumerate(n.pins):
            ref = pin.partition(":")[0]
            if ref not in seen_refs:
                raise _where(source, f"nets[{i}].pins[{j}]",
                             f"unknown component {ref!r}; add it to components or fix the reference")
            if pin in pin_owner:
                raise _where(source, f"nets[{i}].pins[{j}]",
                             f"{pin} is already on net {pin_owner[pin]!r}; a pin belongs to one net")
            pin_owner[pin] = n.name
    no_connect = _pin_ref_list(obj["no_connect"], source, "no_connect") if "no_connect" in obj else ()
    seen_nc: Dict[str, int] = {}
    for i, pin in enumerate(no_connect):
        ref = pin.partition(":")[0]
        if ref not in seen_refs:
            raise _where(source, f"no_connect[{i}]",
                         f"unknown component {ref!r}; add it to components or fix the reference")
        if pin in pin_owner:
            raise _where(source, f"no_connect[{i}]",
                         f"{pin} is on net {pin_owner[pin]!r}; a no-connect pin belongs to no net")
        if pin in seen_nc:
            raise _where(source, f"no_connect[{i}]", f"duplicate pin {pin} (also at no_connect[{seen_nc[pin]}])")
        seen_nc[pin] = i
    power_flags = ()
    if "power_flags" in obj:
        raw_flags = obj["power_flags"]
        if not isinstance(raw_flags, list):
            raise _where(source, "power_flags", f"expected an array, got {_kind(raw_flags)}")
        power_flags = tuple(_power_flag(f, source, f"power_flags[{i}]") for i, f in enumerate(raw_flags))
    seen_flag: Dict[str, int] = {}
    for i, f in enumerate(power_flags):
        ref = f.pin.partition(":")[0]
        if ref not in seen_refs:
            raise _where(source, f"power_flags[{i}].pin",
                         f"unknown component {ref!r}; add it to components or fix the reference")
        if f.pin in seen_nc:
            raise _where(source, f"power_flags[{i}].pin",
                         f"{f.pin} is a no-connect pin; it cannot carry a PWR_FLAG")
        if f.pin in seen_flag:
            raise _where(source, f"power_flags[{i}].pin",
                         f"duplicate pin {f.pin} (also at power_flags[{seen_flag[f.pin]}])")
        seen_flag[f.pin] = i
    remove = ()
    if "remove" in obj:
        raw_remove = obj["remove"]
        if not isinstance(raw_remove, list):
            raise _where(source, "remove", f"expected an array of reference designators, got {_kind(raw_remove)}")
        remove = tuple(_str(r, source, f"remove[{i}]") for i, r in enumerate(raw_remove))
    seen_remove: Dict[str, int] = {}
    for i, ref in enumerate(remove):
        if ref in seen_refs:
            raise _where(source, f"remove[{i}]",
                         f"{ref!r} is also in components; list a reference in one place only")
        if ref in seen_remove:
            raise _where(source, f"remove[{i}]", f"duplicate reference {ref!r} (also at remove[{seen_remove[ref]}])")
        seen_remove[ref] = i
    return Intent(components=components, nets=nets, version=version,
                  no_connect=no_connect, power_flags=power_flags, remove=remove)


def parse_intent(path: str) -> Intent:
    """Read and validate an intent file; any problem is an input error naming the JSON path."""
    if not os.path.exists(path):
        raise FileNotFoundError(f"Intent file not found: {path}")
    with open(path, encoding="utf-8") as f:
        try:
            data = json.load(f)
        except json.JSONDecodeError as e:
            raise IntentError(f"{path}: not valid JSON at line {e.lineno} column {e.colno}: {e.msg}") from e
    return parse_intent_data(data, source=path)


# ---------------------------------------------------------------------------
# Comparison: intent vs KiCad's own netlist
# ---------------------------------------------------------------------------


def _pin_key(pin: str) -> tuple:
    ref, _, num = pin.partition(":")
    return (natural_key(ref), natural_key(num))


def _sorted_pins(pins) -> List[str]:
    return sorted(set(pins), key=_pin_key)


def actual_pin_numbers(netlist: Dict[str, Any]) -> Dict[str, Dict[str, str]]:
    libpins = netlist.get("libpins") or {}
    out: Dict[str, Dict[str, str]] = {}
    for comp in netlist.get("components", []):
        names = out.setdefault(comp["ref"], {})
        for (lib, part, num), meta in libpins.items():
            if lib == comp.get("lib") and part == comp.get("part") and meta.get("name"):
                names.setdefault(meta["name"], num)
    return out


def resolve_pin(pin: str, numbers: Dict[str, Dict[str, str]]) -> str:
    ref, _, spec = pin.partition(":")
    return f"{ref}:{numbers.get(ref, {}).get(spec, spec)}"


def _actual_nets(netlist: Dict[str, Any], numbers: Dict[str, Dict[str, str]]) -> List[Dict[str, Any]]:
    out = []
    for n in netlist.get("nets", []):
        out.append({"name": n["name"],
                    "pins": {resolve_pin(f"{nd['ref']}:{nd['pin']}", numbers) for nd in n["nodes"]}})
    return out


def _component_field(comp: Dict[str, Any], name: str) -> str:
    fields = comp.get("fields") or {}
    if name in fields:
        return fields[name]
    return (comp.get("properties") or {}).get(name, "")


def compare(intent: Intent, netlist: Dict[str, Any], *, subset: bool = False,
            check_lib_id: bool = False, check_uuid: bool = False, check_net_names: bool = False) -> Dict[str, Any]:
    """Differences between an intent and KiCad's netlist.

    Nets are matched by pin set; KiCad's own names only show up in renamed. An intended net
    without an exact counterpart is missing, an actual net without one is extra, and
    merged/split say why: one actual net spanning several intended nets, or one intended net
    spread over several actual nets. Every list is in natural order.

    No-connect flags and PWR_FLAGs are read back from KiCad's netlist (a no-connect pin carries
    ``+no_connect`` in its pin type; a power flag is a ``#FLG`` symbol on the net), so applying
    an intent twice reports no missing flags. With ``subset`` only the listed components and
    nets are checked; extra components and nets from the rest of the design are not reported.
    """
    numbers = actual_pin_numbers(netlist)
    intended = {n.name: {resolve_pin(p, numbers) for p in n.pins} for n in intent.nets}
    intended_nc = {resolve_pin(p, numbers) for p in intent.no_connect}
    actual_full = _actual_nets(netlist, numbers)
    if subset:
        listed_pins = set().union(*intended.values()) if intended else set()
        actual = [{"name": a["name"], "pins": a["pins"] & listed_pins}
                  for a in actual_full if a["pins"] & listed_pins]
    else:
        actual = actual_full
    actual_by_pins = {frozenset(a["pins"]): a["name"] for a in actual}

    comps = {c["ref"]: c for c in netlist.get("components", []) if not str(c["ref"]).startswith("#")}
    wanted = {c.ref: c for c in intent.components}
    missing_components = sorted(set(wanted) - set(comps), key=natural_key)
    removed = set(intent.remove)
    extra_components = ([] if subset else
                        sorted(set(comps) - set(wanted) - removed, key=natural_key))
    remove_present = sorted(removed & set(comps), key=natural_key)

    symbol_mismatches, identity_mismatches = [], []
    field_mismatches = []
    attribute_mismatches = []
    for ref in sorted(set(wanted) & set(comps), key=natural_key):
        ci, ca = wanted[ref], comps[ref]
        actual_id = f"{ca.get('lib') or ''}:{ca.get('part') or ''}"
        if check_lib_id and ci.lib_id != actual_id:
            symbol_mismatches.append({"ref": ref, "expected": ci.lib_id, "actual": actual_id})
        if check_uuid and ci.uuid is not None and ci.uuid not in ca.get("tstamps", []):
            identity_mismatches.append({"ref": ref, "expected": ci.uuid, "actual": ca.get("tstamps", [])})
        checks = []
        if ci.footprint is not None:
            checks.append(("footprint", ci.footprint, ca.get("footprint", "")))
        if ci.value is not None:
            checks.append(("value", ci.value, ca.get("value", "")))
        for name, value in ci.fields.items():
            checks.append((name, value, _component_field(ca, name)))
        for name, expected, found in checks:
            if expected != found:
                field_mismatches.append({"ref": ref, "field": name, "expected": expected, "actual": found})
        attrs = {"dnp": bool(ca.get("dnp")),
                 "in_bom": not ca.get("exclude_from_bom", False),
                 "on_board": not ca.get("exclude_from_board", False)}
        for name in ("dnp", "in_bom", "on_board"):
            expected = getattr(ci, name)
            if expected is not None and expected != attrs[name]:
                attribute_mismatches.append({"ref": ref, "field": name,
                                             "expected": expected, "actual": attrs[name]})
    field_mismatches.sort(key=lambda m: (natural_key(m["ref"]), m["field"]))
    attribute_mismatches.sort(key=lambda m: (natural_key(m["ref"]), m["field"]))

    ordered = sorted(intended, key=natural_key)
    missing_nets, renamed = [], []
    for name in ordered:
        found = actual_by_pins.get(frozenset(intended[name]))
        if found is None:
            missing_nets.append({"name": name, "pins": _sorted_pins(intended[name])})
        elif found != name:
            renamed.append({"name": name, "actual": found, "pins": _sorted_pins(intended[name])})
    exact = {frozenset(v) for v in intended.values()}
    # a no-connect pin is its own single-pin net in KiCad; that net is expected, not extra
    extra_nets = ([] if subset else
                  [{"name": a["name"], "pins": _sorted_pins(a["pins"])}
                   for a in actual if frozenset(a["pins"]) not in exact
                   and not frozenset(a["pins"]) <= intended_nc])
    extra_nets.sort(key=lambda n: natural_key(n["name"]))

    owner = {pin: name for name, pins in intended.items() for pin in pins}
    merged = []
    for a in actual:
        names = {owner[p] for p in a["pins"] if p in owner}
        if len(names) >= 2:
            merged.append({"actual": a["name"], "nets": sorted(names, key=natural_key),
                           "pins": _sorted_pins(a["pins"])})
    merged.sort(key=lambda m: natural_key(m["actual"]))

    split = []
    for name in ordered:
        holders = sorted((a["name"] for a in actual if a["pins"] & intended[name]), key=natural_key)
        if len(holders) >= 2:
            split.append({"name": name, "nets": holders, "pins": _sorted_pins(intended[name])})

    pin_net = {p: a for a in actual_full for p in a["pins"]}
    unconnected_pins = _sorted_pins(
        p for name in ordered for p in intended[name] if p not in pin_net or len(pin_net[p]["pins"]) == 1)

    intended_nc = {resolve_pin(p, numbers) for p in intent.no_connect}
    actual_nc = {resolve_pin(f"{nd['ref']}:{nd['pin']}", numbers)
                 for n in netlist.get("nets", []) for nd in n["nodes"]
                 if "no_connect" in (nd.get("pintype") or "")}
    listed_refs = set(wanted)
    no_connects_missing = _sorted_pins(intended_nc - actual_nc)
    no_connects_extra = [] if subset else _sorted_pins(
        {p for p in actual_nc if p.partition(":")[0] in listed_refs} - intended_nc)

    name_mismatches = list(renamed) if check_net_names else []
    match = not (symbol_mismatches or identity_mismatches or name_mismatches
                 or missing_components or field_mismatches or attribute_mismatches or missing_nets
                 or merged or split or unconnected_pins or no_connects_missing
                 or no_connects_extra or remove_present)
    if not subset:
        match = match and not (extra_components or extra_nets)
    return {
        "match": match,
        "missing_components": missing_components,
        "extra_components": extra_components,
        "field_mismatches": field_mismatches,
        "symbol_mismatches": symbol_mismatches,
        "identity_mismatches": identity_mismatches,
        "name_mismatches": name_mismatches,
        "attribute_mismatches": attribute_mismatches,
        "remove_present": remove_present,
        "missing_nets": missing_nets,
        "extra_nets": extra_nets,
        "renamed": renamed,
        "merged": merged,
        "split": split,
        "unconnected_pins": unconnected_pins,
        "no_connects_missing": no_connects_missing,
        "no_connects_extra": no_connects_extra,
    }


def missing_power_flags(intent: Intent, sess, proj) -> List[Dict[str, Any]]:
    """Intent PWR_FLAG pins that carry no flag in the schematic.

    KiCad's netlist export leaves power symbols out, so a PWR_FLAG cannot be seen there. The
    schematic is the source of truth: a flag counts when a ``power:PWR_FLAG`` pin sits exactly
    on the intended pin, which is where ``apply`` places it.
    """
    missing = []
    for f in intent.power_flags:
        ref, _, spec = f.pin.partition(":")
        tree = None
        for path in _sheet_files(sess, proj):
            if sch_mod.find_symbols(sess.tree(path), ref):
                tree = sess.tree(path)
                break
        if tree is None:
            missing.append({"pin": f.pin})
            continue
        try:
            p = _pin(tree, ref, spec)
        except (KeyError, ValueError):
            missing.append({"pin": f.pin})
            continue
        if not _has_power_flag(tree, p["x"], p["y"]):
            missing.append({"pin": f.pin})
    return missing


def verify_intent(intent: Intent, sess, proj, netlist: Dict[str, Any],
                  *, subset: bool = False, check_lib_id: bool = False, check_uuid: bool = False,
                  check_net_names: bool = False) -> Dict[str, Any]:
    """``compare`` plus the flag checks that only the schematic can answer."""
    result = compare(intent, netlist, subset=subset, check_lib_id=check_lib_id,
                     check_uuid=check_uuid, check_net_names=check_net_names)
    result["power_flags_missing"] = missing_power_flags(intent, sess, proj)
    result["match"] = result["match"] and not result["power_flags_missing"]
    return result


class DesignMismatch(RuntimeError):
    """sch verify --strict: the schematic does not match the intent."""


def mismatch_error(result: Dict[str, Any], intent_path: str) -> DesignMismatch:
    counts = ", ".join(f"{len(result[k])} {k.replace('_', ' ')}"
                       for k in ("missing_components", "extra_components", "field_mismatches",
                                 "symbol_mismatches", "identity_mismatches", "name_mismatches",
                                 "attribute_mismatches", "remove_present", "missing_nets",
                                 "extra_nets", "merged", "split", "unconnected_pins",
                                 "no_connects_missing", "no_connects_extra", "power_flags_missing")
                       if result[k])
    return DesignMismatch(
        f"The schematic does not match {intent_path}: {counts}. The design was not changed. "
        f"Run `kce sch apply {intent_path}` to make it match, then "
        f"`kce sch verify --expect {intent_path}` again (without --strict, a mismatch exits 0).")


# ---------------------------------------------------------------------------
# Apply: build the schematic the intent describes
# ---------------------------------------------------------------------------

#: free-grid placement for components without ``at``: a 25.4 mm lattice from (25.4, 25.4)
_GRID_ORIGIN = 25.4
_GRID_STEP = 25.4
_GRID_COLUMNS = 8
_GRID_MIN_GAP = 20.32
_LABEL_STUB = 2.54


def _resolve_sheet(sess, proj, name: str) -> str:
    """Absolute file of an intent component's ``sheet`` ("" = the root schematic)."""
    if not name:
        return os.path.abspath(proj.sch_path)
    cand = name if os.path.isabs(name) else os.path.join(proj.dir, name)
    if os.path.exists(cand):
        return os.path.abspath(cand)
    if os.path.exists(cand + ".kicad_sch"):
        return os.path.abspath(cand + ".kicad_sch")
    for h in sch_mod.hierarchy(sess.tree, proj.sch_path):
        if h["name"] == name or h["file"] == cand or os.path.basename(h["file"]) == name:
            return os.path.abspath(h["file"])
    raise FileNotFoundError(f"Sheet {name!r} not found (file name, path or sheet name); "
                            f"run `{COMMAND} sch hierarchy`")


def _sheet_files(sess, proj) -> List[str]:
    """Every existing schematic file of the project hierarchy, root first."""
    out = [os.path.abspath(proj.sch_path)]
    for h in sch_mod.hierarchy(sess.tree, proj.sch_path):
        f = os.path.abspath(h["file"])
        if os.path.exists(f) and f not in out:
            out.append(f)
    return out


def _free_position(occupied: List[Tuple[float, float]]) -> Tuple[float, float]:
    """First lattice point at least ``_GRID_MIN_GAP`` from every occupied symbol origin."""
    for row in range(64):
        for col in range(_GRID_COLUMNS):
            x = _GRID_ORIGIN + col * _GRID_STEP
            y = _GRID_ORIGIN + row * _GRID_STEP
            if all(max(abs(x - px), abs(y - py)) >= _GRID_MIN_GAP for px, py in occupied):
                return round(x, 4), round(y, 4)
    raise ValueError("No free placement position left on the schematic grid; give the component an `at`")


def _set_symbol_uuid(sym, uid: str) -> None:
    node = sym.find("uuid")
    if node is None:
        sym.append(sexpr.S("uuid", sexpr.Q(uid)))
    else:
        node[1] = sexpr.Q(uid)


def _has_pin_label(tree, ref: str, spec: str, net: str, kind: str) -> bool:
    """True when this pin already carries a label of this net (a second apply changes nothing)."""
    try:
        _, p = sch_mod.find_pin(tree, ref, spec)
    except (KeyError, ValueError):
        return False
    tag = sch_mod.LABEL_KINDS[kind]
    ox, oy = p["outward"]
    points = [(round(p["x"] + ox * _LABEL_STUB, 4), round(p["y"] + oy * _LABEL_STUB, 4)),
              (p["x"], p["y"])]
    for node in tree.find_all(tag):
        if len(node) < 2 or isinstance(node[1], sexpr.SList) or str(node[1]) != net:
            continue
        at = node.find("at")
        if at is None:
            continue
        x, y = float(at[1]), float(at[2])
        if any(abs(x - cx) <= 0.01 and abs(y - cy) <= 0.01 for cx, cy in points):
            return True
    return False


def _has_power_flag(tree, x: float, y: float) -> bool:
    for sym in sch_mod.placed_symbols(tree):
        if sym.value("lib_id") != "power:PWR_FLAG":
            continue
        try:
            pins = sch_mod.symbol_pins(tree, sym)
        except KeyError:
            continue
        if any(abs(p["x"] - x) <= 0.01 and abs(p["y"] - y) <= 0.01 for p in pins):
            return True
    return False


def _has_no_connect(tree, x: float, y: float) -> bool:
    for node in tree.find_all("no_connect"):
        at = node.find("at")
        if at is not None and abs(float(at[1]) - x) <= 0.01 and abs(float(at[2]) - y) <= 0.01:
            return True
    return False


def _place_power_flag(tree, proj, path: str, sess, x: float, y: float) -> None:
    n = len([s for s in sch_mod.placed_symbols(tree) if s.value("lib_id") == "power:PWR_FLAG"])
    sym = sch_mod.add_symbol(tree, proj, "power:PWR_FLAG", f"#FLG{n + 1:02d}", x, y,
                             instance_paths=sch_mod.instance_paths_for(sess.tree, proj.sch_path, path) or None,
                             project_name=proj.name, snap_grid=False)
    pins = sch_mod.symbol_pins(tree, sym)
    if pins:
        sch_mod.move_symbol(sym, dx=x - pins[0]["x"], dy=y - pins[0]["y"], snap_grid=False)


def _pin(tree, ref: str, spec: str) -> Dict[str, Any]:
    return sch_mod.find_pin(tree, ref, spec)[1]


def apply_intent(intent: Intent, sess, proj, *, power_flags: bool = False,
                 prune: bool = False, patch: bool = False) -> Dict[str, Any]:
    """Make the schematic realize ``intent``; return what changed (the CLI appends ``compare``).

    Components are placed or updated first, then every net is labeled at its pins, then
    no-connect flags, optional PWR_FLAGs and pruning.  With ``patch`` the pass only adds or
    modifies: ``remove`` is not applied and nothing is pruned, which is what an ECO wants.
    All edits happen on copies of the sheet trees and are committed to the session only after
    the whole pass succeeds, so a failure leaves the schematic untouched.
    """
    prune = prune and not patch
    sheets = {c.sheet: _resolve_sheet(sess, proj, c.sheet) for c in intent.components}
    comp_sheet = {c.ref: sheets[c.sheet] for c in intent.components}
    wanted = {c.ref for c in intent.components}
    removed_refs = set(intent.remove)
    wanted_nets = {n.name for n in intent.nets}
    paths = sorted(set(_sheet_files(sess, proj)) | set(sheets.values()))
    trees = {p: copy.deepcopy(sess.tree(p)) for p in paths}
    occupied = {p: [(d["x"], d["y"]) for d in sch_mod.list_symbols(trees[p])] for p in paths}
    for c in intent.components:
        if c.at is not None:
            occupied[comp_sheet[c.ref]].append(c.at)

    removed: List[Dict[str, Any]] = []
    if not patch:
        for ref in intent.remove:
            for path in paths:
                if sch_mod.find_symbols(trees[path], ref):
                    sch_mod.delete_symbol(trees[path], ref)
                    removed.append({"ref": ref, "sheet": _sheet_label(sess, proj, path)})

    placed: List[Dict[str, Any]] = []
    updated: List[Dict[str, Any]] = []
    labels_added: List[Dict[str, Any]] = []
    flags_added: List[Dict[str, Any]] = []
    no_connects_added: List[Dict[str, Any]] = []

    for c in intent.components:
        path = comp_sheet[c.ref]
        tree = trees[path]
        found = sch_mod.find_symbols(tree, c.ref)
        if found:
            sym = found[0]
            now = sch_mod.symbol_dict(sym, tree)
            changed = []
            if c.value is not None and now["value"] != c.value:
                sch_mod.set_field(sym, "Value", c.value)
                changed.append("value")
            if c.footprint is not None and now["footprint"] != c.footprint:
                sch_mod.set_field(sym, "Footprint", c.footprint)
                changed.append("footprint")
            for name in sorted(c.fields):
                if now["fields"].get(name) != c.fields[name]:
                    sch_mod.set_field(sym, name, c.fields[name])
                    changed.append(name)
            if c.uuid is not None and now["uuid"] != c.uuid:
                _set_symbol_uuid(sym, c.uuid)
                changed.append("uuid")
            for name in ("dnp", "in_bom", "on_board"):
                want = getattr(c, name)
                if want is not None and now[name] != want:
                    sch_mod.set_attr(sym, name, want)
                    changed.append(name)
            if changed:
                updated.append({"ref": c.ref, "fields": changed})
            continue
        x, y = c.at if c.at is not None else _free_position(occupied[path])
        sch_mod.add_symbol(tree, proj, c.lib_id, c.ref, x, y, angle=c.angle, unit=c.unit,
                           value=c.value, footprint=c.footprint, fields=dict(c.fields),
                           instance_paths=sch_mod.instance_paths_for(sess.tree, proj.sch_path, path) or None,
                           project_name=proj.name, snap_grid=False, uuid=c.uuid)
        occupied[path].append((x, y))
        sym = sch_mod.find_symbols(tree, c.ref)[0]
        for name in ("dnp", "in_bom", "on_board"):
            want = getattr(c, name)
            if want is not None:
                sch_mod.set_attr(sym, name, want)
        placed.append({"ref": c.ref, "lib_id": c.lib_id, "at": [x, y], "sheet": c.sheet or "/"})

    for n in intent.nets:
        for pin in n.pins:
            ref, _, spec = pin.partition(":")
            tree = trees[comp_sheet[ref]]
            if _has_pin_label(tree, ref, spec, n.name, n.kind):
                continue
            sch_mod.label_pin(tree, ref, spec, n.name, kind=n.kind)
            labels_added.append({"pin": pin, "net": n.name, "kind": n.kind})

    for pin in intent.no_connect:
        ref, _, spec = pin.partition(":")
        tree = trees[comp_sheet[ref]]
        p = _pin(tree, ref, spec)
        if _has_no_connect(tree, p["x"], p["y"]):
            continue
        sch_mod.add_no_connect(tree, p["x"], p["y"])
        no_connects_added.append({"pin": pin, "at": [p["x"], p["y"]]})

    if power_flags:
        for n in intent.nets:
            types = {}
            for pin in n.pins:
                ref, _, spec = pin.partition(":")
                types[pin] = _pin(trees[comp_sheet[ref]], ref, spec)["type"]
            # ERC asks for a driver on a net that has power-input pins and no power output
            if "power_in" not in types.values() or "power_out" in types.values():
                continue
            pin = sorted((p for p, t in types.items() if t == "power_in"), key=_pin_key)[0]
            ref, _, spec = pin.partition(":")
            tree = trees[comp_sheet[ref]]
            p = _pin(tree, ref, spec)
            if _has_power_flag(tree, p["x"], p["y"]):
                continue
            _place_power_flag(tree, proj, comp_sheet[ref], sess, p["x"], p["y"])
            flags_added.append({"net": n.name, "pin": pin, "at": [p["x"], p["y"]]})

    for f in intent.power_flags:
        ref, _, spec = f.pin.partition(":")
        tree = trees[comp_sheet[ref]]
        p = _pin(tree, ref, spec)
        if _has_power_flag(tree, p["x"], p["y"]):
            continue
        _place_power_flag(tree, proj, comp_sheet[ref], sess, p["x"], p["y"])
        entry: Dict[str, Any] = {"pin": f.pin, "at": [p["x"], p["y"]]}
        net_name = next((n.name for n in intent.nets if f.pin in n.pins), None)
        if net_name is not None:
            entry["net"] = net_name
        flags_added.append(entry)

    extras_symbols: List[Dict[str, Any]] = []
    extras_labels: List[Dict[str, Any]] = []
    pruned_symbols: List[Dict[str, Any]] = []
    pruned_labels: List[Dict[str, Any]] = []
    for path in paths:
        tree = trees[path]
        for d in sorted(sch_mod.list_symbols(tree), key=lambda d: natural_key(d["reference"])):
            ref = d["reference"] or ""
            if ref.startswith("#") or ref in wanted or ref in removed_refs:
                continue
            extras_symbols.append({"ref": ref, "sheet": _sheet_label(sess, proj, path)})
            if prune:
                sch_mod.delete_symbol(tree, ref)
                pruned_symbols.append({"ref": ref, "sheet": _sheet_label(sess, proj, path)})
        for d in sorted(sch_mod.list_items(tree, ["label", "global_label"]),
                        key=lambda d: (natural_key(d.get("text") or ""), d["uuid"])):
            if d.get("text") in wanted_nets:
                continue
            item = {"text": d["text"], "kind": d["type"], "sheet": _sheet_label(sess, proj, path)}
            extras_labels.append(item)
            if prune:
                sch_mod.delete_item(tree, d["uuid"])
                pruned_labels.append(item)

    changed_paths = []
    for path in paths:
        before = sexpr.dumps(sess.tree(path), sexpr.mode_for_path(path))
        if sexpr.dumps(trees[path], sexpr.mode_for_path(path)) != before:
            changed_paths.append(path)

    if changed_paths:
        sess.snapshot("apply intent", changed_paths)
        for path in changed_paths:
            sess.doc(path).set_tree(trees[path])
    changes = (len(removed) + len(placed) + len(updated) + len(labels_added)
               + len(no_connects_added) + len(flags_added)
               + len(pruned_symbols) + len(pruned_labels))
    return {
        "changes": changes,
        "placed": placed,
        "updated": updated,
        "removed": removed,
        "labels_added": labels_added,
        "no_connects_added": no_connects_added,
        "power_flags": flags_added,
        "extras": {"symbols": extras_symbols, "labels": extras_labels},
        "pruned": {"symbols": pruned_symbols, "labels": pruned_labels},
        "changed_files": changed_paths,
    }


def _sheet_label(sess, proj, path: str) -> str:
    """KiCad's sheet path name for a file ("/" for the root), for output only."""
    for h in sch_mod.hierarchy(sess.tree, proj.sch_path):
        if os.path.abspath(h["file"]) == os.path.abspath(path):
            return h["sheet_path"]
    return "/"
