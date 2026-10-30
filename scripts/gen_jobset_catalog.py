#!/usr/bin/env python3
"""Regenerate src/kicad_cli_extended/utils/jobset_catalog.json from KiCad source (common/jobs/*.cpp|h).

Usage: gen_jobset_catalog.py <kicad-src-root> [out.json]
(the KiCad source tree is fetched by scripts/fetch_fixtures.py into .fixtures/kicad-src)
Extracts every REGISTER_JOB type, its class hierarchy, JSON setting keys
(JOB_PARAM* declarations, including inherited ones) and enum string values.
"""
import glob, json, os, re, sys

root = sys.argv[1]
out = sys.argv[2] if len(sys.argv) > 2 else os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src",
                                                          "kicad_cli_extended", "utils", "jobset_catalog.json")
jobs_dir = os.path.join(root, "common", "jobs")
src = {}
for f in glob.glob(os.path.join(jobs_dir, "*.cpp")) + glob.glob(os.path.join(jobs_dir, "*.h")):
    with open(f, encoding="utf-8", errors="replace") as fh:
        src[f] = fh.read()
alltext = "\n".join(src.values())

bases = dict(re.findall(r"class\s+(?:KICOMMON_API\s+)?(\w+)\s*:\s*public\s+(\w+)", alltext))
enums = {}
for m in re.finditer(r"NLOHMANN_JSON_SERIALIZE_ENUM\(\s*([\w:]+)\s*,\s*\{(.*?)\}\s*\)", alltext, re.S):
    vals = re.findall(r'\{\s*[\w:]+\s*,\s*"([^"]*)"\s*\}', m.group(2))
    enums[m.group(1)] = vals

def params_for(cls):
    """JOB_PARAM declarations inside the constructor of cls."""
    res = []
    for f, t in src.items():
        if not f.endswith(".cpp"):
            continue
        m = re.search(cls + r"::" + cls + r"\s*\(.*?\)\s*:?(.*?)\n\}", t, re.S)
        if not m:
            continue
        body = m.group(0)
        for pm in re.finditer(r'new\s+(JOB_PARAM\w*)\s*(?:<\s*([\w:<>, ]+?)\s*>)?\s*\(\s*"([^"]+)"', body):
            kind, typ, name = pm.group(1), (pm.group(2) or "").strip(), pm.group(3)
            p = {"key": name, "param": kind, "type": typ}
            for cand in ([typ] if "::" in typ else [f"{cls}::{typ}", typ]):
                if cand in enums:
                    p["choices"] = enums[cand]
                    break
            res.append(p)
    return res

catalog = {}
for m in re.finditer(r"REGISTER_(DEPRECATED_)?JOB\(\s*(\w+)\s*,\s*_(?:HKI)?\(\s*\"([^\"]+)\"\s*\)\s*,\s*KIWAY::(\w+)\s*,\s*(\w+)\s*\)", alltext):
    dep, name, title, face, cls = m.groups()
    chain, c = [], cls
    while c and c not in chain:
        chain.append(c)
        c = bases.get(c)
    params, seen = [], set()
    for c in reversed(chain):
        for p in params_for(c):
            if p["key"] not in seen:
                seen.add(p["key"])
                params.append(p)
    catalog[name] = {"title": title, "face": face, "class": cls, "deprecated": bool(dep),
                     "class_chain": chain, "settings": params}
with open(out, "w", encoding="utf-8", newline="\n") as fh:
    json.dump({"generated_from": "KiCad common/jobs (tag 10.0.7)", "jobs": catalog,
               "outputs": {"folder": ["output_path"], "archive": ["output_path", "format"]}},
              fh, indent=1, sort_keys=True)
print(f"{len(catalog)} job types -> {out}")
for k, v in sorted(catalog.items()):
    print(k, len(v["settings"]), [p["key"] for p in v["settings"]][:6])
