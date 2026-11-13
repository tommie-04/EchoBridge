"""SQLite-backed after-sales tickets, exposed through MCP stdio and a local CLI.

Adapted from learn-nanobot chapter 12's create/get/list/update design,
extended with multi-store (shop_id) tenant isolation for cross-border TikTok storefronts.
Customer identity arguments require a trusted caller. The shared MCP connection
cannot authenticate a Telegram/Feishu sender by itself. Bind identity in the
host integration before enabling this service for untrusted customers.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import sqlite3
from typing import Any, Iterator, Literal
from uuid import uuid4
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parents[1]
Priority = Literal['low', 'medium', 'high', 'urgent']
Status = Literal['open', 'in_progress', 'resolved', 'closed']
Category = Literal['return', 'exchange', 'refund', 'delivery', 'damaged', 'missing', 'other']
PRIORITIES = ('low', 'medium', 'high', 'urgent')
STATUSES = ('open', 'in_progress', 'resolved', 'closed')
CATEGORIES = ('return', 'exchange', 'refund', 'delivery', 'damaged', 'missing', 'other')


class TicketError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def text_field(value: str, name: str, maximum: int, *, required: bool = True) -> str:
    if not isinstance(value, str):
        raise TicketError('INVALID_INPUT', f'{name} 必须为字符串')
    value = value.strip()
    if (required and not value) or len(value) > maximum or '\x00' in value:
        raise TicketError('INVALID_INPUT', f'{name} 为空、过长或含有无效字符')
    return value


def choice_field(value: str, name: str, choices: tuple[str, ...]) -> str:
    if value not in choices:
        raise TicketError('INVALID_INPUT', f'{name} 必须为 {", ".join(choices)}')
    return value


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='microseconds')


def local_timestamp(value: str | None, zone: ZoneInfo) -> str | None:
    """Convert aware timestamps for display without guessing legacy timezones."""
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(zone).isoformat(timespec='seconds')


class TicketStore:
    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection(write=True) as conn:
            conn.execute('''CREATE TABLE IF NOT EXISTS tickets (
                ticket_id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                description TEXT NOT NULL,
                priority TEXT DEFAULT 'medium',
                status TEXT DEFAULT 'open',
                user_id TEXT NOT NULL,
                user_name TEXT DEFAULT '',
                platform TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                comments TEXT DEFAULT '[]'
            )''')
            # Retain existing schema while adding multi-store tenant isolation and category fields.
            columns = {row['name'] for row in conn.execute('PRAGMA table_info(tickets)')}
            additions = {
                'category': "TEXT NOT NULL DEFAULT 'other'",
                'order_id': "TEXT NOT NULL DEFAULT ''",
                'product_name': "TEXT NOT NULL DEFAULT ''",
                'request_id': 'TEXT',
                'request_fingerprint': 'TEXT',
                'shop_id': "TEXT NOT NULL DEFAULT 'tt-us-west'",
            }
            for name, definition in additions.items():
                if name not in columns:
                    conn.execute(f'ALTER TABLE tickets ADD COLUMN {name} {definition}')
            conn.execute('DROP INDEX IF EXISTS tickets_request')
            conn.execute('''CREATE UNIQUE INDEX IF NOT EXISTS tickets_shop_request
                ON tickets(shop_id, platform, user_id, request_id) WHERE request_id IS NOT NULL''')
            conn.execute('''CREATE INDEX IF NOT EXISTS tickets_shop_owner
                ON tickets(shop_id, platform, user_id, created_at)''')
        self.path.chmod(0o600)

    @contextmanager
    def connection(self, *, write: bool = False) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            if write:
                conn.execute('BEGIN IMMEDIATE')
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    @staticmethod
    def owner(platform: str, user_id: str, shop_id: str | None = None) -> tuple[str, ...]:
        platform = text_field(platform, 'platform', 32).lower()
        if not re.fullmatch(r'[a-z][a-z0-9_-]*', platform):
            raise TicketError('INVALID_INPUT', 'platform 格式无效')
        uid = text_field(user_id, 'user_id', 256)
        if shop_id is not None:
            sid = text_field(shop_id, 'shop_id', 64).lower()
            if not re.fullmatch(r'[a-z0-9_-]+', sid):
                raise TicketError('INVALID_INPUT', 'shop_id 格式无效')
            return sid, platform, uid
        return platform, uid

    @staticmethod
    def ticket_dict(row: sqlite3.Row) -> dict[str, Any]:
        ticket = dict(row)
        comments = json.loads(ticket.pop('comments') or '[]')
        if not isinstance(comments, list):
            raise TicketError('INVALID_DATA', '工单记录格式异常，请联系处理人员')
        ticket['comments'] = comments
        zone = ZoneInfo(os.environ.get('TICKET_TIMEZONE', 'America/Los_Angeles'))
        ticket['display_timezone'] = zone.key
        for key in ('created_at', 'updated_at'):
            ticket[key + '_local'] = local_timestamp(ticket[key], zone)
        for event in comments:
            event['timestamp_local'] = local_timestamp(event.get('timestamp'), zone)
        ticket.pop('request_fingerprint', None)
        ticket.pop('request_id', None)
        return ticket

    def create_ticket(
        self, title: str, description: str, user_id: str, platform: str,
        request_id: str, shop_id: str = 'tt-us-west', priority: str = 'medium',
        user_name: str = '', category: str = 'other', order_id: str = '',
        product_name: str = '',
    ) -> dict[str, Any]:
        shop_id, platform, user_id = self.owner(platform, user_id, shop_id)
        request_id = text_field(request_id, 'request_id', 128)
        payload = {
            'shop_id': shop_id,
            'title': text_field(title, 'title', 200),
            'description': text_field(description, 'description', 10000),
            'priority': choice_field(priority, 'priority', PRIORITIES),
            'user_name': text_field(user_name, 'user_name', 200, required=False),
            'category': choice_field(category, 'category', CATEGORIES),
            'order_id': text_field(order_id, 'order_id', 128, required=False),
            'product_name': text_field(product_name, 'product_name', 300, required=False),
        }
        fingerprint = hashlib.sha256(json.dumps(
            payload, sort_keys=True, ensure_ascii=False,
        ).encode()).hexdigest()
        with self.connection(write=True) as conn:
            existing = conn.execute('''SELECT * FROM tickets
                WHERE shop_id = ? AND platform = ? AND user_id = ? AND request_id = ?''',
                (shop_id, platform, user_id, request_id)).fetchone()
            if existing:
                if existing['request_fingerprint'] != fingerprint:
                    raise TicketError('IDEMPOTENCY_CONFLICT', '同一 request_id 对应不同内容，请核实原请求')
                return {'success': True, 'created': False, 'ticket': self.ticket_dict(existing)}
            ticket_id = f'TK-{uuid4().hex.upper()}'
            now = utc_now()
            conn.execute('''INSERT INTO tickets (
                ticket_id, title, description, priority, status, user_id, user_name,
                platform, shop_id, created_at, updated_at, category, order_id, product_name,
                request_id, request_fingerprint
            ) VALUES (?, ?, ?, ?, 'open', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''', (
                ticket_id, payload['title'], payload['description'], payload['priority'],
                user_id, payload['user_name'], platform, shop_id, now, now, payload['category'],
                payload['order_id'], payload['product_name'], request_id, fingerprint,
            ))
            row = conn.execute('SELECT * FROM tickets WHERE ticket_id = ?', (ticket_id,)).fetchone()
            return {'success': True, 'created': True, 'ticket': self.ticket_dict(row)}

    def get_ticket(
        self, ticket_id: str, user_id: str, platform: str, shop_id: str | None = None,
    ) -> dict[str, Any]:
        ticket_id = text_field(ticket_id, 'ticket_id', 128)
        if shop_id is not None:
            shop_id, platform, user_id = self.owner(platform, user_id, shop_id)
            query = 'SELECT * FROM tickets WHERE ticket_id = ? AND user_id = ? AND platform = ? AND shop_id = ?'
            params: tuple[Any, ...] = (ticket_id, user_id, platform, shop_id)
        else:
            platform, user_id = self.owner(platform, user_id)
            query = 'SELECT * FROM tickets WHERE ticket_id = ? AND user_id = ? AND platform = ?'
            params = (ticket_id, user_id, platform)
        with self.connection() as conn:
            row = conn.execute(query, params).fetchone()
        if row is None:
            raise TicketError('NOT_FOUND', '未找到当前用户的工单')
        return {'success': True, 'ticket': self.ticket_dict(row)}

    def list_tickets(
        self, *, status: str | None = None, user_id: str | None = None,
        platform: str | None = None, shop_id: str | None = None,
        limit: int = 20, offset: int = 0,
    ) -> dict[str, Any]:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise TicketError('INVALID_INPUT', 'limit 必须为 1 到 100 的整数')
        if type(offset) is not int or offset < 0:
            raise TicketError('INVALID_INPUT', 'offset 必须为非负整数')
        clauses = []
        values: list[Any] = []
        if user_id is not None or platform is not None:
            if shop_id is not None:
                shop_id, platform, user_id = self.owner(platform, user_id, shop_id)
                clauses.append('shop_id = ? AND platform = ? AND user_id = ?')
                values.extend((shop_id, platform, user_id))
            else:
                platform, user_id = self.owner(platform, user_id)
                clauses.append('platform = ? AND user_id = ?')
                values.extend((platform, user_id))
        elif shop_id is not None:
            shop_id = text_field(shop_id, 'shop_id', 64).lower()
            clauses.append('shop_id = ?')
            values.append(shop_id)
        if status is not None:
            clauses.append('status = ?')
            values.append(choice_field(status, 'status', STATUSES))
        where = ' WHERE ' + ' AND '.join(clauses) if clauses else ''
        with self.connection() as conn:
            conn.execute('BEGIN')
            total = conn.execute('SELECT COUNT(*) FROM tickets' + where, values).fetchone()[0]
            rows = conn.execute('SELECT * FROM tickets' + where +
                ' ORDER BY created_at DESC, ticket_id DESC LIMIT ? OFFSET ?',
                (*values, limit, offset)).fetchall()
        return {'success': True, 'total': total, 'limit': limit, 'offset': offset,
                'tickets': [self.ticket_dict(row) for row in rows]}

    def update_ticket(
        self, ticket_id: str, *, status: str | None = None, comment: str = '',
        owner: tuple[str, ...] | None = None, operator_id: str = 'local-cli',
    ) -> dict[str, Any]:
        ticket_id = text_field(ticket_id, 'ticket_id', 128)
        comment = text_field(comment, 'comment', 4000, required=False)
        if status is not None:
            choice_field(status, 'status', STATUSES)
        if not comment and status is None:
            raise TicketError('INVALID_INPUT', '请提供状态或备注')
        if owner is not None:
            if len(owner) == 3:
                owner = self.owner(owner[1], owner[2], owner[0])
            else:
                owner = self.owner(owner[0], owner[1])
            if status is not None:
                raise TicketError('FORBIDDEN', '客户补充接口仅支持添加备注')
        with self.connection(write=True) as conn:
            query = 'SELECT * FROM tickets WHERE ticket_id = ?'
            params: list[Any] = [ticket_id]
            if owner is not None:
                if len(owner) == 3:
                    query += ' AND shop_id = ? AND platform = ? AND user_id = ?'
                    params.extend(owner)
                else:
                    query += ' AND platform = ? AND user_id = ?'
                    params.extend(owner)
            row = conn.execute(query, params).fetchone()
            if row is None:
                raise TicketError('NOT_FOUND', '未找到可访问的工单')
            ticket = self.ticket_dict(row)
            now = utc_now()
            new_status = status or ticket['status']
            event = {'text': comment, 'timestamp': now,
                     'author': 'customer' if owner is not None else 'staff',
                     'previous_status': ticket['status'], 'status': new_status}
            if owner is None:
                event['operator_id'] = text_field(operator_id, 'operator_id', 256)
            # Persist only business events, not the derived display-time fields.
            comments = json.loads(row['comments'] or '[]')
            comments.append(event)
            conn.execute('''UPDATE tickets SET status = ?, comments = ?, updated_at = ?
                WHERE ticket_id = ?''', (new_status,
                json.dumps(comments, ensure_ascii=False), now, ticket_id))
            updated = conn.execute('SELECT * FROM tickets WHERE ticket_id = ?', (ticket_id,)).fetchone()
            return {'success': True, 'ticket': self.ticket_dict(updated)}

    def admin_get_ticket(self, ticket_id: str) -> dict[str, Any]:
        ticket_id = text_field(ticket_id, 'ticket_id', 128)
        with self.connection() as conn:
            row = conn.execute('SELECT * FROM tickets WHERE ticket_id = ?', (ticket_id,)).fetchone()
        if row is None:
            raise TicketError('NOT_FOUND', '工单不存在')
        return {'success': True, 'ticket': self.ticket_dict(row)}


def serve(store: TicketStore) -> None:
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP('ticket-server', instructions=(
        '本地多店铺售后工单服务。使用可信渠道上下文中的 shop_id, platform 和 user_id。'
        '同意建单后调用 create_ticket，重试复用 request_id。'
        '这些工单属于演示项目，真实退款、退货和官方通知需另行办理。'
    ))

    def run_tool(operation, **kwargs) -> dict[str, Any]:
        try:
            return operation(**kwargs)
        except TicketError as exc:
            return {'success': False, 'error': {'code': exc.code, 'message': str(exc)}}
        except (sqlite3.Error, OSError, json.JSONDecodeError):
            logging.exception('Ticket storage operation failed')
            return {'success': False, 'error': {
                'code': 'STORAGE_ERROR', 'message': '工单存储暂时不可用，请稍后核实结果'}}

    @mcp.tool()
    def create_ticket(
        title: str, description: str, user_id: str, platform: str,
        request_id: str, shop_id: str = 'tt-us-west', priority: Priority = 'medium',
        user_name: str = '', category: Category = 'other', order_id: str = '',
        product_name: str = '',
    ) -> dict[str, Any]:
        """用户同意后建单。request_id 标识本次确认操作，重试复用原值及参数。

        shop_id/platform/user_id 由调用方绑定到可信渠道身份。category 区分退货、换货、
        退款、配送、破损、少件及其他问题。订单号和商品名可以留空。
        """
        return run_tool(store.create_ticket, title=title, description=description,
            user_id=user_id, platform=platform, request_id=request_id, shop_id=shop_id,
            priority=priority, user_name=user_name, category=category,
            order_id=order_id, product_name=product_name)

    @mcp.tool()
    def get_ticket(
        ticket_id: str, user_id: str, platform: str, shop_id: str | None = None,
    ) -> dict[str, Any]:
        """按工单号及可信的来源店铺、平台、用户 ID 查询当前用户的工单。"""
        return run_tool(store.get_ticket, ticket_id=ticket_id, user_id=user_id,
            platform=platform, shop_id=shop_id)

    @mcp.tool()
    def list_user_tickets(
        user_id: str, platform: str, shop_id: str | None = None,
        status: Status | None = None, limit: int = 20, offset: int = 0,
    ) -> dict[str, Any]:
        """分页列出当前用户在当前店铺的工单，status 可选。shop_id/platform/user_id 来自可信上下文。"""
        return run_tool(store.list_tickets, user_id=user_id, platform=platform,
            shop_id=shop_id, status=status, limit=limit, offset=offset)

    @mcp.tool()
    def add_ticket_comment(
        ticket_id: str, user_id: str, platform: str, comment: str,
        shop_id: str | None = None,
    ) -> dict[str, Any]:
        """客户为自己的工单补充说明。此接口只追加备注。"""
        owner = (shop_id, platform, user_id) if shop_id else (platform, user_id)
        return run_tool(store.update_ticket, ticket_id=ticket_id,
            comment=text_field(comment, 'comment', 4000), owner=owner)

    if os.environ.get('TICKET_ENABLE_ADMIN_TOOLS') == '1':
        @mcp.tool()
        def get_ticket_access_context() -> dict[str, Any]:
            '''管理工单前先查询当前请求的真实角色和可用列表工具，由项目 gateway 返回身份上下文。'''
            return {'success': False, 'error': {'code': 'AUTH_CONTEXT_REQUIRED',
                    'message': '请通过项目 customer_gateway.py 获取当前渠道角色'}}

        @mcp.tool()
        def admin_list_tickets(
            status: Status | None = None, shop_id: str | None = None,
            limit: int = 20, offset: int = 0,
        ) -> dict[str, Any]:
            '''飞书管理员在私聊中查看全部渠道工单，可按状态和店铺筛选。由项目入口校验管理员权限。'''
            return run_tool(store.list_tickets, status=status, shop_id=shop_id,
                            limit=limit, offset=offset)

        @mcp.tool()
        def admin_get_ticket(ticket_id: str) -> dict[str, Any]:
            '''飞书管理员查看指定工单的完整详情与处理记录。'''
            return run_tool(store.admin_get_ticket, ticket_id=ticket_id)

        @mcp.tool()
        def admin_update_ticket(ticket_id: str, status: Status | None = None,
                                comment: str = '', operator_id: str = '') -> dict[str, Any]:
            '''飞书管理员更新工单状态或添加客户可见备注，处理人由项目入口绑定。'''
            return run_tool(store.update_ticket, ticket_id=ticket_id, status=status,
                            comment=comment, operator_id=operator_id)

    mcp.run(transport='stdio')


def main() -> None:
    parser = argparse.ArgumentParser(description='本地售后工单 MCP Server 与人工处理入口')
    parser.add_argument('--db', default=os.environ.get('TICKET_DB_PATH', str(PROJECT_ROOT / 'data/tickets.db')))
    commands = parser.add_subparsers(dest='command')
    commands.add_parser('serve', help='启动 MCP stdio 服务，默认命令')
    commands.add_parser('init', help='初始化工单数据库')
    listing = commands.add_parser('list', help='人工查看所有平台工单')
    listing.add_argument('--status', choices=STATUSES)
    listing.add_argument('--shop-id', help='按店铺 ID 筛选工单')
    listing.add_argument('--limit', type=int, default=20)
    listing.add_argument('--offset', type=int, default=0)
    showing = commands.add_parser('show', help='人工查看工单及公开处理记录')
    showing.add_argument('ticket_id')
    update = commands.add_parser('update', help='人工更新状态并添加用户可见的备注')
    update.add_argument('ticket_id')
    update.add_argument('--status', choices=STATUSES)
    update.add_argument('--comment', default='')
    args = parser.parse_args()
    try:
        store = TicketStore(args.db)
        if args.command in (None, 'serve'):
            serve(store)
            return
        if args.command == 'init':
            result = {'success': True, 'database': str(store.path)}
        elif args.command == 'list':
            result = store.list_tickets(status=args.status, shop_id=args.shop_id,
                                        limit=args.limit, offset=args.offset)
        elif args.command == 'show':
            result = store.admin_get_ticket(args.ticket_id)
        else:
            result = store.update_ticket(args.ticket_id, status=args.status, comment=args.comment)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (TicketError, sqlite3.Error, OSError, json.JSONDecodeError) as exc:
        logging.error('%s', exc)
        raise SystemExit(1) from exc


if __name__ == '__main__':
    main()
