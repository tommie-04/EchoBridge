"""Seed instance configuration once, then run the gateway or a setup command."""
from __future__ import annotations

import os
import json
from pathlib import Path
import sys
import tempfile
import shutil


def migrate_admin_config(config: Path, template: Path) -> None:
    """Retain Feishu credentials while retiring earlier channel and MCP settings."""
    current = json.loads(config.read_text(encoding='utf-8'))
    desired = json.loads(template.read_text(encoding='utf-8'))
    old_channels = current.get('channels', {})
    # Existing container volumes may contain a retired channel's login directory.
    for name, settings in old_channels.items():
        if name == 'feishu' or not isinstance(settings, dict) or not settings.get('stateDir'):
            continue
        state = Path(settings['stateDir'])
        if state.is_absolute() and state.resolve().is_relative_to(config.parent.resolve()) and state.resolve() != config.parent.resolve():
            if state.is_dir() and not state.is_symlink():
                shutil.rmtree(state)
            elif state.exists() or state.is_symlink():
                state.unlink()
    pairing = config.parent / 'pairing.json'
    if pairing.is_file():
        records = json.loads(pairing.read_text(encoding='utf-8'))
        records['approved'] = {key: value for key, value in records.get('approved', {}).items()
                               if key.split(':', 1)[0] == 'feishu'}
        records['pending'] = {key: value for key, value in records.get('pending', {}).items()
                              if isinstance(value, dict) and str(value.get('channel', '')).split(':', 1)[0] == 'feishu'}
        pairing.write_text(json.dumps(records, indent=2) + '\n', encoding='utf-8')
        pairing.chmod(0o600)
    channels = {key: value for key, value in old_channels.items()
                if key == 'feishu' or not isinstance(value, dict)}
    channels.setdefault('feishu', desired['channels']['feishu'])
    for flag in ('sendProgress', 'sendToolHints', 'showReasoning'):
        channels[flag] = False
    current['channels'] = channels
    tools = current.setdefault('tools', {})
    servers = tools.get('mcpServers', {})
    tools['mcpServers'] = {'fulfillment_server': servers.get(
        'fulfillment_server', desired['tools']['mcpServers']['fulfillment_server'])}
    content = json.dumps(current, indent=2, ensure_ascii=False) + '\n'
    if content == config.read_text(encoding='utf-8'):
        return
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=config.parent,
                                         delete=False) as target:
            temporary = Path(target.name)
            target.write(content)
        temporary.chmod(0o600)
        os.replace(temporary, config)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def prepare_runtime(config: Path, template: Path) -> None:
    config.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if config.exists():
        if not config.is_file():
            raise RuntimeError('Runtime config path must be a regular file.')
        migrate_admin_config(config, template)
        return
    content = template.read_text(encoding='utf-8')
    try:
        # Preserve login credentials, pairing changes and channel settings on restart.
        with config.open('x', encoding='utf-8') as target:
            target.write(content)
        config.chmod(0o600)
    except FileExistsError:
        if not config.is_file():
            raise RuntimeError('Runtime config path must be a regular file.')


def main() -> None:
    prepare_runtime(
        Path(os.environ.get('CUSTOMER_SUPPORT_CONFIG', '/runtime/config.json')),
        Path('/app/deploy/config.docker.json'),
    )
    if len(sys.argv) < 2:
        raise SystemExit('A gateway or setup command is required.')
    os.execvp(sys.argv[1], sys.argv[1:])


if __name__ == '__main__':
    main()
