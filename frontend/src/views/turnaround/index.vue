<template>
  <section class="page" data-module="turnaround">
    <header class="page-head">
      <div>
        <h2>过站保障管理</h2>
        <p class="page-desc">维护过站任务，围绕过站编号、对应航班、计划过站时间、实际过站时间做登记、筛选与状态流转。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="drawerOpen = true">批量保障下发</button>
        <button class="btn" type="button" @click="openCreate">登记过站任务</button>
        <button class="btn" type="button" @click="exportRows">导出过站保障清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">待收尾（队列）</span>
        <strong class="stat-value">{{ pendingCloseout }}</strong>
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

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)">
          <td v-for="column in columns" :key="column">{{ row[column] ?? '—' }}</td>
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
          <td :colspan="columns.length + 1" class="empty-state">暂无过站保障数据，可先登记过站任务</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条过站保障记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>

    <BatchDrawer
      v-if="drawerOpen"
      :initial-flight="filters['对应航班'] ?? ''"
      @close="drawerOpen = false"
      @changed="onBatchChanged"
    />
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { fetchJson, request } from '@/api/client'

import BatchDrawer from './BatchDrawer.vue'

type Row = Record<string, string | number | null>

const ENDPOINT = '/api/turnaround'
const columns = ["过站编号", "对应航班", "计划过站时间", "实际过站时间", "过站延误", "延误原因", "协调记录", "过站状态"]
const actions = ["启动保障", "完成保障", "记录延误"]
const statuses = ["待保障", "保障中", "已保障", "延误中"]
const stats = [{"label": "待保障航班", "value": 0}, {"label": "保障中航班", "value": 0}, {"label": "已保障航班", "value": 0}]
// 筛选框字段名与列表接口参数的对应关系
const FILTER_PARAM_MAP: Record<string, string> = { "过站编号": "keyword", "对应航班": "flight" }

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = columns.slice(0, 3)
const drawerOpen = ref(false)
// 待收尾数与批量下发抽屉读的是同一份队列汇总
const pendingCloseout = ref(0)

function resetFilters() {
  filters.value = {}
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function openCreate() {
  errorMessage.value = '过站任务登记入口尚未接入审批流'
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  const values: Record<string, string> = { action }
  if (action === '记录延误') {
    const reason = window.prompt('请填写延误原因（缺失的条目不允许收尾）')
    if (reason === null) {
      return
    }
    if (!reason.trim()) {
      errorMessage.value = '记录延误时请填写延误原因'
      return
    }
    values['延误原因'] = reason.trim()
  }
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ values }),
    })
    const payload = await response.json()
    if (!response.ok || !payload.ok) {
      throw new Error(payload.message ?? payload.detail ?? '过站保障动作未生效，请稍后重试')
    }
    await Promise.all([reload(), loadSummary()])
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '过站保障操作失败'
  }
}

async function loadSummary() {
  try {
    const payload = await fetchJson<{ pending_closeout: number }>(`${ENDPOINT}/batch/summary`)
    pendingCloseout.value = payload.pending_closeout
  } catch {
    // 汇总读取失败不打扰主列表，下次刷新再补
  }
}

function onBatchChanged() {
  void reload()
  void loadSummary()
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams()
  for (const [field, value] of Object.entries(filters.value)) {
    const param = FILTER_PARAM_MAP[field]
    if (param && value) {
      query.set(param, value)
    }
  }
  try {
    const response = await request(`${ENDPOINT}?${query.toString()}`)
    if (!response.ok) {
      throw new Error('过站任务列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '过站保障列表读取失败'
  }
}

onMounted(() => {
  void reload()
  void loadSummary()
})
</script>
