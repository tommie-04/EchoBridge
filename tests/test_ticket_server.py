"""Regression checks for protocol compatibility, retries, and shared SQLite state."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp_servers.ticket_server import TicketError, TicketStore

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / 'mcp_servers/ticket_server.py'


def create_arguments(**overrides):
    args = dict(title='杯子收到时破损', description='收到两只杯子，其中一只破损；希望人工核实。',
                user_id='test-user', platform='telegram', request_id='confirmed-message-1',
                category='damaged', product_name='玻璃杯', order_id='DEMO-001')
    args.update(overrides)
    return args


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / 'tickets.db'
        self.store = TicketStore(self.db)

    def test_create_retry_and_persistence(self):
        first = self.store.create_ticket(**create_arguments())
        retry = self.store.create_ticket(**create_arguments())
        self.assertTrue(first['created'])
        self.assertFalse(retry['created'])
        self.assertEqual(first['ticket']['ticket_id'], retry['ticket']['ticket_id'])
        saved = TicketStore(self.db).get_ticket(first['ticket']['ticket_id'], 'test-user', 'telegram')
        self.assertEqual(saved['ticket']['order_id'], 'DEMO-001')
        self.assertEqual(saved['ticket']['status'], 'open')
        self.assertNotIn('request_fingerprint', saved['ticket'])

    def test_concurrent_retries_create_one_ticket(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: self.store.create_ticket(**create_arguments()), range(12)))
        self.assertEqual(sum(r['created'] for r in results), 1)
        self.assertEqual(self.store.list_tickets()['total'], 1)

    def test_changed_retry_payload_is_rejected(self):
        self.store.create_ticket(**create_arguments())
        with self.assertRaises(TicketError) as error:
            self.store.create_ticket(**create_arguments(description='新问题'))
        self.assertEqual(error.exception.code, 'IDEMPOTENCY_CONFLICT')

    def test_platform_and_owner_filtering(self):
        first = self.store.create_ticket(**create_arguments())['ticket']
        self.store.create_ticket(**create_arguments(platform='feishu'))
        for user, platform in [('another-user', 'telegram'), ('test-user', 'feishu')]:
            with self.assertRaises(TicketError):
                self.store.get_ticket(first['ticket_id'], user, platform)
            with self.assertRaises(TicketError):
                self.store.update_ticket(first['ticket_id'], comment='不属于我的工单', owner=(platform,user))
        listed = self.store.list_tickets(user_id='test-user', platform='feishu')
        self.assertEqual(listed['total'], 1)
        self.assertEqual(listed['tickets'][0]['platform'], 'feishu')

    def test_concurrent_comments_and_staff_update(self):
        ticket = self.store.create_ticket(**create_arguments())['ticket']
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(lambda i: self.store.update_ticket(ticket['ticket_id'],
                comment=f'补充 {i}', owner=('telegram','test-user')), range(12)))
        result = self.store.update_ticket(ticket['ticket_id'], status='in_progress', comment='正在核实')
        self.assertEqual(len(result['ticket']['comments']), 13)
        self.assertEqual(result['ticket']['comments'][-1]['author'], 'staff')
        with self.assertRaises(TicketError):
            self.store.update_ticket(ticket['ticket_id'], status='resolved', owner=('telegram','test-user'))

    def test_invalid_inputs_and_pagination(self):
        for override in [dict(title='  '), dict(priority='invalid'), dict(category='invalid'),
                         dict(request_id=''), dict(description='a'*10001), dict(user_id='')]:
            with self.assertRaises(TicketError):
                self.store.create_ticket(**create_arguments(**override))
        for i in range(3):
            self.store.create_ticket(**create_arguments(request_id=str(i)))
        page = self.store.list_tickets(limit=2, offset=2)
        self.assertEqual(page['total'], 3)
        self.assertEqual(len(page['tickets']), 1)
        for args in [dict(limit=0), dict(offset=-1), dict(status='invalid')]:
            with self.assertRaises(TicketError):
                self.store.list_tickets(**args)

    def test_sql_is_parameterized(self):
        user = "x' OR 1=1 --"
        created = self.store.create_ticket(**create_arguments(user_id=user))['ticket']
        self.assertEqual(self.store.list_tickets(user_id=user, platform='telegram')['total'], 1)
        self.assertEqual(self.store.list_tickets(user_id='other', platform='telegram')['total'], 0)
        with self.assertRaises(TicketError):
            self.store.get_ticket("' OR 1=1 --", user, 'telegram')
        self.assertEqual(self.store.get_ticket(created['ticket_id'],user,'telegram')['ticket']['user_id'],user)

    def test_tutorial_schema_migrates_without_losing_records(self):
        legacy = Path(self.tmp.name) / 'legacy.db'
        with sqlite3.connect(legacy) as conn:
            conn.execute('''CREATE TABLE tickets (
                ticket_id TEXT PRIMARY KEY, title TEXT NOT NULL, description TEXT NOT NULL,
                priority TEXT DEFAULT 'medium', status TEXT DEFAULT 'open', user_id TEXT NOT NULL,
                user_name TEXT DEFAULT '', platform TEXT DEFAULT '', created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL, comments TEXT DEFAULT '[]')''')
            conn.execute('''INSERT INTO tickets (ticket_id,title,description,user_id,platform,
                created_at,updated_at,comments) VALUES (?,?,?,?,?,?,?,?)''',
                ('TK-LEGACY','旧工单','旧描述','old-user','feishu','2025-01-01','2025-01-01',
                 '[{"text":"旧备注","timestamp":"2025-01-01"}]'))
        migrated = TicketStore(legacy)
        ticket = migrated.get_ticket('TK-LEGACY','old-user','feishu')['ticket']
        self.assertEqual(ticket['category'],'other')
        self.assertEqual(ticket['comments'][0]['text'],'旧备注')
        migrated.create_ticket(**create_arguments())
        self.assertEqual(migrated.list_tickets()['total'],2)
        self.assertEqual(TicketStore(legacy).list_tickets()['total'],2)

    def test_admin_cli_updates_shared_database(self):
        ticket = self.store.create_ticket(**create_arguments())['ticket']
        run = subprocess.run([sys.executable,str(SERVER),'--db',str(self.db),'update',
            ticket['ticket_id'],'--status','resolved','--comment','已处理，请确认'],
            capture_output=True,text=True,check=True)
        self.assertEqual(json.loads(run.stdout)['ticket']['status'],'resolved')
        self.assertEqual(self.store.get_ticket(ticket['ticket_id'],'test-user','telegram')['ticket']['status'],'resolved')
        missing = subprocess.run([sys.executable,str(SERVER),'--db',str(self.db),'show','missing'],
            capture_output=True,text=True)
        self.assertNotEqual(missing.returncode,0)


class ProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_stdio_client_and_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            params = StdioServerParameters(command=sys.executable,
                args=[str(SERVER),'--db',str(Path(directory)/'test.db'),'serve'])
            async with asyncio.timeout(30):
                async with stdio_client(params) as (read,write):
                    async with ClientSession(read,write) as session:
                        await session.initialize()
                        tools = await session.list_tools()
                        self.assertEqual({t.name for t in tools.tools},
                            {'create_ticket','get_ticket','list_user_tickets','add_ticket_comment'})
                        created = await session.call_tool('create_ticket',create_arguments())
                        self.assertFalse(created.isError)
                        payload = json.loads(created.content[0].text)
                        self.assertTrue(payload['success'])
                        ticket_id = payload['ticket']['ticket_id']
                        retry = await session.call_tool('create_ticket',create_arguments())
                        self.assertFalse(json.loads(retry.content[0].text)['created'])
                        args = dict(ticket_id=ticket_id,user_id='test-user',platform='telegram')
                        got = await session.call_tool('get_ticket',args)
                        self.assertEqual(json.loads(got.content[0].text)['ticket']['order_id'],'DEMO-001')
                        comment = await session.call_tool('add_ticket_comment',{**args,'comment':'补充商品照片说明'})
                        self.assertTrue(json.loads(comment.content[0].text)['success'])
                        wrong = await session.call_tool('get_ticket',{**args,'user_id':'another-user'})
                        self.assertEqual(json.loads(wrong.content[0].text)['error']['code'],'NOT_FOUND')
                        invalid = await session.call_tool('create_ticket',create_arguments(priority='invalid'))
                        self.assertTrue(invalid.isError)
                        listed = await session.call_tool('list_user_tickets',dict(user_id='test-user',platform='telegram'))
                        self.assertEqual(json.loads(listed.content[0].text)['total'],1)


if __name__ == '__main__':
    unittest.main()
