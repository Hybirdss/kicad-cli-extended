"""Enforce read-only and dry-run at the command boundary, before callbacks execute."""
from __future__ import annotations

import contextlib
import json
import os
import shutil
import tempfile
from pathlib import Path

import click

from . import contract

_sandbox = False


def normalize_globals(args):
    """Global switches work before or after a subcommand (values remain paired)."""
    leading, rest = [], []
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == '--':
            rest.extend(args[i:])
            break
        if arg in {'--json', '--dry-run', '--read-only', '--no-validate', '--progress'}:
            leading.append(arg)
        elif arg in {'--project', '-p', '--session-dir'}:
            leading.append(arg)
            if i + 1 < len(args):
                i += 1
                leading.append(args[i])
        elif arg.startswith(('--project=', '--session-dir=')):
            leading.append(arg)
        else:
            rest.append(arg)
        i += 1
    return leading + rest


class SafeGroup(click.Group):
    def parse_args(self, ctx, args):
        args = normalize_globals(args)
        ctx.meta['argv'] = args.copy()
        return super().parse_args(ctx, args)

    def invoke(self, ctx):
        from .. import cli as m
        m._json_output = ctx.params.get('use_json', False)
        argv = ctx.meta['argv']
        # Only parse subcommands; invoking the root callback would resolve the real project too early.
        try:
            leaf, path = contract.resolve_context_plain(self, argv)
            effect = contract.effect(path, leaf.params)
            if ctx.params.get('read_only') and effect != 'read':
                raise ValueError(f"Read-only mode refuses {effect} command {path!r}; inspect `kce info describe {path}`.")
            if (ctx.params.get('dry_run') or ctx.params.get('read_only')) and not _sandbox and path not in {'skill install', 'skill uninstall'}:
                result = rehearse(m, ctx, leaf, path, argv)
                if ctx.params.get('read_only') and isinstance(result, dict):
                    for key in ('dry_run', 'would_write', 'would_save'):
                        result.pop(key, None)
                m.output(result)
                return result
            result = super().invoke(ctx)
            from .runtime import current
            request = current.get()
            if request:
                request.meta['commit_state'] = 'unchanged' if effect == 'read' or ctx.params.get('dry_run') else 'committed'
            return result
        except click.ClickException:
            raise
        except Exception as exc:
            def fail():
                raise exc
            return m.handle_error(fail)()


def _files(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in Path(root).rglob('*') if p.is_file() and not p.is_symlink()}


