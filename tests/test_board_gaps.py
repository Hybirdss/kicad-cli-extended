"""Scenario acceptance checks for board intake and manufacturing safety."""
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from kicad_cli_extended import cli
from kicad_cli_extended.core import fab
from kicad_cli_extended.core.session import Session
from kicad_cli_extended.core.project import KicadProject


@pytest.fixture
def package_session(tmp_path, monkeypatch):
    monkeypatch.setattr(fab.bom_mod, "components", lambda *a, **kw: [])
    root = tmp_path / 'project'
    root.mkdir()
    (root / 'p.kicad_pro').write_text('{}', encoding='utf-8')
    (root / 'p.kicad_pcb').write_text('(kicad_pcb (layers (0 "F.Cu" signal) (31 "B.Cu" signal)))', encoding='utf-8')
    (root / 'p.kicad_sch').write_text('(kicad_sch)', encoding='utf-8')
    sess = Session()
    sess.set_project(KicadProject(str(root / 'p.kicad_pro')))
    return sess


def exporter(fail=None, omit=None):
    def call(args, **kw):
        name = args[2]
        target = Path(args[args.index('-o') + 1])
        if name != omit and name != fail:
            if name == 'gerbers':
                target.mkdir(exist_ok=True)
                for layer in ('F_Cu', 'B_Cu'):
                    (target / (layer + '.gbr')).write_text('G04 test*\n%FSLAX46Y46*%\n', encoding='utf-8')
            elif name == 'drill':
                (target / 'p.drl').write_text('M48\nMETRIC\n', encoding='utf-8')
            elif name == 'pos':
                target.write_text('Ref,PosX,PosY,Rot,Side,Package,Val\nD1,1,2,0,top,LED,red\n', encoding='utf-8')
            elif name == 'bom':
                target.write_text('Comment,Designator,Footprint,LCSC Part #\nred,D1,LED,C123\n', encoding='utf-8')
        return {'returncode': int(name == fail), 'stderr': 'forced failure' if name == fail else '', 'stdout': ''}
    return call


@pytest.mark.parametrize('failure,omission', [('bom', None), ('pos', None), ('gerbers', None), (None, 'bom'), (None, 'drill')])
def test_package_preserves_accepted_and_discards_staging(package_session, tmp_path, failure, omission):
    out = tmp_path / 'accepted'
    out.mkdir()
    (out / 'approved.zip').write_bytes(b'accepted')
    with patch.object(fab.kb, 'run_kicad_cli', side_effect=exporter(failure, omission)):
        result = fab.make_package(package_session, 'jlcpcb', str(out), run_drc=False)
    assert result['complete'] is False
    assert ('bom' if omission == 'bom' else omission or failure) in result['failed_steps']
    assert {p.name: p.read_bytes() for p in out.iterdir()} == {'approved.zip': b'accepted'}
    assert not list(tmp_path.glob('.accepted-stage-*'))


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Existing-directory exchange is Linux-specific; fresh outputs are portable")
def test_package_success_replaces_generation(package_session, tmp_path):
    out = tmp_path / 'accepted'
    out.mkdir()
    (out / 'stale').write_text('old', encoding='utf-8')
    with patch.object(fab.kb, 'run_kicad_cli', side_effect=exporter()):
        result = fab.make_package(package_session, 'jlcpcb', str(out), run_drc=False)
    assert result['complete'] and result['failed_steps'] == []
    assert not (out / 'stale').exists()
    assert (out / result['zip']['path']).is_file()
    assert not list(tmp_path.glob('.accepted-stage-*'))


def test_failed_package_cli_keeps_result_and_exits_design(package_session, tmp_path, monkeypatch):
    monkeypatch.setattr(cli, '_session', package_session)
    with patch.object(fab.kb, 'run_kicad_cli', side_effect=exporter('bom')):
        result = CliRunner().invoke(cli.cli, ['--json', 'fab', 'package', '--preset', 'jlcpcb', '--no-drc', '-o', str(tmp_path / 'out')])
    assert result.exit_code == 3, result.output
    assert json.loads(result.stdout)['failed_steps'] == ['bom']
    assert json.loads(result.stderr)['category'] == 'design'


@pytest.mark.e2e
@pytest.mark.parametrize('custom', [False, True])
def test_route_uses_effective_netclass_width(tmp_path, custom):
    from test_e2e import run, rj
    project = tmp_path / 'route'
    run(['project', 'new', project, '--name', 'route'])
    P = ['--project', project]
    for ref, at in [('R1', '10,10'), ('R2', '20,10')]:
        run(P + ['pcb', 'add-footprint', 'Resistor_SMD:R_0805_2012Metric', '--ref', ref, '--at', at])
        run(P + ['pcb', 'set-pad-net', ref + ':1', 'SIGNAL'])
    path = project / 'route.kicad_pro'
    data = json.loads(path.read_text(encoding='utf-8'))
    data['net_settings']['meta'] = {'version': 5}
    data['net_settings']['classes'] = [{'name': 'Default', 'track_width': 0.2}]
    if custom:
        netclass = dict(data['net_settings']['classes'][0], name='Wide', track_width=0.42)
        data['net_settings']['classes'].append(netclass)
        data['net_settings']['netclass_patterns'] = [{'netclass': 'Wide', 'pattern': 'SIGNAL'}]
        width = 0.42
    else:
        width = data['net_settings']['classes'][0]['track_width']
    path.write_text(json.dumps(data), encoding='utf-8')
    before = rj(P + ['pcb', 'connectivity'])
    result = rj(P + ['pcb', 'route', 'R1:1', 'R2:1'])
    assert result['width'] == pytest.approx(width)
    after = rj(P + ['pcb', 'connectivity'])
    assert after['unrouted_connections'] < before['unrouted_connections']
    tracks = rj(P + ['pcb', 'tracks'])
    assert tracks and all(t['width'] == pytest.approx(width) for t in tracks)



def test_publish_preserves_accepted_when_exchange_is_unavailable(tmp_path, monkeypatch):
    stage, target = tmp_path / 'stage', tmp_path / 'target'
    stage.mkdir()
    target.mkdir()
    (target / 'accepted').write_text('keep', encoding='utf-8')
    monkeypatch.setattr(fab.sys, 'platform', 'unsupported')
    with pytest.raises(FileExistsError, match='fresh --output-dir'):
        fab._publish(str(stage), str(target))
    assert (target / 'accepted').read_text(encoding='utf-8') == 'keep'
    fresh = tmp_path / 'fresh'
    fab._publish(str(stage), str(fresh))
    assert fresh.is_dir() and not stage.exists()
