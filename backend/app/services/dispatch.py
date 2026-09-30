"""批量下发业务规则：同一航班的保障项先入待办队列，再按段顺序下发、收尾。

设计要点（对应班长批量办理的口径）：

- 勾好的保障项先成批放进队列，补上保障班组与协调记录后再统一下发；
- 下发按条目顺序一段一段推进：待下发 → 已启动 → 已收尾，
  没到收尾段的条目继续留在队列里，只收尾已经启动完成的那些；
- 中途中断（数据取不到、缺延误原因）会停在失败的那条，重试时只补跑
  没成功的条目，已经收尾的不会被拉回来；
- 同一份填报（同航班、同条目、同班组与协调记录）重复送进来只认第一次；
- 待收尾数直接数队列里未收尾的条目，和抽屉看到的队列同源。

状态流转只允许在这里改，路由层不做业务判断。
"""
from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any

from app.store import store

MODULE = "turnaround"
FLIGHT_FIELD = "对应航班"
CODE_FIELD = "过站编号"
DELAY_REASON_FIELD = "延误原因"
COORD_FIELD = "协调记录"
CREW_FIELD = "保障班组"

ENTRY_STARTED_STATUS = "保障中"
ENTRY_FINAL_STATUS = "已保障"
ENTRY_ACTIVE_STATUSES = ("待保障", "保障中")

# 条目在队列里的状态
ITEM_PENDING = "待下发"
ITEM_STARTED = "已启动"
ITEM_CLOSED = "已收尾"
ITEM_FAILED = "失败"

# 批次状态
BATCH_QUEUED = "队列中"
BATCH_RUNNING = "下发中"
BATCH_INTERRUPTED = "下发中断"
BATCH_DONE = "已收尾"

UPSTREAM_REASON = "协调数据通道暂时取不到数据，队列进度已保留，通道恢复后请重试"
MISSING_DELAY_REASON = "延误原因缺失，按规定不允许收尾，请补录延误原因后重试"


class UpstreamUnavailable(Exception):
    """收尾依赖的协调数据暂时取不到：属于可重试的临时故障，不是业务驳回。"""

    def __init__(self, reason: str = UPSTREAM_REASON) -> None:
        super().__init__(reason)
        self.reason = reason


