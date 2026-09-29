<template>
  <div class="drawer-mask" @click.self="emit('close')">
    <aside class="drawer" data-module="turnaround-batch">
      <header class="drawer-head">
        <div>
          <h3>批量保障下发</h3>
          <p class="drawer-desc">勾选同一航班的保障项放入待办队列，补齐保障班组与协调记录后统一下发。</p>
        </div>
        <button class="btn ghost" type="button" @click="emit('close')">关闭</button>
      </header>

      <div class="drawer-body">
        <form class="drawer-flight" @submit.prevent="loadFlight">
          <label class="filter-item">
            <span>对应航班</span>
            <input v-model.trim="flight" placeholder="如 CA1234" />
          </label>
          <button class="btn" type="submit" :disabled="loading">载入保障项</button>
        </form>

        <p v-if="loadError" class="drawer-error">
          暂时取不到数据：{{ loadError }}
          <button class="link" type="button" @click="loadFlight">重试</button>
        </p>

        <template v-if="loaded && !loadError">
          <section class="drawer-section">
            <h4>一、勾选保障项（已选 {{ checkedIds.length }} 条）</h4>
            <ul class="check-list">
              <li v-for="row in candidates" :key="Number(row.id)">
                <label>
                  <input
                    v-model="checkedIds"
                    type="checkbox"
                    :value="Number(row.id)"
                    :disabled="row.status === '已保障'"
                  />
                  <span>{{ row['过站编号'] }}</span>
                  <em class="tag">{{ row.status }}</em>
                  <span v-if="row.status === '延误中' && !row['延误原因']" class="warn-text">延误原因未填，收尾会被拦下</span>
                </label>
              </li>
            </ul>
            <p v-if="!candidates.length" class="empty-state">该航班暂无保障项，可先登记过站任务</p>
          </section>

          <section class="drawer-section">
            <h4>二、补齐下发信息</h4>
            <label class="filter-item">
              <span>保障班组</span>
              <input v-model.trim="crew" placeholder="如 保障二班" />
            </label>
            <label class="filter-item">
              <span>协调记录</span>
              <textarea v-model.trim="coordination" rows="2" placeholder="与塔台、机组的协调结论"></textarea>
            </label>
            <label class="filter-item">
              <span>联调模拟中断（可选，推进几条后停下）</span>
              <input v-model.number="stopAfter" type="number" min="0" placeholder="不填则一次推完" />
            </label>
          </section>

          <section class="drawer-section">
            <h4>三、统一下发</h4>
            <div class="drawer-actions">
              <button class="btn primary" type="button" :disabled="dispatching" @click="dispatchAll">统一下发</button>
              <button
                class="btn"
                type="button"
                :disabled="dispatching || !queue || queue.pending_closeout === 0"
                @click="retryQueue"
              >
                补跑未完成
              </button>
            </div>
            <p v-if="resultMessage" class="result-text">{{ resultMessage }}</p>
          </section>

          <section v-if="queue && queue.items.length" class="drawer-section">
            <h4>待办队列（待收尾 {{ queue.pending_closeout }} 条）</h4>
            <ul class="queue-list">
              <li v-for="item in queue.items" :key="item.entry_id">
                <span>{{ item['过站编号'] }}</span>
                <em class="tag" :class="{ done: item.stage === '已收尾' }">{{ item.stage }}</em>
              </li>
            </ul>
          </section>

          <section v-if="queue && queue.failed.length" class="drawer-section drawer-failures">
            <h4>失败条目（{{ queue.failed.length }}）</h4>
            <ul>
              <li v-for="item in queue.failed" :key="item.entry_id">
                <p><strong>{{ item['过站编号'] }}</strong>：{{ item.error }}</p>
                <div v-if="isDelayReasonMissing(item)" class="failure-fix">
                  <input v-model.trim="reasonDrafts[item.entry_id]" placeholder="补填延误原因" />
                  <button class="link" type="button" @click="fixDelayReason(item)">补齐原因并补跑</button>
                </div>
              </li>
            </ul>
          </section>
        </template>
      </div>
    </aside>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | null>

type QueueItem = {
  entry_id: number
  '过站编号': string
  stage: string
  crew: string
  coordination: string
  error: string | null
}

type QueueSnapshot = {
  flight: string
  items: QueueItem[]
  pending_closeout: number
  failed: QueueItem[]
}

const props = defineProps<{ initialFlight?: string }>()
const emit = defineEmits<{ close: []; changed: [] }>()

const ENDPOINT = '/api/turnaround'

const flight = ref(props.initialFlight ?? '')
const candidates = ref<Row[]>([])
const checkedIds = ref<number[]>([])
const crew = ref('')
const coordination = ref('')
const stopAfter = ref<number | null>(null)
const queue = ref<QueueSnapshot | null>(null)
const reasonDrafts = ref<Record<number, string>>({})

const loaded = ref(false)
const loading = ref(false)
const dispatching = ref(false)
const loadError = ref('')
const resultMessage = ref('')

