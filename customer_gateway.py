"""Nanobot gateway entry point for the Feishu warehouse administrator."""
from __future__ import annotations

import os
from pathlib import Path
import sys


def install_admin_permissions() -> None:
    from nanobot.agent.tools import mcp
    from nanobot.agent.tools.base import ToolResult
    from nanobot.agent.tools.context import current_request_context

    original = mcp.MCPToolWrapper
    if getattr(original, '_fulfillment_admin_permissions', False):
        return

    class AdminMCPTool(original):
        _fulfillment_admin_permissions = True

        async def execute(self, **kwargs):
            if self._server_name != 'fulfillment_server':
                return await super().execute(**kwargs)
            context = current_request_context()
            admins = {value.strip() for value in os.environ.get('FEISHU_ADMIN_IDS', '').split(',')
                      if value.strip()}
            if context is None:
                return ToolResult.error('FORBIDDEN: 请通过已授权的飞书管理员私聊操作。')
            private = context.metadata.get('chat_type') == 'p2p' or (
                'chat_type' not in context.metadata and context.chat_id == context.sender_id)
            if (context.channel.split(':', 1)[0] != 'feishu'
                    or context.sender_id not in admins or not private):
                return ToolResult.error('FORBIDDEN: 当前身份无法执行此操作。')
            return await super().execute(**kwargs)

    mcp.MCPToolWrapper = AdminMCPTool


def main() -> None:
    install_admin_permissions()
    from nanobot.cli.entry import main as nanobot_main
    config = str(Path(os.environ.get(
        'CUSTOMER_SUPPORT_CONFIG', str(Path(__file__).resolve().parent / 'config.json')
    )).expanduser().resolve())
    sys.argv = ['nanobot', 'gateway', '--config', config, *sys.argv[1:]]
    nanobot_main()


if __name__ == '__main__':
    main()
