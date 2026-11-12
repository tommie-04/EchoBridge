"""Check initialization does not destroy persisted instance configuration."""
import json
from importlib import import_module
from importlib.metadata import version
from pathlib import Path
import tempfile
import unittest

from deploy.entrypoint import prepare_runtime
from packaging.requirements import Requirement


class ContainerInitializationTests(unittest.TestCase):
    def test_enabled_channels_have_their_official_optional_dependencies(self):
        template = Path(__file__).resolve().parents[1] / 'deploy/config.docker.json'
        channels = json.loads(template.read_text())['channels']
        for name, config in channels.items():
            if not isinstance(config, dict) or not config.get('enabled'):
                continue
            plugin = import_module(f'nanobot.channels.{name}.manifest').PLUGIN
            for dependency in plugin.dependencies:
                requirement = Requirement(dependency)
                if requirement.marker and not requirement.marker.evaluate():
                    continue
                with self.subTest(channel=name, dependency=requirement.name):
                    self.assertIn(version(requirement.name), requirement.specifier)

    def test_restart_preserves_login_and_changed_channel_settings(self):
        template = Path(__file__).resolve().parents[1] / 'deploy/config.docker.json'
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / 'runtime/config.json'
            prepare_runtime(config, template)
            saved = json.loads(config.read_text())
            saved['channels']['weixin']['token'] = 'test-login-state'
            saved['channels']['qq']['enabled'] = False
            config.write_text(json.dumps(saved))
            prepare_runtime(config, template)
            self.assertEqual(json.loads(config.read_text()), saved)
            self.assertEqual(config.stat().st_mode & 0o777, 0o600)

    def test_directory_at_config_path_fails_instead_of_starting_empty_instance(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / 'config.json'
            config.mkdir()
            with self.assertRaises((RuntimeError, IsADirectoryError)):
                prepare_runtime(config, Path(directory) / 'missing-template.json')