// 填报编号由内容决定：同一份填报（同航班、同勾选、同班组与协调记录）重复送进来，
// 算出的编号相同，后端只认第一次；内容变了就是新填报。
function requestId(): string {
  const ids = [...checkedIds.value].sort((a, b) => a - b).join(',')
  const raw = `${flight.value}|${ids}|${crew.value}|${coordination.value}`
  let hash = 0
  for (let i = 0; i < raw.length; i += 1) {
    hash = (hash * 31 + raw.charCodeAt(i)) >>> 0
  }
  return `batch-${hash.toString(16)}`
}

function detailText(payload: unknown, fallback: string): string {
  const detail = (payload as { detail?: unknown })?.detail
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) return detail.map((item) => String(item?.msg ?? item)).join('；')
  return fallback
}

async function postJson(path: string, body: unknown): Promise<Record<string, any>> {
  const response = await request(path, { method: 'POST', body: JSON.stringify(body) })
  const payload = await response.json().catch(() => ({}))
  if (!response.ok) {
    throw new Error(detailText(payload, `接口返回 ${response.status}`))
  }
  return payload as Record<string, any>
}

async function loadFlight() {
  loadError.value = ''
  resultMessage.value = ''
  if (!flight.value) {
    loadError.value = '请先填写航班号再载入'
    return
  }
  loading.value = true
  try {
    const query = encodeURIComponent(flight.value)
    const [listResp, queueResp] = await Promise.all([
      request(`${ENDPOINT}?flight=${query}&size=200`),
      request(`${ENDPOINT}/batch/queue?flight=${query}`),
    ])
    if (!listResp.ok || !queueResp.ok) {
      const bad = listResp.ok ? queueResp : listResp
      throw new Error(detailText(await bad.json().catch(() => ({})), `接口返回 ${bad.status}`))
    }
    candidates.value = ((await listResp.json()).items ?? []) as Row[]
    queue.value = (await queueResp.json()) as QueueSnapshot
    const queued = new Set(queue.value.items.map((item) => item.entry_id))
    checkedIds.value = candidates.value
      .filter((row) => row.status !== '已保障' && !queued.has(Number(row.id)))
      .map((row) => Number(row.id))
    loaded.value = true
  } catch (error) {
    loadError.value = error instanceof Error ? error.message : '保障项列表读取失败'
  } finally {
    loading.value = false
  }
}

async function refreshQueue() {
  const query = encodeURIComponent(flight.value)
  const [listResp, queueResp] = await Promise.all([
    request(`${ENDPOINT}?flight=${query}&size=200`),
    request(`${ENDPOINT}/batch/queue?flight=${query}`),
  ])
  if (listResp.ok) {
    candidates.value = ((await listResp.json()).items ?? []) as Row[]
  }
  if (queueResp.ok) {
    queue.value = (await queueResp.json()) as QueueSnapshot
  }
}

async function runDispatch(): Promise<string> {
  const payload = await postJson(`${ENDPOINT}/batch/dispatch`, {
    flight: flight.value,
    stop_after: stopAfter.value && stopAfter.value > 0 ? stopAfter.value : null,
  })
  await refreshQueue()
  emit('changed')
  return String(payload.message ?? '下发完成')
}

async function dispatchAll() {
  resultMessage.value = ''
  dispatching.value = true
  try {
    const enqueuePayload = await postJson(`${ENDPOINT}/batch/enqueue`, {
      request_id: requestId(),
      flight: flight.value,
      entry_ids: checkedIds.value,
      crew: crew.value,
      coordination: coordination.value,
    })
    if (!enqueuePayload.ok) {
      throw new Error(String(enqueuePayload.message ?? '入队失败'))
    }
    const notes = [String(enqueuePayload.message ?? '')]
    const rejected = (enqueuePayload.rejected ?? []) as { reason: string }[]
    if (rejected.length) {
      notes.push(`未入队 ${rejected.length} 条：${rejected.map((item) => item.reason).join('；')}`)
    }
    notes.push(await runDispatch())
    resultMessage.value = notes.filter(Boolean).join('；')
  } catch (error) {
    resultMessage.value = error instanceof Error ? error.message : '统一下发失败'
    await refreshQueue()
  } finally {
    dispatching.value = false
  }
}

async function retryQueue() {
  resultMessage.value = ''
  dispatching.value = true
  try {
    resultMessage.value = await runDispatch()
  } catch (error) {
    resultMessage.value = error instanceof Error ? error.message : '补跑失败'
  } finally {
    dispatching.value = false
  }
}

function isDelayReasonMissing(item: QueueItem): boolean {
  return Boolean(item.error && item.error.includes('延误原因缺失'))
}

async function fixDelayReason(item: QueueItem) {
  const reason = (reasonDrafts.value[item.entry_id] ?? '').trim()
  if (!reason) {
    resultMessage.value = '请先在失败条目旁填写延误原因'
    return
  }
  dispatching.value = true
  resultMessage.value = ''
  try {
    const payload = await postJson(`${ENDPOINT}/${item.entry_id}/actions`, {
      values: { action: '记录延误', 延误原因: reason },
    })
    if (!payload.ok) {
      throw new Error(String(payload.message ?? '延误原因保存失败'))
    }
    resultMessage.value = await runDispatch()
  } catch (error) {
    resultMessage.value = error instanceof Error ? error.message : '补齐延误原因失败'
    await refreshQueue()
  } finally {
    dispatching.value = false
  }
}

if (flight.value) {
  void loadFlight()
}
</script>
