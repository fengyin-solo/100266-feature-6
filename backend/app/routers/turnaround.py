"""过站保障接口：维护过站任务，覆盖启动保障、完成保障、记录延误等动作。

批量下发（待办队列、统一下发、补跑）的接口也收在这里，业务规则在
app/services/turnaround.py 的 TurnaroundBatchService 里。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, BatchDispatchPayload, BatchEnqueuePayload, EntryPayload, PageResult
from app.services.turnaround import TurnaroundBatchService, TurnaroundService

router = APIRouter(prefix="/api/turnaround", tags=["过站保障"])

service = TurnaroundService()
batch_service = TurnaroundBatchService()

LIST_FIELDS = ["过站编号", "对应航班", "计划过站时间", "实际过站时间", "过站延误", "延误原因", "协调记录", "过站状态"]
STATUSES = ["待保障", "保障中", "已保障", "延误中"]


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按过站编号检索"),
    status: str | None = Query(default=None, description="待保障、保障中、已保障、延误中"),
    flight: str | None = Query(default=None, description="按对应航班过滤，批量下发勾选同一航班保障项时用"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按过站编号、航班与状态过滤过站保障列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, flight=flight, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/batch/queue")
def batch_queue(flight: str | None = Query(default=None, description="要查看待办队列的航班号")) -> dict[str, Any]:
    """读取某航班的待办队列快照；抽屉与统计入口共用这一份数据。"""
    if not (flight or "").strip():
        raise HTTPException(status_code=400, detail="缺少航班号，暂时取不到队列数据，请补上航班后重试")
    return batch_service.queue_snapshot(flight.strip())


@router.get("/batch/summary")
def batch_summary() -> dict[str, Any]:
    """待收尾汇总：列表页统计卡从这里读，与抽屉里的队列同源。"""
    return batch_service.summary()


@router.post("/batch/enqueue")
def batch_enqueue(payload: BatchEnqueuePayload) -> dict[str, Any]:
    """把勾选的保障项放进待办队列；同一份填报重复送进来时只认第一次。"""
    result, duplicated = batch_service.enqueue(
        request_id=payload.request_id,
        flight=payload.flight,
        entry_ids=payload.entry_ids,
        crew=payload.crew,
        coordination=payload.coordination,
    )
    if duplicated:
        return {**result, "duplicated": True, "message": "这份填报已受理过，按第一次的结果为准，未重复入队"}
    return {**result, "duplicated": False}


@router.post("/batch/dispatch")
def batch_dispatch(payload: BatchDispatchPayload) -> dict[str, Any]:
    """统一下发：按队列顺序一段一段推进，只收尾走完最后环节的条目。

    中断后再次调用只会补跑没成功的那几条，已收尾的不会被拉回来。
    """
    return batch_service.dispatch(flight=payload.flight, stop_after=payload.stop_after)


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条过站任务明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"过站任务 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条过站任务，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="过站任务已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条过站任务执行启动保障、完成保障、记录延误；不允许的动作会被拦下并说明原因。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action, payload.values)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出过站保障清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "turnaround", "total": total, "items": items}
