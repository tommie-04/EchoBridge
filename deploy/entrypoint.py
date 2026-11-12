"""Seed instance configuration once, then run the gateway or a setup command."""
from __future__ import annotations

import os
from pathlib import Path
import sys


def prepare_runtime(config: Path, template: Path) -> None:
    config.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if config.exists():
        if not config.is_file():
            raise RuntimeError('Runtime config path must be a regular file.')
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
