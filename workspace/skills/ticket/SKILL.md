---
name: ticket
description: 处理出海电商多店铺（TikTok Shop）售后建单、客户本人店铺工单回查、补充说明以及飞书管理员跨店铺全量工单管理与状态更新。
---

# TikTok 多店铺售后工单管理指南

## 1. 角色确定与店铺租户上下文

在执行任何工单操作前，先调用 `mcp_ticket_server_get_ticket_access_context` 获取当前请求的可信身份与店铺上下文：

- **管理员角色 (`role: "admin"`)**：
  - 触发条件：仅限飞书授权白名单管理员在私聊（P2P）中发起；
  - 权限范围：`scope: "all_platforms"`，可调用 `mcp_ticket_server_admin_list_tickets` 跨平台、跨店铺查询全部工单，支持传入 `shop_id`（如 `tt-us-west`、`tt-us-east`、`tt-uk`）进行按店筛选；支持调用 `admin_get_ticket` 查看全量详情及 `admin_update_ticket` 推进工单状态或给客户留言。
- **客户角色 (`role: "customer"`)**：
  - 触发条件：来自任意客户渠道（如 Telegram 各店铺 Bot、QQ 等）；
  - 权限范围：`scope: "own_tickets"`，严格租户隔离，仅能访问当前店铺所属的个人工单；
  - 身份绑定：程序已从消息通道自动绑定 `shop_id`、`platform`、`user_id` 及防重复幂等键 `request_id`。模型工具参数中这些身份字段已被安全隐藏，无需也不允许模型猜测或伪造。

---

## 2. 客户建单与跟进流程

1. **转人工征求同意**：
   - 当 FAQ 无法完全解决客户问题、属于运损破损/需换新开具 RMA，或客户明确提出转人工时，客服 Agent 先提炼问题核心（订单号、商品名称、故障现象），征求客户明确确认（“是否为您创建售后工单由人工客服跟进？”）。
2. **调用建单工具**：
   - 客户同意后，调用 `mcp_ticket_server_create_ticket`：
     - `title`：如 `Lumen Ergo Chair 气压杆漏气下沉` 或 `Lumen Standing Desk 电机报警 E08`；
     - `description`：客户描述详情、已尝试排查步骤、客户诉求；
     - `category`：从 `return`, `exchange`, `refund`, `delivery`, `damaged`, `missing`, `other` 中选择；
     - `order_id`：客户提供的 TikTok 订单编号；
     - `product_name`：关联的具体商品型号；
     - `priority`：默认 `medium`，严重破损或大件阻碍日常使用可选 `high` / `urgent`。
3. **客户工单查询与补充说明**：
   - 客户查询自身历史工单时，调用 `mcp_ticket_server_list_user_tickets` 或带 `ticket_id` 调用 `mcp_ticket_server_get_ticket`；
   - 客户补充破损照片说明或最新进展时，调用 `mcp_ticket_server_add_ticket_comment`。

---

## 3. 飞书管理员工作流程 (Admin Operations)

1. **跨店铺列表查看与筛选**：
   - 管理员询问“待处理工单”时，调用 `mcp_ticket_server_admin_list_tickets(status="open")`；
   - 管理员指定查看特定店铺（例如“看看美西店的单”或“英国店有哪些售后”），传入对应 `shop_id="tt-us-west"` 或 `shop_id="tt-uk"`。
2. **工单详情查看**：
   - 调用 `mcp_ticket_server_admin_get_ticket(ticket_id="TK-...")`，向管理员汇报：工单编号、所属店铺、来源渠道、客户 ID、商品型号、分类、当前状态以及历史跟进流水。
3. **工单流转与跟进批复**：
   - 管理员提供处理方案或指导（如“已通过申请，RMA 码为 RMA-99201，请指导客户寄回加州仓”），调用 `mcp_ticket_server_admin_update_ticket`：
     - 更新状态（`status`）：`in_progress`（处理中）、`resolved`（已解决）、`closed`（已关闭）；
     - 添加公开备注（`comment`）：此备注将在客户于原店铺平台查询时展示给客户。

---

## 4. 工单状态释义对照

| 数据库状态 | 客户界面呈现 | 管理员界面呈现 | 说明 |
| --- | --- | --- | --- |
| `open` | 待处理 | 待人工受理 | 客户刚创建，待人工专员审核 RMA 或定损 |
| `in_progress` | 处理中 | 处理跟进中 | 客服已出具预付运单/补发配件正在寄送中 |
| `resolved` | 已解决 | 方案已落实 | 退款已在 TikTok 发起或换新包裹已签收 |
| `closed` | 已归档关闭 | 已结单归档 | 售后流程完结，客户无进一步追问 |
