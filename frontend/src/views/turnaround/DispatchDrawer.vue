<template>
  <div v-if="open" class="drawer-mask" @click.self="emitClose">
    <aside class="dispatch-drawer" aria-label="过站保障批量下发抽屉">
      <header class="drawer-head">
        <div>
          <h3>批量下发 · 过站保障</h3>
          <p class="drawer-sub">勾好同一航班的保障项先放进待办队列，补上保障班组与协调记录后统一下发</p>
        </div>
        <button class="link" type="button" @click="emitClose">收起</button>
      </header>

      <div v-if="queueError" class="drawer-banner error" role="alert">
        <span>{{ queueError }}</span>
        <button class="btn small" type="button" :disabled="loadingQueue" @click="loadQueue">
          {{ loadingQueue ? '重试中…' : '重试' }}
        </button>
      </div>

      <section class="drawer-section">
        <h4>① 放进待办队列</h4>
        <div v-if="!selection.length" class="drawer-empty">还没有勾选保障项，请先在列表里勾选同一航班的条目</div>
        <div v-else class="selected-list">
          <div class="selected-meta">
            <span>{{ selectionFlight ? `航班 ${selectionFlight}` : '' }} · 已选 {{ selection.length }} 条</span>
            <button class="link" type="button" @click="emit('clear-selection')">清空选择</button>
          </div>
          <ul>
            <li v-for="row in selection" :key="String(row.id)">
              <span class="code">{{ row['过站编号'] }}</span>
              <span class="muted">{{ row['过站状态'] || row.status }}</span>
              <span v-if="!String(row['延误原因'] || '').trim()" class="tag warn">缺延误原因</span>
            </li>
          </ul>
          <label class="form-item">
            <span>保障班组 <em>*</em></span>
            <input v-model="crew" placeholder="如：过站甲班" />
          </label>
          <label class="form-item">
            <span>协调记录 <em>*</em></span>
            <textarea v-model="coordNote" rows="2" placeholder="记录本次保障的协调事项"></textarea>
          </label>
          <div class="section-actions">
            <button class="btn primary small" type="button" :disabled="enqueuing" @click="enqueue">
              {{ enqueuing ? '提交中…' : '放进队列' }}
            </button>
            <span v-if="enqueueMsg" class="form-msg" :class="enqueueOk ? 'ok' : 'error-text'">{{ enqueueMsg }}</span>
          </div>
        </div>
      </section>

      <section class="drawer-section grow">
        <div class="section-title-row">
          <h4>② 待办队列 <span class="muted small-text">（待收尾 {{ pendingCount }} 条，与队列同源）</span></h4>
          <button class="btn small ghost" type="button" :disabled="loadingQueue" @click="loadQueue">刷新</button>
        </div>

        <div v-if="loadingQueue" class="drawer-empty">队列加载中…</div>
        <div v-else-if="!batches.length" class="drawer-empty">队列为空，先把勾好的保障项放进队列</div>
        <div v-else class="queue-list">
          <article v-for="batch in batches" :key="batch.id" class="queue-card" :class="{ done: batch.status === '已收尾' }">
            <header class="queue-card-head">
              <div>
                <strong>航班 {{ batch.flight_no }}</strong>
                <span class="muted small-text">批次 #{{ batch.id }} · {{ batch.crew }} · {{ batch.status }}</span>
              </div>
              <div class="queue-card-actions">
                <button
                  v-if="batch.status !== '已收尾'"
                  class="btn small primary"
                  type="button"
                  :disabled="runningBatchId === batch.id"
                  @click="dispatch(batch.id)"
                >
                  {{ runningBatchId === batch.id ? '下发中…' : batch.pending === batch.total ? '统一下发' : '补跑未成功条目' }}
                </button>
                <span v-else class="tag ok">已全部收尾</span>
              </div>
            </header>
            <p class="coord-note muted small-text">协调记录：{{ batch.coord_note }}</p>
            <ol class="item-list">
              <li v-for="item in batch.items" :key="item.entry_id" :class="itemStatusClass(item.status)">
                <span class="item-code">{{ item.code }}</span>
                <span class="item-status" :class="itemStatusClass(item.status)">{{ item.status }}</span>
                <span v-if="item.failure_reason" class="item-reason">{{ item.failure_reason }}</span>
              </li>
            </ol>
          </article>
        </div>
      </section>

      <section v-if="failedItems.length" class="drawer-section failed-section">
        <h4>③ 失败条目（{{ failedItems.length }}）</h4>
        <ul class="failed-list">
          <li v-for="failed in failedItems" :key="`${failed.batchId}-${failed.item.entry_id}`">
            <div class="failed-head">
              <strong>{{ failed.item.code }}</strong>
              <span class="muted small-text">航班 {{ failed.item.flight_no }} · 批次 #{{ failed.batchId }}</span>
            </div>
            <p class="failed-reason">{{ failed.item.failure_reason }}</p>
            <div v-if="isMissingDelay(failed.item)" class="failed-actions">
              <input
                v-model="delayInputs[`${failed.batchId}-${failed.item.entry_id}`]"
                placeholder="补录延误原因后即可继续下发收尾"
              />
              <button
                class="btn small"
                type="button"
                :disabled="savingReasonKey === `${failed.batchId}-${failed.item.entry_id}`"
                @click="submitDelayReason(failed.batchId, failed.item)"
              >
                补录并继续
              </button>
            </div>
            <div v-else-if="failed.item.retryable" class="failed-actions">
              <button class="btn small primary" type="button" @click="dispatch(failed.batchId)">重试该批次</button>
              <span class="muted small-text">临时故障，重试只补跑没成功的条目，已收尾的不会重跑</span>
            </div>
          </li>
        </ul>
      </section>

      <footer class="drawer-foot">
        <label class="upstream-switch">
          <input
            type="checkbox"
            :checked="upstream.available"
            :disabled="switchingUpstream"
            @change="toggleUpstream(($event.target as HTMLInputElement).checked)"
          />
          协调数据通道{{ upstream.available ? '正常' : '已断开（演练）' }}
        </label>
        <span v-if="runMsg" class="form-msg" :class="runOk ? 'ok' : 'error-text'">{{ runMsg }}</span>
      </footer>
    </aside>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'

