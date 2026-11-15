"""Real stdio MCP checks for the Feishu-only administrator boundary."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from customer_gateway import install_admin_permissions
from nanobot.agent.tools.context import RequestContext, request_context
from nanobot.agent.tools.mcp import connect_mcp_servers
from nanobot.agent.tools.registry import ToolRegistry
from nanobot.config.schema import MCPServerConfig


class AdministratorTests(unittest.IsolatedAsyncioTestCase):
    async def test_private_whitelisted_admin_can_prepare_and_read_batches(self):
        install_admin_permissions()
        with tempfile.TemporaryDirectory() as directory, patch.dict('os.environ', {'FEISHU_ADMIN_IDS': 'admin'}):
            registry = ToolRegistry()
            config = MCPServerConfig(command=sys.executable, args=[str(
                Path('mcp_servers/fulfillment_server.py').resolve())],
                env={'FULFILLMENT_SIM_DB': str(Path(directory) / 'simulation.db')})
            connections = await connect_mcp_servers({'fulfillment_server': config}, registry)
            try:
                shops = registry.get('mcp_fulfillment_server_list_shipping_shops')
                prepare = registry.get('mcp_fulfillment_server_prepare_shipping_batch')
                get = registry.get('mcp_fulfillment_server_get_shipping_batch')
                self.assertIn('FORBIDDEN', str(await shops.execute()))
                private_admin = RequestContext(channel='feishu:ops', chat_id='admin', sender_id='admin',
                                               message_id='test-message', metadata={'chat_type': 'p2p'})
                with request_context(private_admin):
                    self.assertEqual(len(json.loads(await shops.execute())['shops']), 20)
                    first = json.loads(await prepare.execute(shop_ids=['mock-us-01','mock-us-02'], ship_date='2026-11-14'))
                    self.assertEqual(first['package_count'], 21)
                    self.assertEqual(first['excluded_reasons'], {'cancelled': 2, 'label_pending': 2})
                    self.assertEqual(json.loads(await get.execute(batch_id=first['batch_id'])), first)
                    retry = json.loads(await prepare.execute(shop_ids=['mock-us-02','mock-us-01'], ship_date='2026-11-14'))
                    self.assertEqual(retry, first)
                for channel, sender, chat, metadata in [
                    ('feishu','outsider','outsider',{'chat_type':'p2p'}),
                    ('feishu','admin','group',{'chat_type':'group'}),
                    ('untrusted','admin','admin',{'chat_type':'p2p'}),
                ]:
                    context = RequestContext(channel=channel, sender_id=sender, chat_id=chat,
                                             message_id='test-message', metadata=metadata)
                    with request_context(context):
                        for tool, args in [(shops, {}), (prepare, {'shop_ids':['mock-us-01'], 'ship_date':'2026-11-14'}),
                                           (get, {'batch_id':first['batch_id']})]:
                            self.assertIn('FORBIDDEN', str(await tool.execute(**args)))
            finally:
                for connection in connections.values():
                    await connection.aclose()
