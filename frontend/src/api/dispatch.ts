/** 批量下发接口封装：入待办队列、分段下发、补录延误原因、待收尾数与通道状态。 */
import { request } from './client'

export type DispatchItemStatus = '待下发' | '已启动' | '已收尾' | '失败'
export type BatchStatus = '队列中' | '下发中' | '下发中断' | '已收尾'

export interface DispatchItem {
  entry_id: number
  code: string
  flight_no: string
  status: DispatchItemStatus
  failure_reason: string | null
  retryable: boolean
  delay_reason: string | null
  entry_status: string
  closed_at: string | null
}

export interface DispatchBatch {
  id: number
  flight_no: string
  crew: string
  coord_note: string
  status: BatchStatus
  created_at: string
  items: DispatchItem[]
  total: number
  closed: number
  failed: number
  pending: number
}

export interface RunSummary {
  started: number
  closed: number
  failed: number
}

export interface RunResult {
  ok: boolean
  finished: boolean
  interrupted: boolean
  message: string
  batch: DispatchBatch | null
  summary: RunSummary
}

export interface PendingClose {
  count: number
  active_batches: number
  source: string
}

export interface UpstreamState {
  available: boolean
  reason: string | null
}

async function asJson<T>(response: Response): Promise<T> {
  const data = (await response.json().catch(() => ({}))) as Record<string, unknown>
  if (!response.ok) {
    const message = typeof data.message === 'string' ? data.message : `接口返回 ${response.status}，数据未更新`
    const error = new Error(message) as Error & { status?: number; code?: string }
    error.status = response.status
    error.code = typeof data.code === 'string' ? data.code : undefined
    throw error
  }
  return data as T
}

export async function fetchBatches(): Promise<DispatchBatch[]> {
  const response = await request('/api/turnaround/dispatch/batches')
  return asJson<DispatchBatch[]>(response)
}

export async function fetchPendingClose(): Promise<PendingClose> {
  const response = await request('/api/turnaround/dispatch/pending-close')
  return asJson<PendingClose>(response)
}

export async function fetchUpstream(): Promise<UpstreamState> {
  const response = await request('/api/turnaround/dispatch/upstream')
  return asJson<UpstreamState>(response)
}

export async function setUpstream(available: boolean): Promise<UpstreamState> {
  const response = await request('/api/turnaround/dispatch/upstream', {
    method: 'POST',
    body: JSON.stringify({ available }),
  })
  return asJson<UpstreamState>(response)
}

export async function enqueueBatch(body: {
  entry_ids: number[]
  crew: string
  coord_note: string
  request_id?: string
}): Promise<{ ok: boolean; message: string; duplicated: boolean; batch: DispatchBatch | null }> {
  // 同一份填报带稳定幂等键：内容指纹由后端算，前端在网络重试时也能拿到首次结果
  const requestId =
    body.request_id ??
    [
      'enq',
      [...body.entry_ids].sort((a, b) => a - b).join(','),
      body.crew,
      body.coord_note,
    ].join('|')
  const response = await request('/api/turnaround/dispatch/batches', {
    method: 'POST',
    body: JSON.stringify({ ...body, request_id: requestId }),
  })
  return asJson(response)
}

export async function runBatch(batchId: number): Promise<RunResult> {
  // 一次补跑一个幂等键：中断后再次点"补跑"会换键重试，但同一指令重发沿用首次结果
  const requestId = `run-${batchId}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
  const response = await request(`/api/turnaround/dispatch/batches/${batchId}/run`, {
    method: 'POST',
    body: JSON.stringify({ request_id: requestId }),
  })
  return asJson<RunResult>(response)
}

export async function fillDelayReason(
  batchId: number,
  entryId: number,
  reason: string,
): Promise<{ ok: boolean; message: string; batch: DispatchBatch | null }> {
  const response = await request(
    `/api/turnaround/dispatch/batches/${batchId}/items/${entryId}/delay-reason`,
    { method: 'POST', body: JSON.stringify({ reason }) },
  )
  return asJson(response)
}