import {
  enqueueBatch,
  fetchBatches,
  fetchPendingClose,
  fetchUpstream,
  fillDelayReason,
  runBatch,
  setUpstream,
  type DispatchBatch,
  type DispatchItem,
  type UpstreamState,
} from '@/api/dispatch'

type Row = Record<string, string | number | null>

const props = defineProps<{
  open: boolean
  selection: Row[]
  selectionFlight: string
}>()

const emit = defineEmits<{
  (e: 'close'): void
  (e: 'clear-selection'): void
  (e: 'count-changed', count: number): void
}>()

const batches = ref<DispatchBatch[]>([])
const pendingCount = ref(0)
const loadingQueue = ref(false)
const queueError = ref('')
const upstream = ref<UpstreamState>({ available: true, reason: null })
const switchingUpstream = ref(false)

const crew = ref('')
const coordNote = ref('')
const enqueuing = ref(false)
const enqueueMsg = ref('')
const enqueueOk = ref(false)

const runningBatchId = ref<number | null>(null)
const runMsg = ref('')
const runOk = ref(false)
const savingReasonKey = ref('')
const delayInputs = ref<Record<string, string>>({})

const failedItems = computed(() =>
  batches.value.flatMap((batch) =>
    batch.items
      .filter((item) => item.status === '失败')
      .map((item) => ({ batchId: batch.id, item })),
  ),
)

function emitClose() {
  emit('close')
}

function itemStatusClass(status: DispatchItem['status']) {
  return {
    'is-closed': status === '已收尾',
    'is-started': status === '已启动',
    'is-failed': status === '失败',
  }
}