def rehearse(m, ctx, leaf, path, argv):
    """Run on copies, remapping explicit filesystem parameters and all output destinations.

    Global/UI/arbitrary-code operations are refused: they cannot be isolated by copying a project.
    """
    global _sandbox
    from . import project as projects
    if (path in contract.CONTROL_COMMANDS or path in {'lib init-global-tables', 'raw pcbnew', 'lib index',
        'skill install', 'skill uninstall', 'sim run'} or leaf.params.get('scope') == 'global'):
        raise ValueError(f"Dry-run cannot isolate {path}; no action was taken. See `kce info describe {path}`.")
    target = ctx.params.get('project_path') or m.get_session().project_path
    plan_data = None
    if path == 'run':
        from .plan import load_plan
        plan_data = load_plan(leaf.params['plan'])
        target = plan_data.get('project') or target
    try:
        project = projects.resolve_project(target)
    except (FileNotFoundError, ValueError):
        if target:
            raise
        project = None
    with tempfile.TemporaryDirectory(prefix='kce-dry-run-') as td:
        scratch = Path(td) / 'project'
        scratch.mkdir()
        original = Path(project.dir).resolve() if project else Path.cwd().resolve()
        mapping = {str(scratch): str(original)}
        if project:
            # Copy regular files, never symlink a writable byte back to the original.
            def ignore(directory, names):
                return [n for n in names if n in {'.git', '__pycache__'} or Path(directory, n).is_symlink()]
            shutil.copytree(project.dir, scratch, dirs_exist_ok=True, ignore=ignore)
            for doc in m.get_session().dirty_docs():
                rel = os.path.relpath(doc.path, project.dir)
                if not rel.startswith('..'):
                    p = scratch / rel
                    p.parent.mkdir(parents=True, exist_ok=True)
                    p.write_bytes(doc.bytes())
        external = Path(td) / 'external'
        external.mkdir()
        def remap(value, *, role='input'):
            if not isinstance(value, str):
                return value
            absolute = Path(os.path.abspath(value))
            try:
                dest = scratch / absolute.relative_to(original)
            except ValueError:
                existing = next((temp for temp, real in mapping.items() if real == str(absolute)), None)
                dest = Path(existing) if existing else external / role / str(len(mapping)) / absolute.name
                mapping[str(dest)] = str(absolute)
            # Existing output bytes are part of the rehearsal baseline too: native
            # overwrite checks must see the same state as a real invocation.
            if not dest.exists():
                if absolute.is_file():
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(absolute, dest)
                elif absolute.is_dir():
                    shutil.copytree(absolute, dest, symlinks=False)
            return str(dest)
        args = []
        for p in leaf.command.params:
            value = leaf.params.get(p.name)
            if value is None or p.name in {'help'}:
                continue
            is_path = isinstance(p.type, click.Path) or p.name in {
                'out', 'output', 'output_dir', 'root_dir', 'file', 'filename', 'directory', 'dir',
                'path', 'lib_file', 'library_file', 'dest', 'source', 'plan', 'intent_file', 'expect'}
            is_path = is_path or any(flag.endswith(('-file', '-dir', '-path', '-directory')) for flag in p.opts)
            is_path = is_path or bool(set(p.opts) & {'--output', '-o', '--file', '--report-file', '--output-dir', '--root-dir', '--3d-models-dir'})
            # Enumerated selectors (e.g. skill show --file) are never filesystem paths.
            is_path = is_path and not isinstance(p.type, click.Choice)
            output_path = p.name in {'out', 'output', 'output_dir', 'dest', 'directory', 'dir'}
            output_path = output_path or bool(set(p.opts) & {'--output', '-o', '--report-file', '--output-dir'})
            output_path = output_path and contract.effect(path, leaf.params) != 'read'
            # An existing-path constraint explicitly marks an input even for names like --dir.
            if isinstance(p.type, click.Path) and p.type.exists:
                output_path = False
            values = list(value) if isinstance(value, (tuple, list)) else [value]
            if p.name == 'fpid' and path.startswith('lib fp '):
                values = [remap(m.lib_mod.resolve_footprint(project, v)[1]) for v in values]
            elif p.name == 'copy_into':
                values = [remap(str(original / v), role='output') for v in values]
            elif is_path:
                values = [remap(v, role='output' if output_path else 'input') for v in values]
            if isinstance(p, click.Argument):
                args.extend(str(v) for v in values)
            elif p.is_flag:
                if value:
                    args.append(p.opts[0])
                elif p.secondary_opts and p.default is True:
                    args.append(p.secondary_opts[0])
            else:
                for v in values:
                    args.extend([p.opts[0], str(v)])
        if plan_data is not None:
            # Each step is parsed and remapped by its own dry-run invocation only after
            # the project tree has been copied; arbitrary external plan paths are refused.
            plan_data.pop('project', None)
            for step in plan_data['steps']:
                step['args'] = [a.replace(str(original), str(scratch)) for a in step['args']]
            pp = Path(td) / 'plan.json'
            pp.write_text(json.dumps(plan_data), encoding='utf-8')
            args = [str(pp)]
        # Jobset destinations are stored in project files rather than CLI arguments.
        if path in {'jobset run', 'jobset execute'}:
            for jobset in Path(td).rglob('*.kicad_jobset'):
                data = json.loads(jobset.read_text(encoding='utf-8'))
                for job in data.get('jobs', []):
                    if job.get('type', '').startswith('special_'):
                        raise ValueError('Dry-run refuses jobsets with external commands or file-copy jobs; no action was taken')
                    for key in ('output_dir', 'output_filename', 'vrml_model_dir'):
                        value = job.get('settings', {}).get(key)
                        if isinstance(value, str) and (os.path.isabs(value) or '..' in Path(value).parts):
                            job['settings'][key] = remap(value if os.path.isabs(value) else str(original / value))
                for output in data.get('outputs', []):
                    settings = output.get('settings', {})
                    value = settings.get('output_path')
                    if value and (os.path.isabs(value) or '..' in Path(value).parts):
                        settings['output_path'] = remap(value if os.path.isabs(value) else str(original / value))
                jobset.write_text(json.dumps(data), encoding='utf-8')
        from .session import state_directory
        original_state = state_directory(str(original), ctx.params.get('session_dir') or m.get_session().session_dir)
        copied_state = state_directory(str(scratch), str(Path(td) / 'session'))
        if os.path.isdir(original_state):
            shutil.copytree(original_state, copied_state, dirs_exist_ok=True)
        mapping[copied_state] = original_state
        before = _files(td)
        previous_cache = m.lib_mod.CACHE_DIR
        previous, previous_cwd = _sandbox, os.getcwd()
        previous_session_dir = os.environ.get('KCE_SESSION_DIR')
        try:
            _sandbox = True
            m.lib_mod.CACHE_DIR = str(Path(td) / 'cache')
            os.environ['KCE_SESSION_DIR'] = str(Path(td) / 'session')
            os.chdir(scratch)
            command_args = [*path.split(), *args]
            if ctx.params.get('progress'):
                command_args.insert(0, '--progress')
            if ctx.params.get('no_validate'):
                command_args.insert(0, '--no-validate')
            result = m.run_in_process(command_args, str(scratch / (project.name + '.kicad_pro')) if project else None)
        finally:
            _sandbox = previous
            m.lib_mod.CACHE_DIR = previous_cache
            os.chdir(previous_cwd)
            if previous_session_dir is None:
                os.environ.pop('KCE_SESSION_DIR', None)
            else:
                os.environ['KCE_SESSION_DIR'] = previous_session_dir
        def translate(value):
            if isinstance(value, str):
                for temp, real in sorted(mapping.items(), key=lambda x: -len(x[0])):
                    value = value.replace(temp, real)
                return value
            if isinstance(value, list):
                return [translate(v) for v in value]
            if isinstance(value, dict):
                out = {k: translate(v) for k, v in value.items()}
                if 'dry_run' in out:
                    out['dry_run'] = True
                return out
            return value
        after = _files(td)
        changed = [translate(str(Path(td) / rel)) for rel in sorted(before.keys() | after.keys())
                   if before.get(rel) != after.get(rel) and not rel.startswith('session/')
                   and '/.kicad-cli-extended/' not in '/' + rel]
        result = translate(result)
        if isinstance(result, dict) and 'fpid' in result and leaf.params.get('fpid'):
            result['fpid'] = leaf.params['fpid']
        if contract.effect(path, leaf.params) == 'read':
            return result
        if isinstance(result, list):
            return [dict(item, dry_run=True, would_write=changed, would_save=changed)
                    if isinstance(item, dict) else item for item in result]
        if not isinstance(result, dict):
            result = {'result': result}
        result.update(dry_run=True, would_write=changed, would_save=changed)
        if path == 'run':
            result['saved'] = []
        return result
