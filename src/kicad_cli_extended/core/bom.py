"""Assembly field checks from KiCad's actual netlist, without supplier network calls."""
from __future__ import annotations

from collections import defaultdict


def components(sess, variant=None):
    from .pcb import schematic_netlist
    proj = sess.require_project()
    if not proj.has_sch() and not sess.has_doc(proj.sch_path):
        return []
    netlist = schematic_netlist(sess)
    if variant and variant not in {v['name'] for v in netlist.get('variants', [])}:
        raise ValueError(f"Unknown BOM variant {variant!r}; see project variant list")
    result = []
    for component in netlist['components']:
        c = dict(component, fields=dict(component.get('fields') or {}))
        if variant:
            override = c.get('variants', {}).get(variant, {})
            c.update({k: v for k, v in override.items() if k != 'fields'})
            c['fields'].update(override.get('fields') or {})
            c['value'] = c['fields'].get('Value', c['value'])
            c['footprint'] = c['fields'].get('Footprint', c['footprint'])
        if not c['ref'].startswith('#') and not c.get('exclude_from_bom') and not c.get('exclude_from_board'):
            result.append(c)
    return sorted(result, key=lambda c: c['ref'])


def assembly_report(parts):
    parts = [c for c in parts if not c.get('dnp')]
    missing = [c['ref'] for c in parts if not c['fields'].get('LCSC', '').strip()]
    groups = defaultdict(lambda: defaultdict(list))
    for c in parts:
        groups[(c['value'], c['footprint'])][c['fields'].get('LCSC', '')].append(c['ref'])
    duplicates = [{'value': value, 'footprint': fp, 'parts': dict(sorted(entries.items()))}
                  for (value, fp), entries in sorted(groups.items()) if len(entries) > 1]
    return {'rows': None, 'missing_lcsc': missing, 'duplicate_values_differing_lcsc': duplicates}


def parts_check(sess):
    parts = [c for c in components(sess) if not c.get('dnp')]
    return {'offline': True, 'count': len(parts), 'items': [
        {'ref': c['ref'], 'lcsc': c['fields'].get('LCSC') or None, 'footprint': c['footprint'],
         'lcsc_url': ('https://www.lcsc.com/search?q=' + c['fields']['LCSC']) if c['fields'].get('LCSC') else None}
        for c in parts]}


def load_prices(path, key):
    import csv
    import math
    prices = {}
    with open(path, encoding='utf-8-sig', newline='') as file:
        reader = csv.DictReader(file)
        if key not in (reader.fieldnames or []) or 'unit_price_usd' not in (reader.fieldnames or []):
            raise ValueError(f'Prices CSV requires {key} and unit_price_usd columns')
        for row in reader:
            part = row[key].strip()
            price = float(row['unit_price_usd'])
            if not part or not math.isfinite(price) or price < 0 or part in prices:
                raise ValueError('Prices require unique nonempty part IDs and finite non-negative unit_price_usd')
            prices[part] = price
    return prices


def evaluate(parts, required, prices=None, price_key='LCSC'):
    missing, unpriced, rows = [], [], []
    cost = 0.0
    for c in parts:
        if c.get('dnp'):
            continue
        fields = dict(c['fields'], Reference=c['ref'], Value=c['value'], Footprint=c['footprint'])
        for field in required:
            if not str(fields.get(field) or '').strip():
                missing.append({'ref': c['ref'], 'field': field})
        rows.append((c['value'], c['footprint'], tuple(sorted(c['fields'].items()))))
        if prices is not None:
            part = str(fields.get(price_key) or '').strip()
            if part not in prices:
                unpriced.append({'ref': c['ref'], 'key': part})
            else:
                cost += prices[part]
    return {'lines': len(set(rows)), 'dnp': sum(bool(c.get('dnp')) for c in parts), 'missing': missing,
            'duplicates': assembly_report(parts)['duplicate_values_differing_lcsc'], 'unpriced': unpriced,
            'cost_usd': round(cost, 6) if prices is not None and not unpriced else None}


def check(sess, required=('MPN', 'LCSC', 'Footprint'), prices_file=None, price_key='LCSC',
          against=None, max_delta_pct=None, variant=None):
    import math
    import shutil
    from . import diff
    from .session import Session
    if max_delta_pct is not None and (not math.isfinite(max_delta_pct) or max_delta_pct < 0 or not prices_file or not against):
        raise ValueError('--max-delta-pct requires --prices and --against, and must be finite and non-negative')
    project = sess.require_project()
    if not project.has_sch() and not sess.has_doc(project.sch_path):
        raise FileNotFoundError('BOM check needs a schematic; a board-only import has no assembly netlist')
    prices = load_prices(prices_file, price_key) if prices_file else None
    result = evaluate(components(sess, variant), required, prices, price_key)
    result.update(variants=[variant] if variant else [], base_cost_usd=None, delta_pct=None, over_budget=False)
    if against:
        project, cleanup = diff.resolve_side(against, sess.require_project())
        try:
            baseline = Session()
            baseline.set_project(project)
            base = evaluate(components(baseline, variant), (), prices, price_key)
            result['base_cost_usd'] = base['cost_usd']
            result['base_unpriced'] = base['unpriced']
            if base['cost_usd'] is not None and result['cost_usd'] is not None:
                old, new = base['cost_usd'], result['cost_usd']
                result['delta_pct'] = round((new - old) / old * 100, 6) if old else (0.0 if not new else None)
                result['over_budget'] = max_delta_pct is not None and (result['delta_pct'] is None or result['delta_pct'] > max_delta_pct)
        finally:
            if cleanup:
                shutil.rmtree(cleanup, ignore_errors=True)
    result['ok'] = not (result['missing'] or result['unpriced'] or result.get('base_unpriced') or result['over_budget'])
    return result
