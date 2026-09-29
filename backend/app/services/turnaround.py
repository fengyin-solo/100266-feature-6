"""过站保障业务规则：状态流转、字段校验与筛选口径都收在这里。"""
from __future__ import annotations

from typing import Any

from app.store import store

MODULE = "turnaround"
REQUIRED_FIELDS = ["过站编号", "对应航班", "计划过站时间"]
STATUS_ORDER = ["待保障", "保障中", "已保障", "延误中"]
ACTION_RULES = {"启动保障": "保障中", "完成保障": "已保障", "记录延误": "延误中"}
NEGATIVE_ACTIONS = []

# 批量下发的队列环节：待启动 -> 保障中 -> 已收尾；只收尾走完最后环节的条目。
BATCH_STAGES = ["待启动", "保障中", "已收尾"]
FINAL_STAGE = BATCH_STAGES[-1]


class TurnaroundService:
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        flight: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("过站编号", ""))]
        if flight:
            rows = [row for row in rows if flight in str(row.get("对应航班", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return entry, []

    def run_action(self, entry_id: int, action: str, values: dict[str, Any] | None = None) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"过站任务 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于过站保障可执行范围"
        if action == "记录延误":
            reason = str((values or {}).get("延误原因") or "").strip()
            if not reason:
                return None, "记录延误时请填写延误原因，否则后续无法收尾"
            entry["延误原因"] = reason
            entry["过站延误"] = "是"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry["status"] = target
        entry["过站状态"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return entry, f"过站任务已{action}"


class TurnaroundBatchService:
    """批量下发：同一航班的保障项先入待办队列，再按顺序一段一段推进、逐条收尾。"""

    def queue_snapshot(self, flight: str) -> dict[str, Any]:
        """读取某个航班的待办队列；抽屉与统计入口共用这一份快照。"""
        items = [self._item_view(item) for item in store.batch_queue(MODULE, flight)]
        return {
            "flight": flight,
            "items": items,
            "pending_closeout": sum(1 for item in items if item["stage"] != FINAL_STAGE),
            "failed": [item for item in items if item["error"]],
        }

    def summary(self) -> dict[str, Any]:
        """待收尾汇总：另一个入口（列表页统计卡）从这里读，和抽屉里的队列同源。"""
        flights = []
        for flight in store.batch_flights(MODULE):
            snapshot = self.queue_snapshot(flight)
            flights.append({
                "flight": flight,
                "pending_closeout": snapshot["pending_closeout"],
                "queued": len(snapshot["items"]),
            })
        return {
            "pending_closeout": sum(item["pending_closeout"] for item in flights),
            "flights": flights,
        }

    def enqueue(
        self,
        *,
        request_id: str,
        flight: str,
        entry_ids: list[int],
        crew: str,
        coordination: str,
    ) -> tuple[dict[str, Any], bool]:
        """把勾选的保障项放进待办队列；返回 (受理结果, 是否重复提交)。"""
        recorded = store.batch_request(request_id)
        if recorded is not None:
            return recorded, True
        missing = []
        if not crew.strip():
            missing.append("保障班组")
        if not coordination.strip():
            missing.append("协调记录")
        if not entry_ids:
            missing.append("保障项")
        if missing:
            result = {"ok": False, "message": f"下发前请先补齐：{'、'.join(missing)}", "queued": [], "rejected": []}
            store.remember_batch_request(request_id, result)
            return result, False

        queue = store.batch_queue(MODULE, flight)
        queued: list[dict[str, Any]] = []
        rejected: list[dict[str, Any]] = []
        for entry_id in dict.fromkeys(entry_ids):
            entry = store.find(MODULE, entry_id)
            if entry is None:
                rejected.append({"entry_id": entry_id, "reason": f"过站任务 {entry_id} 不存在或已归档"})
                continue
            if str(entry.get("对应航班", "")).strip() != flight.strip():
                rejected.append({"entry_id": entry_id, "reason": f"过站任务 {entry.get('过站编号', entry_id)} 不属于航班 {flight}，不能混进同一批"})
                continue
            if entry.get("status") == "已保障":
                rejected.append({"entry_id": entry_id, "reason": f"过站任务 {entry.get('过站编号', entry_id)} 已收尾，不再重复入队"})
                continue
            if any(int(item["entry_id"]) == entry_id for item in queue):
                rejected.append({"entry_id": entry_id, "reason": f"过站任务 {entry.get('过站编号', entry_id)} 已在待办队列中"})
                continue
            item = {
                "entry_id": entry_id,
                "过站编号": entry.get("过站编号", ""),
                "stage": BATCH_STAGES[0],
                "crew": crew.strip(),
                "coordination": coordination.strip(),
                "error": None,
            }
            queue.append(item)
            queued.append(self._item_view(item))
        result = {
            "ok": True,
            "message": f"已把 {len(queued)} 条保障项放进 {flight} 的待办队列" if queued else "没有可入队的保障项",
            "queued": queued,
            "rejected": rejected,
        }
        store.remember_batch_request(request_id, result)
        return result, False

    def dispatch(self, *, flight: str, stop_after: int | None = None) -> dict[str, Any]:
        """按队列顺序推进：每条依次走 待启动->保障中->已收尾。

        中断（stop_after）时没推进到的条目原样留在队列；已收尾的条目不会被
        拉回来重跑，补跑只处理没成功的那几条。
        """
        queue = store.batch_queue(MODULE, flight)
        if not queue:
            return {"ok": False, "message": f"航班 {flight} 的待办队列是空的，请先勾选保障项再下发", "finished": [], "failures": [], "remaining": 0}

        finished: list[dict[str, Any]] = []
        failures: list[dict[str, Any]] = []
        attempted = 0
        interrupted = False
        for item in queue:
            if item["stage"] == FINAL_STAGE:
                continue  # 已经完成的不会被拉回来
            if stop_after is not None and attempted >= stop_after:
                interrupted = True
                break
            attempted += 1
            item["error"] = None
            reason = self._advance(item)
            if reason is None:
                finished.append(self._item_view(item))
            else:
                item["error"] = reason
                failures.append({**self._item_view(item), "reason": reason})

        remaining = sum(1 for item in queue if item["stage"] != FINAL_STAGE)
        if interrupted:
            message = f"下发中断：已推进 {attempted} 条，其余 {remaining} 条留在队列里等补跑"
        elif failures:
            message = f"下发完成：{len(finished)} 条已收尾，{len(failures)} 条失败列在下方"
        elif remaining:
            message = f"下发完成：{len(finished)} 条已收尾，{remaining} 条仍在队列中"
        else:
            message = f"下发完成：{len(finished)} 条全部收尾，队列已清空"
        return {"ok": not failures, "message": message, "finished": finished, "failures": failures, "remaining": remaining, "interrupted": interrupted}

    def _advance(self, item: dict[str, Any]) -> str | None:
        """把一条队列项推进到已收尾；走不通时返回原因，条目留在当前环节。"""
        entry = store.find(MODULE, int(item["entry_id"]))
        if entry is None:
            return f"过站任务 {item['entry_id']} 不存在或已归档"
        if item["stage"] == BATCH_STAGES[0]:
            if entry.get("status") == "待保障":
                entry["status"] = "保障中"
                entry["过站状态"] = "保障中"
            item["stage"] = BATCH_STAGES[1]
        return self._close_out(item, entry)

    def _close_out(self, item: dict[str, Any], entry: dict[str, Any]) -> str | None:
        """收尾前校验：延误中的任务必须填了延误原因才允许收尾。"""
        if entry.get("status") == "延误中" and not str(entry.get("延误原因") or "").strip():
            return "延误原因缺失，不允许收尾"
        entry["status"] = "已保障"
        entry["过站状态"] = "已保障"
        entry["pending"] = False
        entry["协调记录"] = item["coordination"]
        entry["保障班组"] = item["crew"]
        item["stage"] = FINAL_STAGE
        return None

    def _item_view(self, item: dict[str, Any]) -> dict[str, Any]:
        return {
            "entry_id": item["entry_id"],
            "过站编号": item["过站编号"],
            "stage": item["stage"],
            "crew": item["crew"],
            "coordination": item["coordination"],
            "error": item["error"],
        }
