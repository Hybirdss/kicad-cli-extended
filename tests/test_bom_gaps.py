import csv
import json
import shutil
from pathlib import Path
from unittest.mock import patch

import pytest
from kicad_cli_extended.core import fab, bom
from test_board_gaps import package_session, exporter

read_components = bom.components


def test_required_lcsc_fails_before_export_and_keeps_accepted(package_session, tmp_path, monkeypatch):
    parts = [{'ref': 'R3', 'value': '1k', 'footprint': 'R0603', 'fields': {}}]
    monkeypatch.setattr(bom, 'components', lambda *a, **kw: parts)
    out = tmp_path / 'accepted'
    out.mkdir()
    (out / 'approved').write_text('accepted', encoding='utf-8')
    with patch.object(fab.kb, 'run_kicad_cli') as call:
        result = fab.make_package(package_session, 'jlcpcb', str(out), run_drc=False, require_lcsc=True)
    call.assert_not_called()
    assert not result['complete'] and result['bom']['missing_lcsc'] == ['R3']
    assert list(out.iterdir()) == [out / 'approved']
    assert not list(tmp_path.glob('.accepted-stage-*'))


def test_assembly_report_separates_differing_lcsc_and_ignores_dnp():
    parts = [dict(ref='R1', value='1k', footprint='R0603', fields={'LCSC': 'C1'}),
             dict(ref='R2', value='1k', footprint='R0603', fields={'LCSC': 'C2'}),
             dict(ref='R3', value='1k', footprint='R0603', fields={}, dnp=True)]
    report = bom.assembly_report(parts)
    assert report['missing_lcsc'] == []
    assert report['duplicate_values_differing_lcsc'][0]['parts'] == {'C1': ['R1'], 'C2': ['R2']}


@pytest.mark.e2e
def test_actual_bom_retains_mpn_and_missing_lcsc(tmp_path):
    from test_e2e import demos_dir, rj
    project = tmp_path / 'pic_programmer'
    shutil.copytree(Path(demos_dir()) / 'pic_programmer', project)
    rj(['--project', project, 'sch', 'set-field', 'C2', 'MPN', 'EEU-FR1V221'])
    result = rj(['--project', project, 'fab', 'package', '--preset', 'jlcpcb', '--no-drc', '--no-step'])
    assert result['complete'] and 'C2' in result['bom']['missing_lcsc'] and result['warnings']
    rows = list(csv.DictReader((Path(result['output_dir']) / 'pic_programmer-bom.csv').open(encoding='utf-8')))
    assert any(r['MPN'] == 'EEU-FR1V221' for r in rows)


def test_bom_check_cost_delta_missing_and_unpriced(package_session, tmp_path, monkeypatch):
    current = [{'ref': 'C2', 'value': '220u', 'footprint': 'Cap', 'fields': {'MPN': 'New', 'LCSC': 'NEW'}}]
    baseline = [{'ref': 'C2', 'value': '220u', 'footprint': 'Cap', 'fields': {'MPN': 'Old', 'LCSC': 'OLD'}}]
    from kicad_cli_extended.core import diff
    monkeypatch.setattr(diff, 'resolve_side', lambda *a: (package_session.project, None))
    monkeypatch.setattr(bom, 'components', lambda sess, variant=None: current if sess is package_session else baseline)
    prices = tmp_path / 'prices.csv'
    prices.write_text('LCSC,unit_price_usd\nOLD,1.00\nNEW,1.15\n', encoding='utf-8')
    result = bom.check(package_session, prices_file=str(prices), against='git:HEAD', max_delta_pct=10)
    assert result['delta_pct'] == 15.0 and result['over_budget'] and not result['ok']
    prices.write_text('LCSC,unit_price_usd\nOLD,1.00\n', encoding='utf-8')
    result = bom.check(package_session, prices_file=str(prices))
    assert result['unpriced'] == [{'ref': 'C2', 'key': 'NEW'}] and result['cost_usd'] is None and not result['ok']
    current[0]['fields'].pop('MPN')
    assert bom.check(package_session)['missing'] == [{'ref': 'C2', 'field': 'MPN'}]


def test_package_discloses_missing_fab_qualification_and_column_mapping(package_session, tmp_path, monkeypatch):
    monkeypatch.setattr(fab.checks_mod, 'run_drc', lambda *a, **kw: dict(total=0, errors=0, warnings=0, unconnected=0, clean=True, by_type={}))
    with patch.object(fab.kb, 'run_kicad_cli', side_effect=exporter()):
        result = fab.make_package(package_session, 'jlcpcb', str(tmp_path / 'release'))
    assert result['drc']['rules_preset'] is None
    assert any('No fab rules preset' in warning for warning in result['warnings'])
    preset = next(p for p in fab.list_presets() if p['name'] == 'jlcpcb')
    assert preset['bom_columns']['LCSC Part #'] == 'field LCSC'


def test_variant_applies_value_and_footprint_and_dnp(package_session, monkeypatch):
    from kicad_cli_extended.core import pcb
    raw = dict(ref='R1', value='1k', footprint='Old', fields={'MPN': 'base'},
               variants={'lite': {'fields': {'Value': '2k', 'Footprint': 'New', 'MPN': 'variant'}, 'dnp': True}})
    monkeypatch.setattr(pcb, 'schematic_netlist', lambda sess: {'components': [raw], 'variants': [{'name': 'lite'}]})
    # This fixture replaces components for export tests; exercise the real variant reader here.
    parts = read_components(package_session, 'lite')
    assert parts[0]['value'] == '2k' and parts[0]['footprint'] == 'New' and parts[0]['dnp']
    assert bom.evaluate(parts, ['MPN'])['dnp'] == 1