function isMissingDelay(item: DispatchItem) {
  return item.failure_reason?.includes('延误原因缺失') ?? false
}

async function loadQueue() {
  loadingQueue.value = true
  queueError.value = ''
  try {
    const [batchRows, pending] = await Promise.all([fetchBatches(), fetchPendingClose()])
    batches.value = batchRows
    pendingCount.value = pending.count
    emit('count-changed', pending.count)
  } catch (error) {
    // 暂时取不到数据时在抽屉里写明原因并留重试，不用半截数据糊弄
    queueError.value = error instanceof Error ? error.message : '队列暂时取不到数据，请稍后重试'
  } finally {
    loadingQueue.value = false
  }
}

async function loadUpstream() {
  try {
    upstream.value = await fetchUpstream()
  } catch {
    // 通道状态取不到时不阻断主流程，队列读取会给出同样的说明
  }
}

async function enqueue() {
  enqueuing.value = true
  enqueueMsg.value = ''
  try {
    const result = await enqueueBatch({
      entry_ids: props.selection.map((row) => Number(row.id)),
      crew: crew.value,
      coord_note: coordNote.value,
    })
    enqueueOk.value = result.ok
    enqueueMsg.value = result.message
    if (result.ok) {
      if (!result.duplicated) {
        crew.value = ''
        coordNote.value = ''
        emit('clear-selection')
      }
      await loadQueue()
    }
  } catch (error) {
    enqueueOk.value = false
    enqueueMsg.value = error instanceof Error ? error.message : '放进队列失败，请稍后重试'
  } finally {
    enqueuing.value = false
  }
}

async function dispatch(batchId: number) {
  runningBatchId.value = batchId
  runMsg.value = ''
  try {
    const result = await runBatch(batchId)
    runOk.value = result.ok && !result.interrupted
    runMsg.value = result.message
    // 无论成功还是中断都刷新：中断时失败条目要列到抽屉底部，成功条目要留在已收尾
    await loadQueue()
  } catch (error) {
    runOk.value = false
    runMsg.value = error instanceof Error ? error.message : '下发请求未送达，请稍后重试'
  } finally {
    runningBatchId.value = null
  }
}

async function submitDelayReason(batchId: number, item: DispatchItem) {
  const key = `${batchId}-${item.entry_id}`
  const reason = (delayInputs.value[key] || '').trim()
  if (!reason) {
    runOk.value = false
    runMsg.value = '延误原因不能为空，补录后才允许收尾'
    return
  }
  savingReasonKey.value = key
  runMsg.value = ''
  try {
    const result = await fillDelayReason(batchId, item.entry_id, reason)
    runOk.value = result.ok
    runMsg.value = result.message
    if (result.ok) {
      delayInputs.value[key] = ''
      // 补录后直接补跑该批次：只补没成功的条目，已收尾的不会拉回来
      await loadQueue()
      await dispatch(batchId)
    }
  } catch (error) {
    runOk.value = false
    runMsg.value = error instanceof Error ? error.message : '延误原因补录失败'
  } finally {
    savingReasonKey.value = ''
  }
}

async function toggleUpstream(available: boolean) {
  switchingUpstream.value = true
  try {
    upstream.value = await setUpstream(available)
    await loadQueue()
  } catch (error) {
    queueError.value = error instanceof Error ? error.message : '通道状态切换失败'
  } finally {
    switchingUpstream.value = false
  }
}

watch(
  () => props.open,
  (open) => {
    if (open) {
      void loadUpstream()
      void loadQueue()
    }
  },
  { immediate: true },
)
</script>