class DispatchService:
    def __init__(self) -> None:
        self._batches: list[dict[str, Any]] = []
        self._fingerprints: dict[str, int] = {}
        self._seq = 0
        # 收尾要读取协调数据；演练时可以断开，模拟“暂时取不到数据”
        self._upstream_available = True

    # ------------------------------------------------------------------
    # 协调数据通道：演练开关与状态
    # ------------------------------------------------------------------
    def upstream(self) -> dict[str, Any]:
        return {
            "available": self._upstream_available,
            "reason": None if self._upstream_available else UPSTREAM_REASON,
        }

    def set_upstream(self, available: bool) -> dict[str, Any]:
        self._upstream_available = bool(available)
        return self.upstream()

    def _require_upstream(self) -> None:
        if not self._upstream_available:
            raise UpstreamUnavailable

    # ------------------------------------------------------------------
    # 查询：队列列表与“待收尾数”同一份数据
    # ------------------------------------------------------------------
    def list_batches(self) -> list[dict[str, Any]]:
        # 通道断的时候连队列也取不到：抽屉里写明原因并留重试，不返回半截数据
        self._require_upstream()
        return [self._serialize(batch) for batch in self._batches]

    def pending_close_count(self) -> dict[str, Any]:
        """另一个入口读的待收尾数：直接数队列中未收尾的条目，保证与抽屉同源。"""
        self._require_upstream()
        count = sum(
            1
            for batch in self._batches
            for item in batch["items"]
            if item["status"] != ITEM_CLOSED
        )
        active_batches = sum(1 for batch in self._batches if batch["status"] != BATCH_DONE)
        return {"count": count, "active_batches": active_batches, "source": "dispatch-queue"}

    # ------------------------------------------------------------------
    # 放进待办队列（同一份填报只认第一次）
    # ------------------------------------------------------------------
    def create_batch(
        self,
        entry_ids: list[int],
        crew: str,
        coord_note: str,
        flight_no: str | None = None,
        request_id: str | None = None,
    ) -> tuple[dict[str, Any] | None, str, bool]:
        ids = [int(value) for value in entry_ids]
        if not ids:
            return None, "请至少勾选一个保障项再放进队列", False
        crew = (crew or "").strip()
        coord_note = (coord_note or "").strip()
        if not crew:
            return None, "保障班组为必填项，补上后再放进队列", False
        if not coord_note:
            return None, "协调记录为必填项，补上后再统一下发", False

        entries: list[dict[str, Any]] = []
        missing: list[str] = []
        for entry_id in ids:
            row = store.find(MODULE, entry_id)
            if row is None:
                missing.append(str(entry_id))
            else:
                entries.append(row)
        if missing:
            return None, f"保障项 {'、'.join(missing)} 不存在或已归档，请刷新后重新勾选", False

        flights = {str(row.get(FLIGHT_FIELD) or "").strip() for row in entries}
        if len(flights) > 1:
            return None, "一个抽屉只办理同一航班的保障项，勾到别的航班了", False
        flight = (flight_no or "").strip() or next(iter(flights))

        closed_codes = [
            str(row.get(CODE_FIELD))
            for row in entries
            if row.get("status") == ENTRY_FINAL_STATUS
        ]
        if closed_codes:
            return None, f"保障项 {'、'.join(closed_codes)} 已收尾，不能重复下发", False

        # 同一份填报：有客户端 request_id 就用它，否则按航班+条目+班组+协调记录生成指纹
        fingerprint_seed = (request_id or "").strip() or "|".join(
            [flight, ",".join(str(value) for value in sorted(ids)), crew, coord_note]
        )
        fingerprint = hashlib.sha1(fingerprint_seed.encode("utf-8")).hexdigest()
        existed_id = self._fingerprints.get(fingerprint)
        if existed_id is not None:
            existed = self._find_batch(existed_id)
            if existed is not None:
                # 已经收尾的条目原样保留，重复填报不会产生第二份队列
                return self._serialize(existed), "同一份填报重复送进来只认第一次，已返回原队列", True

        self._seq += 1
        batch: dict[str, Any] = {
            "id": self._seq,
            "flight_no": flight,
            "crew": crew,
            "coord_note": coord_note,
            "status": BATCH_QUEUED,
            "fingerprint": fingerprint,
            "request_log": {},
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "items": [],
        }
        for row in entries:
            # 列表里已经在保障中的条目视为启动段已走完，入队后等收尾
            initial = ITEM_STARTED if row.get("status") == ENTRY_STARTED_STATUS else ITEM_PENDING
            batch["items"].append(
                {
                    "entry_id": int(row["id"]),
                    "status": initial,
                    "failure_reason": None,
                    "retryable": False,
                    "closed_at": None,
                }
            )
        self._batches.append(batch)
        self._fingerprints[fingerprint] = batch["id"]
        return self._serialize(batch), f"已把 {len(ids)} 个保障项放进 {flight} 的待办队列", False

    # ------------------------------------------------------------------
    # 统一下发：按顺序一段一段推进，失败即中断，重试只补跑没成功的
    # ------------------------------------------------------------------
    def dispatch(
        self, batch_id: int, request_id: str | None = None
    ) -> tuple[dict[str, Any] | None, str, dict[str, int]]:
        batch = self._find_batch(batch_id)
        if batch is None:
            return None, f"批次 {batch_id} 不存在或已清理", {}

        request_id = (request_id or "").strip()
        if request_id and request_id in batch["request_log"]:
            cached = dict(batch["request_log"][request_id])
            return (
                self._serialize(batch),
                "同一下发指令已送过，沿用首次执行结果，已收尾条目不会重跑",
                cached,
            )

        summary = {"started": 0, "closed": 0, "failed": 0}
        for item in batch["items"]:
            if item["status"] == ITEM_CLOSED:
                # 已经收尾的不会被拉回来，重试时直接跳过
                continue
            entry = store.find(MODULE, item["entry_id"])
            code = str(entry.get(CODE_FIELD) if entry else item["entry_id"])
            if entry is None:
                self._mark_failed(item, f"保障项 {code} 已归档，找不到原记录", retryable=True)
                summary["failed"] += 1
                batch["status"] = BATCH_INTERRUPTED
                self._log_request(batch, request_id, summary)
                return self._serialize(batch), f"下发中断：{item['failure_reason']}", summary

            if item["status"] == ITEM_PENDING:
                # 第一段：启动保障。本次只推进到“已启动”，留在队列里等收尾
                entry["status"] = ENTRY_STARTED_STATUS
                entry["pending"] = True
                item["status"] = ITEM_STARTED
                item["failure_reason"] = None
                item["retryable"] = False
                summary["started"] += 1
                continue

            # 第二段：收尾已经启动完成的条目。先过业务校验，再取协调数据
            delay_reason = str(entry.get(DELAY_REASON_FIELD) or "").strip()
            if not delay_reason:
                self._mark_failed(item, MISSING_DELAY_REASON, retryable=False)
                summary["failed"] += 1
                batch["status"] = BATCH_INTERRUPTED
                self._log_request(batch, request_id, summary)
                return self._serialize(batch), f"下发中断：{code} {MISSING_DELAY_REASON}", summary

            try:
                self._require_upstream()
            except UpstreamUnavailable as exc:
                self._mark_failed(item, f"{code}：{exc.reason}", retryable=True)
                summary["failed"] += 1
                batch["status"] = BATCH_INTERRUPTED
                self._log_request(batch, request_id, summary)
                return self._serialize(batch), f"下发中断：{item['failure_reason']}", summary

            entry["status"] = ENTRY_FINAL_STATUS
            entry["pending"] = False
            entry[CREW_FIELD] = batch["crew"]
            entry[COORD_FIELD] = batch["coord_note"]
            item["status"] = ITEM_CLOSED
            item["failure_reason"] = None
            item["retryable"] = False
            item["closed_at"] = datetime.now().isoformat(timespec="seconds")
            summary["closed"] += 1

        all_closed = all(item["status"] == ITEM_CLOSED for item in batch["items"])
        batch["status"] = BATCH_DONE if all_closed else BATCH_RUNNING
        self._log_request(batch, request_id, summary)
        if all_closed:
            return self._serialize(batch), f"航班 {batch['flight_no']} 的保障项已全部收尾", summary
        return (
            self._serialize(batch),
            f"本次下发：启动 {summary['started']} 条、收尾 {summary['closed']} 条，"
            "没到收尾环节的仍留在队列里",
            summary,
        )

    # ------------------------------------------------------------------
    # 补录延误原因：补好后失败条目回到已启动，可继续下发
    # ------------------------------------------------------------------
    def fill_delay_reason(
        self, batch_id: int, entry_id: int, reason: str
    ) -> tuple[dict[str, Any] | None, str]:
        batch = self._find_batch(batch_id)
        if batch is None:
            return None, f"批次 {batch_id} 不存在或已清理"
        item = next((one for one in batch["items"] if one["entry_id"] == entry_id), None)
        if item is None:
            return None, f"保障项 {entry_id} 不在该批次队列里"
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"保障项 {entry_id} 已归档，找不到原记录"
        reason = (reason or "").strip()
        if not reason:
            return None, "延误原因不能为空，补录后才允许收尾"
        entry[DELAY_REASON_FIELD] = reason
        if item["status"] == ITEM_FAILED and item["failure_reason"] == MISSING_DELAY_REASON:
            item["status"] = ITEM_STARTED
            item["failure_reason"] = None
            item["retryable"] = False
            if batch["status"] == BATCH_INTERRUPTED:
                batch["status"] = BATCH_RUNNING
        return self._serialize(batch), f"保障项 {entry.get(CODE_FIELD)} 的延误原因已补录，可继续下发"

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------
    def _find_batch(self, batch_id: int) -> dict[str, Any] | None:
        return next((batch for batch in self._batches if batch["id"] == batch_id), None)

    @staticmethod
    def _mark_failed(item: dict[str, Any], reason: str, *, retryable: bool) -> None:
        item["status"] = ITEM_FAILED
        item["failure_reason"] = reason
        item["retryable"] = retryable

    @staticmethod
    def _log_request(
        batch: dict[str, Any], request_id: str, summary: dict[str, int]
    ) -> None:
        if request_id:
            batch["request_log"][request_id] = dict(summary)

    def _serialize(self, batch: dict[str, Any]) -> dict[str, Any]:
        items: list[dict[str, Any]] = []
        for item in batch["items"]:
            entry = store.find(MODULE, item["entry_id"])
            items.append(
                {
                    "entry_id": item["entry_id"],
                    "code": str(entry.get(CODE_FIELD)) if entry else f"#{item['entry_id']}",
                    "flight_no": str(entry.get(FLIGHT_FIELD)) if entry else batch["flight_no"],
                    "status": item["status"],
                    "failure_reason": item["failure_reason"],
                    "retryable": item["retryable"],
                    "delay_reason": (str(entry.get(DELAY_REASON_FIELD) or "").strip() or None)
                    if entry
                    else None,
                    "entry_status": entry.get("status") if entry else "已归档",
                    "closed_at": item["closed_at"],
                }
            )
        closed = sum(1 for item in items if item["status"] == ITEM_CLOSED)
        failed = sum(1 for item in items if item["status"] == ITEM_FAILED)
        return {
            "id": batch["id"],
            "flight_no": batch["flight_no"],
            "crew": batch["crew"],
            "coord_note": batch["coord_note"],
            "status": batch["status"],
            "created_at": batch["created_at"],
            "items": items,
            "total": len(items),
            "closed": closed,
            "failed": failed,
            "pending": len(items) - closed,
        }


dispatch_service = DispatchService()
