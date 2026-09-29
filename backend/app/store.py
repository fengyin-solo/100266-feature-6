"""内存数据仓库：给每个业务模块准备一份可筛选、可流转的示例数据。

真实项目里这里会换成数据库访问层；当前实现只依赖标准库，保证克隆下来就能起。
"""
from __future__ import annotations

from typing import Any

from app.seed import SEED_ROWS


class Store:
    def __init__(self) -> None:
        self._tables: dict[str, list[dict[str, Any]]] = {
            name: [dict(row) for row in rows] for name, rows in SEED_ROWS.items()
        }
        # 批量下发的待办队列与已受理的填报都放这里：抽屉、列表页统计等入口
        # 读到的待收尾数必须同源，不能只存在某个页面的局部状态里。
        self._batch_queues: dict[str, list[dict[str, Any]]] = {}
        self._batch_requests: dict[str, dict[str, Any]] = {}

    def module_names(self) -> list[str]:
        return sorted(self._tables)

    def rows(self, module: str) -> list[dict[str, Any]]:
        return self._tables.setdefault(module, [])

    def find(self, module: str, entry_id: int) -> dict[str, Any] | None:
        for row in self.rows(module):
            if int(row.get("id", 0)) == entry_id:
                return row
        return None

    def batch_queue(self, module: str, flight: str) -> list[dict[str, Any]]:
        """取某个航班在某模块下的待办队列；没有时建一条空队列。"""
        return self._batch_queues.setdefault(f"{module}:{flight}", [])

    def batch_flights(self, module: str) -> list[str]:
        """列出该模块下已经有队列的航班号。"""
        prefix = f"{module}:"
        return sorted(key[len(prefix):] for key in self._batch_queues if key.startswith(prefix))

    def batch_request(self, request_id: str) -> dict[str, Any] | None:
        """按填报编号查已受理的结果，用于重复提交只认第一次。"""
        return self._batch_requests.get(request_id)

    def remember_batch_request(self, request_id: str, result: dict[str, Any]) -> None:
        self._batch_requests[request_id] = result

    def overview(self) -> dict[str, object]:
        modules: list[dict[str, object]] = []
        for name in self.module_names():
            rows = self.rows(name)
            modules.append({
                "name": name,
                "created": len(rows),
                "pending": sum(1 for row in rows if row.get("pending")),
                "abnormal": sum(1 for row in rows if row.get("abnormal")),
            })
        cards = [
            {"label": "业务模块", "value": len(modules)},
            {"label": "今日新增", "value": sum(int(item["created"]) for item in modules)},
            {"label": "待处理", "value": sum(int(item["pending"]) for item in modules)},
            {"label": "异常量", "value": sum(int(item["abnormal"]) for item in modules)},
        ]
        return {"cards": cards, "modules": modules}


store = Store()
