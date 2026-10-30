"""Command discovery and effects, shared by help, documentation and safety boundaries."""
from __future__ import annotations

import click

# Unknown extensions default to write, so they cannot silently bypass read-only mode.
READ_COMMANDS = set('''
errors
check diagnose|check drc|check erc
fab presets|fab bom-check|fab parts check
gui selection|gui status
info commands|info describe|info schema|info doctor|info kicad-version|info paths|info version
jobset list|jobset show|jobset type-info|jobset types
lib fp libs|lib fp list|lib fp search|lib fp show|lib fp check|lib fp model list|lib fp wizard-params|lib fp wizards
lib sym libs|lib sym list|lib sym search|lib sym show|lib suggest-footprints|lib link-check
pcb component-classes|pcb connectivity|pcb diffpair list|pcb drawings|pcb footprint|pcb footprints|pcb info|pcb layers|pcb length report|pcb models|pcb net-ties|pcb nets|pcb ratsnest|pcb score|pcb stackup|pcb tracks|pcb zones
preview latest|preview live status|preview recipes
project gitignore|project compare|project files|project get|project info|project libtable list|project netclass list|project rules custom-show|project rules show|project templates|project textvar list|project variant list
raw check|raw query
sch at|sch components|sch hierarchy|sch info|sch items|sch net-of|sch nets|sch pins|sch show|sch symbols|sch verify
session checkpoints|session history|session status
sim directives|sim engine|sim models
skill path|skill show|skill status
'''.replace('\n', '|').split('|')) - {''}
CONTROL_COMMANDS = {'serve', 'repl', 'gui action', 'gui reload', 'gui select', 'project open', 'session discard'}
EXPORT_COMMANDS = {'fab package', 'jobset execute', 'jobset run', 'pcb dsn', 'pcb panelize', 'pcb render',
                   'preview capture', 'preview diff', 'preview live start', 'preview live push',
                   'preview live stop', 'project archive', 'sim netlist', 'sim run'}
SCHEMAS = {'run': 'run-result', 'sch apply': 'sch-apply', 'sch verify': 'sch-verify',
           'sch nets': 'sch-nets', 'pcb score': 'pcb-score', 'serve': 'rpc'}
EXAMPLES = {
    'run': 'kce --json -p P run stage.json',
    'sch apply': 'kce --json -p P --dry-run sch apply intent.json --power-flags',
    'sch verify': 'kce --json -p P sch verify --expect intent.json --strict',
    'sch no-connect': 'kce --json -p P sch no-connect J1:6',
    'session checkpoint': 'kce --json -p P session checkpoint before-eco',
    'session restore': 'kce --json -p P session restore before-eco',
    'pcb score': 'kce --json -p P pcb score',
    'pcb legalize': 'kce --json -p P --dry-run pcb legalize',
    'pcb place near': 'kce --json -p P pcb place near C1 --to U1 --max-dist 5',
    'pcb update': 'kce --json -p P --dry-run pcb update --stable-uuids',
    'serve': 'kce --json -p P serve --request-timeout-ms 30000',
}


def leaves(command, path=()):
    if isinstance(command, click.Group):
        for name, sub in sorted(command.commands.items()):
            yield from leaves(sub, (*path, name))
    else:
        yield ' '.join(path), command


def effect(path, params=None):
    params = params or {}
    if params.get('show_command'):
        return 'read'
    if path == 'project gitignore' and params.get('append_'):
        return 'write'
    if path in CONTROL_COMMANDS:
        return 'control'
    if path in EXPORT_COMMANDS or ' export ' in path or path in {'pcb drc', 'sch erc'}:
        return 'export'
    if path in READ_COMMANDS:
        if params.get('out') or params.get('output') or params.get('save_board'):
            return 'write' if params.get('save_board') else 'export'
        return 'read'
    return 'write'


def describe(path, command):
    args = []
    example = ['kce', '--json', *path.split()]
    for p in command.params:
        flags = list(p.opts) if isinstance(p, click.Option) else []
        args.append({'name': p.opts[0].lstrip('-').replace('-', '_') if flags and p.name.startswith('kc_') else p.name, 'flags': flags, 'type': p.type.name, 'required': p.required,
                     'default': p.default if isinstance(p.default, (str, int, float, bool, list, tuple, dict, type(None))) else None,
                     'choices': list(p.type.choices) if isinstance(p.type, click.Choice) else None,
                     'help': getattr(p, 'help', None) or ''})
        if p.required:
            value = str(p.type.choices[0]) if isinstance(p.type, click.Choice) else p.name.upper()
            example.extend(([p.opts[0], value] if flags else [value]))
    return {'command': path, 'help': command.help or command.short_help or '', 'effect': effect(path),
            'args': args, 'returns': {'schema': f'schemas/{SCHEMAS[path]}.schema.json'} if path in SCHEMAS
            else {'format': 'JSON', 'description': 'Command result; see help for fields.'},
            'example': EXAMPLES.get(path, ' '.join(example)),
            'exit_codes': {'internal': 1, 'input': 2, 'design': 3, 'validation': 4,
                           'crash': 5, 'timeout': 6, 'environment': 7}}


def resolve_context_plain(cli, args):
    """Parse without invoking callbacks; return the leaf context and its command path."""
    ctx = click.Context(cli, info_name='kce')
    click.Group.parse_args(cli, ctx, list(args))
    path = []
    while isinstance(ctx.command, click.Group):
        rest = [*ctx._protected_args, *ctx.args]
        if not rest:
            break
        name, sub, remaining = ctx.command.resolve_command(ctx, rest)
        path.append(name)
        ctx = sub.make_context(name, remaining, parent=ctx)
    return ctx, ' '.join(path)
