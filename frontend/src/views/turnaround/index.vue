<template>
  <section class="page" data-module="turnaround">
    <header class="page-head">
      <div>
        <h2>过站保障管理</h2>
        <p class="page-desc">同一航班的保障项勾好后收进侧边抽屉：先放待办队列、补班组与协调记录，再按段统一下发与收尾。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" :disabled="!selectedIds.size" @click="drawerOpen = true">
          批量下发抽屉{{ selectedIds.size ? `（已选 ${selectedIds.size}）` : '' }}
        </button>
        <button class="btn" type="button" @click="exportRows">导出过站保障清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card" :title="item.label === '队列待收尾' ? pendingCountError : ''">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
        <span v-if="item.label === '队列待收尾' && pendingCountError" class="stat-note">取数失败，稍后重试</span>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label v-for="field in filterFields" :key="field" class="filter-item">
        <span>{{ field }}</span>
        <input v-model="filters[field]" :placeholder="`按${field}检索`" />
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <p v-if="selectionHint" class="selection-hint">{{ selectionHint }}</p>

    <table class="data-table">
      <thead>
        <tr>
          <th class="check-col">勾选</th>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)">
          <td class="check-col">
            <input
              type="checkbox"
              :checked="selectedIds.has(Number(row.id))"
              :disabled="isClosed(row)"
              @change="toggleSelect(row)"
            />
          </td>
          <td v-for="column in columns" :key="column">{{ row[column] || '—' }}</td>
          <td class="row-actions">
            <button
              v-for="action in actions"
              :key="action"
              class="link"
              type="button"
              @click="runAction(action, row)"
            >
              {{ action }}
            </button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 2" class="empty-state">暂无过站保障数据，可先登记过站任务</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条过站保障记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>

    <DispatchDrawer
      :open="drawerOpen"
      :selection="selectedRows"
      :selection-flight="selectionFlight"
      @close="drawerOpen = false"
      @clear-selection="clearSelection"
      @count-changed="onPendingCountChanged"
    />
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { request } from '@/api/client'
import { fetchPendingClose } from '@/api/dispatch'
import DispatchDrawer from './DispatchDrawer.vue'

type Row = Record<string, string | number | null>

const ENDPOINT = '/api/turnaround'
const columns = ["过站编号", "对应航班", "计划过站时间", "实际过站时间", "过站延误", "延误原因", "协调记录", "过站状态"]
const actions = ["启动保障", "完成保障", "记录延误"]

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = columns.slice(0, 3)

// 抽屉与勾选
const drawerOpen = ref(false)
const selectedIds = ref<Set<number>>(new Set())
const pendingClose = ref<number | null>(null)
const pendingCountError = ref('')

const stats = computed(() => [
  { label: "待保障航班", value: rows.value.filter((row) => row.status === '待保障').length },
  { label: "保障中航班", value: rows.value.filter((row) => row.status === '保障中').length },
  { label: "已保障航班", value: rows.value.filter((row) => row.status === '已保障').length },
  // 待收尾数与抽屉里的队列同源：直接读下发队列，不从本页表格推
  { label: "队列待收尾", value: pendingClose.value === null ? '—' : pendingClose.value },
])

const selectedRows = computed(() =>
  rows.value.filter((row) => selectedIds.value.has(Number(row.id))),
)

const selectionFlight = computed(() => {
  const flights = new Set(selectedRows.value.map((row) => String(row['对应航班'] || '')))
  return flights.size === 1 ? [...flights][0] : ''
})

const selectionHint = computed(() => {
  if (!selectedRows.value.length) return ''
  if (selectionFlight.value) {
    return `已勾选航班 ${selectionFlight.value} 的 ${selectedRows.value.length} 个保障项，可打开批量下发抽屉。`
  }
  return '已勾选的保障项分属不同航班，一个抽屉只办理同一航班，请取消多余航班的勾选。'
})

function isClosed(row: Row) {
  return row.status === '已保障'
}

function toggleSelect(row: Row) {
  const id = Number(row.id)
  const next = new Set(selectedIds.value)
  if (next.has(id)) {
    next.delete(id)
  } else {
    // 只允许勾同一航班：与抽屉入队校验保持一致，提前在列表上拦一道
    const flight = String(row['对应航班'] || '')
    if (next.size && selectionFlight.value && flight !== selectionFlight.value) {
      errorMessage.value = `只能勾选同一航班的保障项，已选的是 ${selectionFlight.value}`
      return
    }
    next.add(id)
  }
  selectedIds.value = next
  errorMessage.value = ''
}

function clearSelection() {
  selectedIds.value = new Set()
}

function onPendingCountChanged(count: number) {
  pendingClose.value = count
}

async function loadPendingClose() {
  try {
    const pending = await fetchPendingClose()
    pendingClose.value = pending.count
    pendingCountError.value = ''
  } catch (error) {
    // 取不到数据时另一个入口也要写明原因，不拿本地表格数顶上
    pendingClose.value = null
    pendingCountError.value = error instanceof Error ? error.message : '待收尾数暂时取不到'
  }
}

function resetFilters() {
  filters.value = {}
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ values: { action } }),
    })
    if (!response.ok) {
      throw new Error('过站保障动作未生效，请稍后重试')
    }
    const result = (await response.json()) as { ok?: boolean; message?: string }
    if (!result.ok) {
      throw new Error(result.message || '过站保障动作未生效')
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '过站保障操作失败'
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value as Record<string, string>).toString()
  try {
    const response = await request(`${ENDPOINT}?${query}`)
    if (!response.ok) {
      throw new Error('过站任务列表读取失败')
    }
    const payload = (await response.json()) as { items?: Row[]; total?: number }
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
    // 列表刷新后把已经收尾的勾选清掉，避免抽屉里带进不可下发的条目
    const valid = new Set(rows.value.filter((row) => !isClosed(row)).map((row) => Number(row.id)))
    selectedIds.value = new Set([...selectedIds.value].filter((id) => valid.has(id)))
    await loadPendingClose()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '过站保障列表读取失败'
  }
}

onMounted(reload)
</script>

<style scoped>
.check-col { width: 40px; text-align: center; }
.selection-hint {
  margin: 0 0 8px;
  padding: 6px 10px;
  font-size: 12px;
  color: #b54708;
  background: #fffaeb;
  border: 1px solid #fedf89;
  border-radius: 6px;
}
.stat-note { display: block; font-size: 11px; color: #b42318; }
.page-actions { display: flex; gap: 8px; }
</style>