<style scoped>
.drawer-mask {
  position: fixed;
  inset: 0;
  background: rgba(15, 23, 42, 0.35);
  display: flex;
  justify-content: flex-end;
  z-index: 50;
}
.dispatch-drawer {
  width: 520px;
  max-width: 92vw;
  height: 100%;
  background: #fff;
  border-left: 1px solid var(--border);
  display: flex;
  flex-direction: column;
  box-shadow: -8px 0 24px rgba(15, 23, 42, 0.12);
}
.drawer-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  padding: 16px 18px 12px;
  border-bottom: 1px solid var(--border);
}
.drawer-head h3 { margin: 0; font-size: 16px; }
.drawer-sub { margin: 4px 0 0; font-size: 12px; color: var(--muted); }
.drawer-banner {
  margin: 12px 18px 0;
  padding: 8px 12px;
  border-radius: 6px;
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 8px;
  font-size: 13px;
}
.drawer-banner.error { background: #fef3f2; border: 1px solid #fda29b; color: #b42318; }
.drawer-section { padding: 12px 18px; border-bottom: 1px solid var(--border); }
.drawer-section.grow { flex: 1; overflow-y: auto; }
.drawer-section h4 { margin: 0 0 8px; font-size: 13px; }
.section-title-row { display: flex; justify-content: space-between; align-items: center; }
.drawer-empty { font-size: 13px; color: var(--muted); padding: 8px 0; }
.selected-meta { display: flex; justify-content: space-between; font-size: 13px; margin-bottom: 6px; }
.selected-list ul { list-style: none; margin: 0 0 10px; padding: 0; display: flex; flex-direction: column; gap: 4px; }
.selected-list li { display: flex; gap: 8px; align-items: center; font-size: 13px; }
.selected-list .code { font-weight: 600; }
.form-item { display: block; margin-bottom: 8px; }
.form-item span { display: block; font-size: 12px; color: var(--muted); margin-bottom: 4px; }
.form-item em { color: #b42318; font-style: normal; }
.form-item input, .form-item textarea, .failed-actions input {
  width: 100%; border: 1px solid var(--border); border-radius: 6px; padding: 6px 8px; font-size: 13px;
  font-family: inherit;
}
.section-actions { display: flex; align-items: center; gap: 10px; }
.form-msg { font-size: 12px; }
.form-msg.ok { color: #067647; }
.queue-list { display: flex; flex-direction: column; gap: 10px; }
.queue-card { border: 1px solid var(--border); border-radius: 8px; padding: 10px 12px; background: #fbfcfe; }
.queue-card.done { background: #f6fef9; border-color: #a6f4c5; }
.queue-card-head { display: flex; justify-content: space-between; align-items: center; gap: 8px; }
.small-text { font-size: 12px; font-weight: normal; }
.coord-note { margin: 4px 0 8px; }
.item-list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 4px; }
.item-list li { display: flex; gap: 8px; align-items: baseline; font-size: 12px; }
.item-code { font-weight: 600; }
.item-status {
  flex-shrink: 0; padding: 1px 8px; border-radius: 10px; background: #e2e8f0; color: #334155;
}
.item-status.is-started { background: #fef0c7; color: #b54708; }
.item-status.is-closed { background: #d1fadf; color: #067647; }
.item-status.is-failed { background: #fee4e2; color: #b42318; }
.item-reason { color: #b42318; }
.tag { padding: 1px 8px; border-radius: 10px; font-size: 12px; }
.tag.warn { background: #fef0c7; color: #b54708; }
.tag.ok { background: #d1fadf; color: #067647; }
.failed-section { background: #fff7f7; border-bottom: none; }
.failed-list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 8px; }
.failed-head { display: flex; gap: 8px; align-items: baseline; }
.failed-reason { margin: 4px 0; font-size: 12px; color: #b42318; }
.failed-actions { display: flex; gap: 8px; align-items: center; }
.failed-actions input { flex: 1; }
.drawer-foot {
  padding: 10px 18px;
  border-top: 1px solid var(--border);
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 8px;
}
.upstream-switch { display: flex; align-items: center; gap: 6px; font-size: 12px; color: var(--muted); }
.btn.small { padding: 4px 10px; font-size: 12px; }
.muted { color: var(--muted); }
</style>
