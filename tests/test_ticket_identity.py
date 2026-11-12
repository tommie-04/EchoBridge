"""Ensure the gateway binds identities before forwarding ticket operations."""
import asyncio
import json
from pathlib import Path
import sys
import tempfile
import unittest

from customer_gateway import install_ticket_identity_binding
from nanobot.agent.tools.context import RequestContext, request_context
from nanobot.agent.tools.mcp import connect_mcp_servers
from nanobot.agent.tools.registry import ToolRegistry
from nanobot.config.schema import MCPServerConfig


class IdentityTests(unittest.IsolatedAsyncioTestCase):
    async def test_bound_sender_cannot_impersonate_other_customer(self):
        install_ticket_identity_binding()
        with tempfile.TemporaryDirectory() as directory:
            registry = ToolRegistry()
            config = MCPServerConfig(command=sys.executable, args=[
                str(Path('mcp_servers/ticket_server.py').resolve()),
                '--db', str(Path(directory)/'test.db'), 'serve'])
            connections = await connect_mcp_servers({'ticket_server':config},registry)
            try:
                create = registry.get('mcp_ticket_server_create_ticket')
                get = registry.get('mcp_ticket_server_get_ticket')
                for tool in [create,get]:
                    self.assertNotIn('user_id',tool.parameters['properties'])
                    self.assertNotIn('platform',tool.parameters['properties'])
                self.assertNotIn('request_id',create.parameters['properties'])
                args = dict(title='杯子破损',description='需要人工核实')
                self.assertIn('AUTH_CONTEXT_REQUIRED',await create.execute(**args))
                alice = RequestContext(channel='feishu:assistant-1',chat_id='group-1',
                    sender_id='alice',message_id='message-1')
                bob = RequestContext(channel='feishu:assistant-1',chat_id='group-1',
                    sender_id='bob',message_id='message-2')
                with request_context(alice):
                    first = json.loads(await create.execute(**args,user_id='forged',platform='telegram',request_id='forged'))
                    retry = json.loads(await create.execute(**args,request_id='another-forged-key'))
                ticket = first['ticket']
                self.assertEqual(ticket['user_id'],'alice')
                self.assertEqual(ticket['platform'],'feishu')
                self.assertFalse(retry['created'])
                with request_context(bob):
                    denied = json.loads(await get.execute(ticket_id=ticket['ticket_id'],user_id='alice',platform='feishu'))
                self.assertEqual(denied['error']['code'],'NOT_FOUND')
                with request_context(alice):
                    allowed = json.loads(await get.execute(ticket_id=ticket['ticket_id']))
                self.assertTrue(allowed['success'])
                telegram_alice = RequestContext(channel='telegram',chat_id='alice',sender_id='alice',message_id='message-3')
                with request_context(telegram_alice):
                    denied = json.loads(await get.execute(ticket_id=ticket['ticket_id']))
                self.assertEqual(denied['error']['code'],'NOT_FOUND')
                async def read_as(context):
                    with request_context(context):
                        await asyncio.sleep(0)
                        return json.loads(await get.execute(ticket_id=ticket['ticket_id']))['success']
                self.assertEqual(await asyncio.gather(read_as(alice),read_as(bob)),[True,False])
                for channel in ['qq','weixin']:
                    customer = RequestContext(channel=channel,chat_id='channel-user',sender_id='channel-user',message_id='channel-message')
                    with request_context(customer):
                        own = json.loads(await create.execute(title='新渠道售后',description='验证渠道身份'))
                        self.assertEqual(own['ticket']['platform'],channel)
                        found = json.loads(await get.execute(ticket_id=own['ticket']['ticket_id']))
                        self.assertTrue(found['success'])
                        denied = json.loads(await get.execute(ticket_id=ticket['ticket_id']))
                        self.assertEqual(denied['error']['code'],'NOT_FOUND')
            finally:
                for connection in connections.values():
                    await connection.aclose()

    async def test_feishu_admin_permissions_and_update(self):
        import os
        from unittest.mock import patch
        install_ticket_identity_binding()
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'TICKET_ADMIN_FEISHU_IDS':'admin'}):
            registry = ToolRegistry()
            cfg = MCPServerConfig(command=sys.executable, args=[
                str(Path('mcp_servers/ticket_server.py').resolve()), '--db',
                str(Path(directory)/'test.db'), 'serve'], env={'TICKET_ENABLE_ADMIN_TOOLS':'1'})
            connections = await connect_mcp_servers({'ticket_server':cfg},registry)
            try:
                create = registry.get('mcp_ticket_server_create_ticket')
                listing = registry.get('mcp_ticket_server_admin_list_tickets')
                access = registry.get('mcp_ticket_server_get_ticket_access_context')
                update = registry.get('mcp_ticket_server_admin_update_ticket')
                get = registry.get('mcp_ticket_server_get_ticket')
                customer = RequestContext(channel='feishu:bot',chat_id='customer',sender_id='customer',message_id='m1')
                admin = RequestContext(channel='feishu:bot',chat_id='admin',sender_id='admin',metadata={'chat_type':'p2p'})
                group_admin = RequestContext(channel='feishu:bot',chat_id='group',sender_id='admin',metadata={'chat_type':'group'})
                with request_context(customer):
                    self.assertEqual(json.loads(await access.execute())['role'],'customer')
                    ticket = json.loads(await create.execute(title='杯子破损',description='待处理'))['ticket']
                    denial = await listing.execute()
                    self.assertIn('FORBIDDEN',denial)
                    self.assertNotIn('飞书',denial)
                    self.assertNotIn('管理员',denial)
                    self.assertNotIn('admin_list_tickets',denial)
                    self.assertIn('FORBIDDEN',await update.execute(ticket_id=ticket['ticket_id'],status='closed'))
                with request_context(group_admin):
                    self.assertEqual(json.loads(await access.execute())['role'],'customer')
                    self.assertIn('FORBIDDEN',await listing.execute())
                telegram_customer = RequestContext(channel='telegram',chat_id='tg-user',sender_id='tg-user',message_id='tg-m1')
                with request_context(telegram_customer):
                    await create.execute(title='床套仅退款申请',description='需要人工核实退款条件')
                    self.assertEqual(json.loads(await access.execute())['role'],'customer')
                with request_context(admin):
                    role = json.loads(await access.execute())
                    self.assertEqual(role['role'],'admin')
                    self.assertEqual(role['scope'],'all_platforms')
                    records = json.loads(await listing.execute(status='open'))
                    self.assertEqual(records['total'],2)
                    self.assertEqual({t['platform'] for t in records['tickets']},{'feishu','telegram'})
                    changed=json.loads(await update.execute(ticket_id=ticket['ticket_id'],status='in_progress',comment='请补充照片',operator_id='forged'))
                self.assertEqual(changed['ticket']['comments'][-1]['operator_id'],'admin')
                with request_context(customer):
                    result=json.loads(await get.execute(ticket_id=ticket['ticket_id']))
                self.assertEqual(result['ticket']['status'],'in_progress')
                self.assertEqual(result['ticket']['comments'][-1]['text'],'请补充照片')
                self.assertNotIn('operator_id',update.parameters['properties'])
            finally:
                for connection in connections.values():await connection.aclose()
