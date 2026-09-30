"""批量下发接口：把同一航班的保障项收进侧边抽屉，入待办队列后统一下发、收尾。

路由层只做参数接收与结果包装，业务判断都在 DispatchService 里。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.schemas import (
    DelayReasonPayload,
    DispatchBatchPayload,
    DispatchRunPayload,
    UpstreamSwitchPayload,
)
from app.services.dispatch import BATCH_DONE, UpstreamUnavailable, dispatch_service

router = APIRouter(prefix="/api/turnaround/dispatch", tags=["过站保障-批量下发"])


@router.get("/upstream")
def upstream_status() -> dict[str, Any]:
    """协调数据通道当前是否可用（抽屉要在取不到数据时写明原因并留重试）。"""
    return dispatch_service.upstream()


@router.post("/upstream")
def switch_upstream(payload: UpstreamSwitchPayload) -> dict[str, Any]:
    """演练开关：断开后队列与收尾都报“暂时取不到数据”，恢复后可原样重试。"""
    return dispatch_service.set_upstream(payload.available)


@router.get("/pending-close", response_model=None)
def pending_close() -> JSONResponse | dict[str, Any]:
    """另一个入口读的待收尾数：与抽屉里的队列同源，直接数未收尾条目。"""
    try:
        return dispatch_service.pending_close_count()
    except UpstreamUnavailable as exc:
        return JSONResponse(
            status_code=503,
            content={"ok": False, "code": "upstream_unavailable", "message": exc.reason},
        )


@router.get("/batches", response_model=None)
def list_batches() -> JSONResponse | list[dict[str, Any]]:
    """读取待办队列（含已收尾批次）；通道断开时写明原因，前端在抽屉里留重试。"""
    try:
        return dispatch_service.list_batches()
    except UpstreamUnavailable as exc:
        return JSONResponse(
            status_code=503,
            content={"ok": False, "code": "upstream_unavailable", "message": exc.reason},
        )


@router.post("/batches")
def create_batch(payload: DispatchBatchPayload) -> dict[str, Any]:
    """勾好同一航班的保障项，补上保障班组与协调记录后放进待办队列。"""
    batch, message, duplicated = dispatch_service.create_batch(
        payload.entry_ids,
        payload.crew,
        payload.coord_note,
        flight_no=payload.flight_no,
        request_id=payload.request_id,
    )
    if batch is None:
        return {"ok": False, "message": message, "duplicated": False, "batch": None}
    return {"ok": True, "message": message, "duplicated": duplicated, "batch": batch}


@router.post("/batches/{batch_id}/run")
def run_batch(batch_id: int, payload: DispatchRunPayload) -> dict[str, Any]:
    """统一下发：按顺序一段一段推进；中断后只补跑没成功的条目。"""
    batch, message, summary = dispatch_service.dispatch(batch_id, request_id=payload.request_id)
    if batch is None:
        return {"ok": False, "message": message, "batch": None, "summary": {}}
    finished = batch["status"] == BATCH_DONE
    return {
        "ok": True,
        "finished": finished,
        "interrupted": batch["status"] == "下发中断",
        "message": message,
        "batch": batch,
        "summary": summary,
    }


@router.post("/batches/{batch_id}/items/{entry_id}/delay-reason")
def fill_delay_reason(batch_id: int, entry_id: int, payload: DelayReasonPayload) -> dict[str, Any]:
    """延误原因缺失被拦下后，在抽屉底部补录，补好即可继续下发收尾。"""
    batch, message = dispatch_service.fill_delay_reason(batch_id, entry_id, payload.reason)
    if batch is None:
        return {"ok": False, "message": message, "batch": None}
    return {"ok": True, "message": message, "batch": batch}
