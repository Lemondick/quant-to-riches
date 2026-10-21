<template>
  <PageHeader title="交易日历管理" subtitle="查看各市场交易日历配置、自动生成来源与核对状态。" />
  <BackendState :error="error" :refreshing="refreshing && !loaded" :has-data="loaded" @retry="load()" />
  <template v-if="loaded">
    <div class="page-context"><span class="data-badge">管理者</span><span>自动生成的日历需人工核对后确认；确认接口由后端提供。</span></div>
    <p v-if="confirmUnsupported" class="feedback" role="status">后端确认接口尚未就绪（POST /calendar/{market}/{year}/confirm 并行开发中），确认按钮已禁用；日历查看与来源展示不受影响。</p>
    <p v-if="actionNotice" class="feedback" role="status">{{ actionNotice }}</p>
    <p v-if="actionError" class="feedback error" role="alert">{{ actionError }}</p>
    <div v-for="market in markets" :key="market" class="panel table-panel section-space">
      <div class="panel-head"><div><h2>{{ marketNames[market] }}（{{ market }}）</h2><p v-if="status[market]?.warnings?.length">{{ status[market].warnings.join(' ') }}</p><p v-else>当年与次年日历配置</p></div></div>
      <div v-for="view in views[market] ?? []" :key="view.year" class="card-pad calendar-year">
        <div class="detail-title"><h3 class="card-title">{{ view.year }} 年</h3>
          <span class="tag" :class="{ green: view.configured, red: !view.configured }">{{ view.configured ? '已配置' : '缺失' }}</span>
          <span v-if="pendingConfirm(view)" class="tag red">待核对</span>
          <span v-else-if="view.configured" class="tag blue">已确认</span>
        </div>
        <p v-if="pendingConfirm(view)" class="feedback" role="alert">该日历为自动生成（{{ view.source }}），尚未人工核对。请对照交易所官方公告检查休市日期后点击确认。</p>
        <div class="row-list">
          <div class="row"><span>来源 source</span><b>{{ view.source ?? '—' }}</b></div>
          <div class="row"><span>置信度 confidence</span><b>{{ view.confidence ?? '—' }}</b></div>
          <div class="row"><span>休市日数量</span><b>{{ view.holidays?.length ?? 0 }}</b></div>
          <div class="row"><span>版本 revision</span><b class="mono">{{ (view.revision ?? '—').toString().slice(0, 12) }}</b></div>
          <div class="row"><span>出处 origin</span><b>{{ view.origin ?? '—' }}</b></div>
        </div>
        <p v-if="view.notes?.length" class="subtle">备注 notes：{{ view.notes.join('；') }}</p>
        <details v-if="view.holidays?.length" class="section-space"><summary class="text-action">查看休市日期（{{ view.holidays.length }} 天）</summary><p class="subtle">{{ view.holidays.join('、') }}</p></details>
        <div class="inline-actions section-space">
          <button class="btn primary" :disabled="!pendingConfirm(view) || confirming || confirmUnsupported" @click="confirm(market, view.year)">{{ confirming ? '确认中' : '一键确认' }}</button>
        </div>
      </div>
    </div>
  </template>
</template>
<script setup lang="ts">
import { ref, watch } from 'vue'
import PageHeader from './PageHeader.vue'
import BackendState from './BackendState.vue'
import { ApiError } from '../services/dataApi'
import { useAccountResource } from '../composables/useAccountResource'

type CalendarView = {
  market: string; year: number; configured: boolean; source?: string | null
  holidays?: string[]; revision?: string | null; origin?: string | null
  confidence?: string | null; notes?: string[] | null
  confirmed?: boolean | null; pending_confirmation?: boolean | null; auto_generated?: boolean | null
}
type CalendarStatus = { warnings?: string[] }

const markets = ['CN', 'US', 'JP', 'HK'] as const
const marketNames: Record<string, string> = { CN: '中国', US: '美国', JP: '日本', HK: '中国香港' }
/** 契约 §1 的 worker 自动生成来源；命中且无人工确认标记时视为待核对。 */
const AUTO_SOURCES = ['lunar_algorithm', 'nyse_rules', 'japan_law', 'hkex_rules']
const shanghaiNow = new Date(new Date().toLocaleString('en-US', { timeZone: 'Asia/Shanghai' }))
const years = [shanghaiNow.getFullYear(), shanghaiNow.getFullYear() + 1]

// 会话由 useAccountResource 建立；日历读取走其 scoped read（自动带会话 scope）。
const { error, refreshing, session, read, mutate } = useAccountResource('admin-calendar',
  (api, signal) => api.read<unknown>('/auth/session', signal))

const views = ref<Record<string, CalendarView[]>>({})
const status = ref<Record<string, CalendarStatus>>({})
const loaded = ref(false)
const confirming = ref(false), confirmUnsupported = ref(false)
const actionNotice = ref(''), actionError = ref('')

function pendingConfirm(view: CalendarView) {
  if (!view.configured) return false
  if (view.confirmed === false || view.pending_confirmation === true || view.auto_generated === true) return true
  if (view.confirmed === true) return false
  // 后端确认字段尚未提供时：来源为自动生成算法则视为待核对（契约 §1）
  return !!view.source && AUTO_SOURCES.includes(view.source)
}

async function load() {
  loaded.value = false
  actionError.value = ''
  try {
    const next: Record<string, CalendarView[]> = {}
    const nextStatus: Record<string, CalendarStatus> = {}
    for (const market of markets) {
      const [currentYear, nextYear, marketStatus] = await Promise.all([
        read<CalendarView>(`/admin/calendar?year=${years[0]}&market=${market}`),
        read<CalendarView>(`/admin/calendar?year=${years[1]}&market=${market}`),
        read<CalendarStatus>(`/admin/calendar/status?market=${market}`).catch(() => ({}) as CalendarStatus),
      ])
      next[market] = [currentYear, nextYear]
      nextStatus[market] = marketStatus
    }
    views.value = next
    status.value = nextStatus
    loaded.value = true
  } catch (failure) {
    actionError.value = failure instanceof Error ? failure.message : '日历读取失败。'
  }
}

async function confirm(market: string, year: number) {
  confirming.value = true
  actionNotice.value = ''
  actionError.value = ''
  try {
    // A 并行开发的确认接口；按任务描述优先 /calendar/{market}/{year}/confirm，
    // 404 时回退到现有控制器前缀 /admin/calendar/{market}/{year}/confirm。
    const candidates = [`/calendar/${market}/${year}/confirm`, `/admin/calendar/${market}/${year}/confirm`]
    let missing = false
    for (const path of candidates) {
      try {
        await mutate(path, {}, 'POST')
        missing = false
        break
      } catch (failure) {
        if (failure instanceof ApiError && failure.status === 404) { missing = true; continue }
        throw failure
      }
    }
    if (missing) {
      confirmUnsupported.value = true
      actionError.value = '后端确认接口尚未就绪（A 并行开发中），已禁用确认按钮。'
      return
    }
    actionNotice.value = `${market} ${year} 年日历已确认。`
    await load()
  } catch (failure) {
    actionError.value = failure instanceof Error ? failure.message : '确认失败。'
  } finally {
    confirming.value = false
  }
}

watch(session, value => { if (value) void load() }, { immediate: true })
</script>
<style scoped>
.calendar-year + .calendar-year{border-top:1px solid var(--line)}
.calendar-year h3{font-size:14px}
.mono{font-family:ui-monospace,monospace;font-size:12px}
</style>
