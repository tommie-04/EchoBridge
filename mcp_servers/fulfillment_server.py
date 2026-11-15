"""MCP tools for the local TikTok Shipping simulation, used by Feishu admins."""
from collections import Counter
from pathlib import Path
import sys

# Direct stdio execution starts with mcp_servers/ on sys.path.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mcp.server.fastmcp import FastMCP
from fulfillment_simulator import list_mock_shops
from fulfillment_service import prepare_batch, read_batch

mcp = FastMCP('fulfillment-server', instructions=(
    '20 家美国店铺、洛杉矶仓、TikTok Shipping / USPS 模拟履约。'
    '日期按洛杉矶时间。工具仅准备演示文件，结果不能作为购买邮资或实物打印成功的证明。'
    '请求身份由项目 Gateway 校验。'))


def batch_summary(batch: dict) -> dict:
    return {
        'mode': 'simulation', 'batch_id': batch['batch_id'],
        'ship_date': batch['ship_date'], 'timezone': batch['timezone'],
        'warehouse': batch['warehouse'], 'shop_count': len(batch['selected_shops']),
        'package_count': batch['package_count'], 'unit_count': batch['unit_count'],
        'excluded_count': batch['excluded_count'],
        'excluded_reasons': dict(Counter(item['reason'] for item in batch['excluded'])),
        'groups': [{key: value for key, value in group.items() if key != 'packages'}
                   for group in batch['groups']],
        'pdf_url': '/api/simulation/batches/' + batch['batch_id'] + '/pdf',
    }


@mcp.tool()
def list_shipping_shops() -> dict:
    """列出可用的模拟 TikTok 店铺及其 ID，用于选择履约范围。"""
    return {'mode': 'simulation', 'shops': list_mock_shops()}


@mcp.tool()
def prepare_shipping_batch(shop_ids: list[str], ship_date: str) -> dict:
    """按明确选择的店铺和洛杉矶日期准备模拟面单批次，重复请求复用快照。"""
    return batch_summary(prepare_batch(shop_ids, ship_date))


@mcp.tool()
def get_shipping_batch(batch_id: str) -> dict:
    """查询已准备的批次摘要、分类数量与演示 PDF 下载入口。"""
    return batch_summary(read_batch(batch_id))


if __name__ == '__main__':
    mcp.run(transport='stdio')
