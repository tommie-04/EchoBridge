# Nanobot 多平台智能客服

基于 Nanobot 的个人实战项目。第一阶段计划接入飞书、Telegram 和钉钉，共用产品 FAQ，并在用户确认后通过 MCP 工具创建和查询工单。

已创建 FAQ Skill 和本地工单 MCP Server。工单 Server 已完成协议与数据库测试，并已注册到项目 MCP 配置。正在运行的 gateway 重启后加载该服务。

## 首次配置

仓库提供 config.example.json 和 .env.example，所有凭据及账号均使用占位内容。首次部署时分别复制为 config.json 和 .env，再填写自己的配置。已有本地配置时保留原文件。将配置模板里的 /ABSOLUTE/PATH/TO/PROJECT 与 /ABSOLUTE/PATH/TO/PYTHON 替换成自己的项目目录和 Python 路径。

平台机器人需要在各自后台完成配置及用户配对。管理员由 TICKET_ADMIN_FEISHU_IDS 指定。宜家网页快照仅用于本地验证，未随仓库发布；使用前自行准备知识资料并调整 FAQ Skill 中的文件路径。个人配置、配对、会话、数据库及运行日志均被 Git 忽略。

## 本地工单 Server

实现参考 [learn-nanobot 第十二章](https://github.com/bcefghj/learn-nanobot/blob/740d041/docs/12-nanobot-real-projects/README.md) 的 SQLite 工单结构与工具职责，使用 MCP Python SDK 1.30.0 的 stdio 服务。教程的 MIT 许可保存在 mcp_servers/LICENSE.learn-nanobot。

使用 Python 3.11 或更高版本，在虚拟环境中安装 requirements.txt 后运行。使用安装了 requirements.txt 中依赖的 Python 环境。

```bash
python -m pip install -r requirements.txt
python mcp_servers/ticket_server.py init
python mcp_servers/ticket_server.py list --status open
python mcp_servers/ticket_server.py show TK-实际编号
python mcp_servers/ticket_server.py update TK-实际编号 --status in_progress --comment "正在核实商品破损情况"
python -m unittest discover -s tests -v
```

MCP 进程的启动命令为 python mcp_servers/ticket_server.py serve。默认数据库固定为项目根目录下的 data/tickets.db，支持通过 TICKET_DB_PATH 或命令行 --db 指定其他位置。--db 写在 serve、list、show、update 等子命令前。数据库位于 Agent workspace 外，并已加入 Git 忽略规则。

客户 MCP 工具为 create_ticket、get_ticket、list_user_tickets 和 add_ticket_comment。创建需要 request_id，完全相同的重试复用此标识。状态为 open、in_progress、resolved、closed。人工通过本地 update 命令更新状态，备注对客户可见。建单仅保存本项目的售后请求，实际退款、退货和通知需要接入业务系统。

### 渠道身份绑定

通过项目专用入口启动客服 gateway，入口自动使用项目 config.json。

```bash
set -a
source .env
set +a
python customer_gateway.py
```

customer_gateway.py 在本进程中扩展 Nanobot 0.4.1 的 MCPToolWrapper。从当前请求上下文读取 channel、sender_id，并覆盖工单调用的 platform、user_id，模型工具定义中隐藏这些字段。创建请求由 message_id 或 turn_id 生成幂等标识。缺少身份的调用返回明确错误。工单 Server 在本地 stdio 连接上信任此入口提供的身份，再按平台和用户筛选记录。

身份绑定依赖此项目入口；普通 nanobot gateway 启动方式没有该扩展。Nanobot 升级后须重跑 tests/test_ticket_identity.py，验证内部接口兼容性。该绑定覆盖正常工单 MCP 调用，文件与 Shell 工具的系统权限仍需单独配置。

工单 Skill 位于 workspace/skills/ticket/SKILL.md。已通过真实 MCP 子进程测试发送者覆盖、跨用户及跨平台查询拒绝、并发上下文隔离与重复建单去重，真实渠道仍需进行会话验收。

### 飞书管理员私聊处理

.env 中的 TICKET_ADMIN_FEISHU_IDS 为飞书管理员 open_id 列表，使用英文逗号分隔。在本地填写自己的管理员账号 ID。通过 customer_gateway.py 启动后，管理员可私聊机器人说“查看全部待处理工单”“查看 TK-... 详情”或“将 TK-... 改为处理中，备注请补充照片”。

管理工具为 admin_list_tickets、admin_get_ticket、admin_update_ticket；发送者、飞书渠道和私聊条件由项目入口校验，更新记录包含实际处理人的 operator_id。普通客户仅能使用本人查询及补充接口。当前更新保存到数据库，客户下次查询可见，主动推送尚待实现。更改管理员列表后重新加载 .env 并重启项目入口。
