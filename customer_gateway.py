"""Project gateway entrypoint with trusted sender binding for ticket MCP tools."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import sys

SUPPORTED_CHANNELS = {'qq', 'telegram', 'feishu', 'dingtalk', 'discord', 'weixin', 'wecom'}
ADMIN_TOOLS = {'admin_list_tickets', 'admin_get_ticket', 'admin_update_ticket'}
TICKET_TOOLS = {'create_ticket', 'get_ticket', 'list_user_tickets', 'add_ticket_comment', 'get_ticket_access_context'}


def install_ticket_identity_binding() -> None:
    from nanobot.agent.tools import mcp
    from nanobot.agent.tools.base import ToolResult
    from nanobot.agent.tools.context import current_request_context

    original = mcp.MCPToolWrapper
    if getattr(original, '_ticket_identity_binding', False):
        return

    class SenderBoundMCPTool(original):
        _ticket_identity_binding = True

        def __init__(self, session, server_name, tool_def, tool_timeout=30):
            super().__init__(session, server_name, tool_def, tool_timeout)
            self._admin_ticket_tool = server_name == 'ticket_server' and tool_def.name in ADMIN_TOOLS
            self._bind_ticket_sender = server_name == 'ticket_server' and tool_def.name in TICKET_TOOLS
            if self._bind_ticket_sender or self._admin_ticket_tool:
                self._parameters = deepcopy(self._parameters)
                hidden = {'platform', 'user_id', 'operator_id'}
                if tool_def.name == 'create_ticket':
                    hidden.add('request_id')
                for key in hidden:
                    self._parameters.get('properties', {}).pop(key, None)
                self._parameters['required'] = [
                    key for key in self._parameters.get('required', []) if key not in hidden
                ]

        async def execute(self, **kwargs):
            if not (self._bind_ticket_sender or self._admin_ticket_tool):
                return await super().execute(**kwargs)
            context = current_request_context()
            if context is None:
                return ToolResult.error('AUTH_CONTEXT_REQUIRED: 工单操作需要当前渠道的发送者身份。')
            platform = context.channel.split(':', 1)[0]
            sender_id = context.sender_id
            if platform not in SUPPORTED_CHANNELS or not isinstance(sender_id, str) or not sender_id.strip() or sender_id == 'unknown':
                return ToolResult.error('AUTH_CONTEXT_REQUIRED: 当前渠道未提供可用的发送者身份。')
            arguments = dict(kwargs)
            admins = {value.strip() for value in os.environ.get('TICKET_ADMIN_FEISHU_IDS', '').split(',') if value.strip()}
            private = context.metadata.get('chat_type') == 'p2p' or (
                'chat_type' not in context.metadata and context.chat_id == sender_id)
            is_admin = platform == 'feishu' and sender_id in admins and private
            if self._original_name == 'get_ticket_access_context':
                return json.dumps({'success': True, 'role': 'admin' if is_admin else 'customer',
                    'scope': 'all_platforms' if is_admin else 'own_tickets'})
            if self._admin_ticket_tool:
                if not is_admin:
                    return ToolResult.error('FORBIDDEN: 当前无法直接办理此操作。可以协助整理问题，并在您同意后提交人工跟进。')
                if self._original_name == 'admin_update_ticket':
                    arguments['operator_id'] = sender_id
                return await super().execute(**arguments)
            arguments['platform'] = platform
            arguments['user_id'] = sender_id
            if self._original_name == 'create_ticket':
                event_id = context.message_id or context.turn_id
                if not event_id:
                    return ToolResult.error('REQUEST_CONTEXT_REQUIRED: 当前消息缺少建单幂等标识。')
                # Re-delivery of the same platform message reuses the same key.
                material = f'{context.channel}\0{sender_id}\0{event_id}'.encode()
                arguments['request_id'] = hashlib.sha256(material).hexdigest()
            return await super().execute(**arguments)

    mcp.MCPToolWrapper = SenderBoundMCPTool


def main() -> None:
    install_ticket_identity_binding()
    from nanobot.cli.entry import main as nanobot_main
    config = str(Path(os.environ.get(
        'CUSTOMER_SUPPORT_CONFIG', str(Path(__file__).resolve().parent / 'config.json')
    )).expanduser().resolve())
    sys.argv = ['nanobot', 'gateway', '--config', config, *sys.argv[1:]]
    nanobot_main()


if __name__ == '__main__':
    main()
