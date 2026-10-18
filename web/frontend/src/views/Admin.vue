<template>
  <LiveAdmin v-if="liveBackend" />
  <template v-else>
  <PageHeader title="管理控制台" subtitle="集中查看演示账号、运行能力与本地审计记录。"><RouterLink class="btn" to="/settings"><Settings :size="15" aria-hidden="true" />账号设置</RouterLink><button class="btn primary" @click="refresh"><RefreshCw :size="15" aria-hidden="true" />刷新概况</button></PageHeader>
  <div class="page-context"><span class="data-badge">本地管理演示</span><span>账号操作仅修改本浏览器的样本，不涉及真实登录权限或服务器。</span></div>
  <p v-if="notice" class="feedback" role="status">{{ notice }}</p>
  <div class="tabs-line section-tabs" role="group" aria-label="管理页面"><button v-for="tab in tabs" :key="tab" :aria-pressed="currentTab === tab" :class="{ active: currentTab === tab }" @click="currentTab = tab">{{ tab }}</button></div>
  <template v-if="currentTab === '管理概览'">
    <div class="stats-grid"><StatCard label="演示账号" :value="String(state.accounts.length)" :delta="'人数上限 ' + state.capacity" note="" :icon="UsersRound" /><StatCard label="启用账号" :value="String(enabledCount)" delta="本地状态" note="" tone="green" :icon="UserCheck" /><StatCard label="停用账号" :value="String(state.accounts.length - enabledCount)" delta="记录继续保留" note="" tone="red" :icon="UserX" /><StatCard label="剩余名额" :value="String(state.capacity - state.accounts.length)" delta="用于演示账号管理" note="" tone="purple" :icon="Database" /></div>
    <div class="two-col"><div class="panel card-pad"><div class="detail-title"><h2 class="card-title">账号容量</h2><span class="tag" :class="{ blue: !state.registrationOpen }">{{ state.registrationOpen ? '允许新增' : '已暂停新增' }}</span></div><div class="capacity-value">{{ state.accounts.length }}<span>/ {{ state.capacity }} 个账号</span></div><div class="progress"><i :style="{ width: state.accounts.length / state.capacity * 100 + '%' }"></i></div><p class="subtle">停用的演示账号仍占用名额。</p><button class="text-action section-space" @click="currentTab = '注册与审计'">管理容量与审计 <ArrowUpRight :size="13" aria-hidden="true" /></button></div><div class="panel card-pad"><h2 class="card-title">运行能力</h2><div class="row-list"><div class="row"><span>本地存储</span><span class="tag" :class="{ blue: !storageReadable }">{{ storageReadable ? '可读取' : '受浏览器限制' }}</span></div><div class="row"><span>在线身份服务</span><span class="tag blue">未连接</span></div><div class="row"><span>在线行情与交易</span><span class="tag blue">未连接</span></div></div><button class="text-action section-space" @click="currentTab = '运行与备份'">查看能力与备份 <ArrowUpRight :size="13" aria-hidden="true" /></button></div></div>
  </template>
  <template v-if="currentTab === '管理概览' || currentTab === '用户管理'">
    <div class="panel table-panel section-space"><div class="panel-head"><div><h2>{{ currentTab === '管理概览' ? '最近活动账号' : '演示账号管理' }}</h2><p>当前管理员受到保护，其他账号可切换启用状态。</p></div><button class="btn" :disabled="!state.registrationOpen || state.accounts.length >= state.capacity" @click="addOpen = true"><Plus :size="14" aria-hidden="true" />新增账号</button></div>
      <FilterBar v-if="currentTab === '用户管理'" v-model="status" v-model:query="query" :options="['全部', '启用', '停用']" class="panel-filters" label="账号状态" placeholder="搜索账号、显示名称或邮箱" search-label="搜索演示账号" />
      <DataTable table-id="accounts-v2" label="账号列表" :headers="['账号', '显示名称', '角色', '状态', '最近活动', '操作']" :rows="accountRows" :min-width="880">
        <template #cell-0="{ value }"><button class="table-name-button" @click="selectedUsername = String(value); detailOpen = true">{{ value }}</button></template>
        <template #cell-3="{ value }"><span class="tag" :class="{ blue: value === '停用' }">{{ value }}</span></template>
        <template #cell-5="{ row }"><span v-if="row[0] === 'admin'" class="subtle">当前管理员</span><button v-else class="text-action" :aria-label="'切换账号状态：' + row[0]" @click="toggleAccount(String(row[0]))">{{ row[3] === '启用' ? '停用' : '启用' }}</button></template>
      </DataTable>
    </div>
  </template>
  <template v-if="currentTab === '运行与备份'">
    <div class="panel card-pad"><div class="detail-title"><div><h2 class="card-title">本地工作台能力</h2><p class="subtle">最近查看 {{ dateTime(checkedAt) }}，不代表服务器健康检查。</p></div><span class="tag blue">前端演示</span></div><div class="service-list"><div><Database :size="19" aria-hidden="true" /><span><b>本地持久化</b><small>浏览器内保存持仓、偏好和研究记录</small></span><span class="tag">{{ storageReadable ? '可读取' : '受限' }}</span></div><div><FileText :size="19" aria-hidden="true" /><span><b>演示账本</b><small>{{ portfolio.transactions.length }} 笔记录 · {{ portfolio.holdings.length }} 项持仓</small></span><RouterLink to="/ledger" class="text-action">查看账本</RouterLink></div><div><ShieldCheck :size="19" aria-hidden="true" /><span><b>在线身份与交易服务</b><small>当前没有真实账号管理或交易执行能力</small></span><span class="tag blue">未连接</span></div></div></div>
    <div class="panel card-pad section-space"><h2 class="card-title">本地备份</h2><p class="subtle">导出本浏览器的观澜演示数据，文件包含账户样本、研究记录和偏好。</p><button class="btn section-space" @click="exportBackup"><Download :size="15" aria-hidden="true" />导出演示备份</button></div>
  </template>
  <div v-if="currentTab === '交易日志'" class="panel table-panel"><div class="panel-head"><div><h2>演示记账日志</h2><p>与交易账本同步，不代表券商成交记录。</p></div><RouterLink class="btn" to="/ledger">前往交易账本</RouterLink></div><DataTable table-id="admin-trades" label="演示记账日志" :headers="['名称', '时间', '代码', '方向', '金额', '备注']" :rows="tradeRows" :numeric-columns="[4]" /></div>
  <template v-if="currentTab === '注册与审计'">
    <form class="panel card-pad" @submit.prevent="saveCapacity"><h2 class="card-title">新增账号与容量</h2><label class="switch-row section-space"><span><b>允许新增演示账号</b><small>仅控制本页面的新增操作，不开放真实注册。</small></span><input type="checkbox" :checked="state.registrationOpen" @change="toggleRegistration" /></label><div class="inline-actions section-space"><label class="stack-label">演示账号上限<input v-model.number="capacityDraft" class="field" type="number" :min="state.accounts.length" max="100" required /></label><button class="btn primary" type="submit">保存容量</button></div></form>
    <div class="panel table-panel section-space"><div class="panel-head"><div><h2>本地审计记录</h2><p>保留最近 50 次演示管理操作。</p></div></div><DataTable table-id="admin-audit" label="本地审计记录" :headers="['操作', '对象', '操作者', '时间']" :rows="auditRows" empty-title="暂无管理操作记录" /></div>
  </template>
  <AppDialog v-model="addOpen" title="新增演示账号" description="不会创建真实登录账号或发送任何通知。"><form id="account-form" class="stack-form" @submit.prevent="addAccount"><label>账号<input v-model.trim="draft.username" class="field" pattern="[A-Za-z0-9_]{3,20}" placeholder="3–20 位字母、数字或下划线" required /></label><label>显示名称<input v-model.trim="draft.displayName" class="field" maxlength="30" required /></label><label>联系邮箱<input v-model.trim="draft.email" class="field" type="email" required /></label><p v-if="formError" class="feedback error" role="alert">{{ formError }}</p></form><template #footer><button class="btn" @click="addOpen = false">取消</button><button class="btn primary" type="submit" form="account-form">保存演示账号</button></template></AppDialog>
  <AppDialog v-model="detailOpen" title="账号资料" drawer><template v-if="selectedAccount"><div class="profile-preview"><div class="avatar">{{ selectedAccount.displayName.slice(0, 1) }}</div><div><h3>{{ selectedAccount.displayName }}</h3><p class="subtle">{{ selectedAccount.username }}</p></div></div><div class="row-list section-space"><div class="row"><span>邮箱</span><b class="break-anywhere">{{ selectedAccount.email }}</b></div><div class="row"><span>角色</span><b>{{ selectedAccount.role }}</b></div><div class="row"><span>演示状态</span><span class="tag" :class="{ blue: !selectedAccount.enabled }">{{ selectedAccount.enabled ? '启用' : '停用' }}</span></div></div></template></AppDialog>
</template>
</template>
<script setup lang="ts">
import LiveAdmin from '../components/LiveAdmin.vue'
const liveBackend = !!import.meta.env.VITE_API_BASE_URL?.trim()
import { computed, reactive, ref } from 'vue'
import { ArrowUpRight, Database, Download, FileText, Plus, RefreshCw, Settings, ShieldCheck, UserCheck, UserX, UsersRound } from 'lucide-vue-next'
import PageHeader from '../components/PageHeader.vue'
import StatCard from '../components/StatCard.vue'
import DataTable from '../components/DataTable.vue'
import FilterBar from '../components/FilterBar.vue'
import AppDialog from '../components/AppDialog.vue'
import { useLocalState } from '../composables/useLocalState'
import { usePortfolio } from '../composables/usePortfolio'
import { usePreferences } from '../composables/usePreferences'
import { dateTime, money, downloadFile } from '../utils/format'
type Account = { username: string; displayName: string; email: string; role: string; enabled: boolean; lastActive: string }
type Audit = { action: string; target: string; actor: string; date: string }
type AdminState = { accounts: Account[]; capacity: number; registrationOpen: boolean; audit: Audit[] }
const initial: AdminState = { capacity: 30, registrationOpen: true, accounts: [{ username: 'admin', displayName: '观澜研究员', email: 'admin@example.com', role: '管理员', enabled: true, lastActive: '2026-10-16T12:45:00+08:00' }, { username: 'xiaozhu01', displayName: '小朱', email: 'xiaozhu@example.com', role: '普通用户', enabled: true, lastActive: '2026-10-11T17:43:00+08:00' }], audit: [] }
const state = useLocalState<AdminState>('guanlan.admin.v1', initial, (value): value is AdminState => !!value && typeof value === 'object' && Array.isArray((value as AdminState).accounts) && (value as AdminState).accounts.every(account => typeof account.username === 'string' && typeof account.displayName === 'string' && typeof account.email === 'string' && typeof account.role === 'string' && typeof account.lastActive === 'string' && typeof account.enabled === 'boolean') && (value as AdminState).accounts.some(account => account.username === 'admin' && account.enabled) && Number.isInteger((value as AdminState).capacity) && (value as AdminState).capacity >= (value as AdminState).accounts.length && (value as AdminState).capacity <= 100 && typeof (value as AdminState).registrationOpen === 'boolean' && Array.isArray((value as AdminState).audit) && (value as AdminState).audit.every(log => [log.action, log.target, log.actor, log.date].every(item => typeof item === 'string')))
const { portfolio } = usePortfolio(), { preferences } = usePreferences()
const tabs = ['管理概览', '用户管理', '运行与备份', '交易日志', '注册与审计']
const currentTab = ref(tabs[0]), status = ref('全部'), query = ref(''), notice = ref(''), checkedAt = ref(Date.now()), capacityDraft = ref(state.value.capacity), addOpen = ref(false), formError = ref(''), selectedUsername = ref('admin'), detailOpen = ref(false)
let storageReadable = true
try { localStorage.getItem('guanlan.admin.v1') } catch { storageReadable = false }
const draft = reactive({ username: '', displayName: '', email: '' })
const enabledCount = computed(() => state.value.accounts.filter(account => account.enabled).length)
const accounts = computed(() => state.value.accounts.filter(account => currentTab.value !== '用户管理' || ((status.value === '全部' || (status.value === '启用') === account.enabled) && (!query.value.trim() || [account.username, account.displayName, account.email].some(value => value.toLowerCase().includes(query.value.trim().toLowerCase()))))).slice(0, currentTab.value === '管理概览' ? 5 : undefined))
const accountRows = computed(() => accounts.value.map(account => [account.username, account.displayName, account.role, account.enabled ? '启用' : '停用', account.lastActive ? dateTime(account.lastActive) : '尚无活动', account.username]))
const selectedAccount = computed(() => state.value.accounts.find(account => account.username === selectedUsername.value))
const tradeRows = computed(() => portfolio.value.transactions.map(trade => [trade.name, dateTime(trade.date), trade.code, trade.direction, money(trade.amount), trade.note]))
const auditRows = computed(() => state.value.audit.map(log => [log.action, log.target, log.actor, dateTime(log.date)]))
function audit(action: string, target: string) { state.value.audit.unshift({ action, target, actor: preferences.value.username, date: new Date().toISOString() }); state.value.audit = state.value.audit.slice(0, 50) }
function toggleAccount(username: string) { if (username === 'admin') return; const account = state.value.accounts.find(item => item.username === username); if (account) { account.enabled = !account.enabled; audit(account.enabled ? '启用演示账号' : '停用演示账号', username); notice.value = '演示账号状态已更新。' } }
function addAccount() {
  formError.value = ''
  if (!state.value.registrationOpen || state.value.accounts.length >= state.value.capacity) { formError.value = '当前不允许新增，或账号容量已满。'; return }
  if (!/^[A-Za-z0-9_]{3,20}$/.test(draft.username) || !draft.displayName || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(draft.email)) { formError.value = '请填写有效的账号、名称与邮箱。'; return }
  if (state.value.accounts.some(account => account.username.toLowerCase() === draft.username.toLowerCase())) { formError.value = '账号已存在，请使用其他名称。'; return }
  state.value.accounts.push({ ...draft, role: '普通用户', enabled: true, lastActive: '' }); audit('新增演示账号', draft.username); addOpen.value = false; Object.assign(draft, { username: '', displayName: '', email: '' }); notice.value = '演示账号已保存。'
}
function toggleRegistration() { state.value.registrationOpen = !state.value.registrationOpen; audit('修改新增开关', state.value.registrationOpen ? '允许新增' : '暂停新增') }
function saveCapacity() { if (!Number.isInteger(capacityDraft.value) || capacityDraft.value < state.value.accounts.length || capacityDraft.value > 100) { notice.value = '容量应为不小于当前账号数且不大于 100 的整数。'; return } state.value.capacity = capacityDraft.value; audit('修改账号容量', String(capacityDraft.value)); notice.value = '账号容量已保存。' }
function refresh() { checkedAt.value = Date.now(); notice.value = '已更新本地概况，未请求服务器健康检查。' }
function exportBackup() {
  try { const data: Record<string, unknown> = {}; for (let index = 0; index < localStorage.length; index++) { const key = localStorage.key(index); if (key?.startsWith('guanlan.')) { const raw = localStorage.getItem(key); try { data[key] = JSON.parse(raw ?? 'null') } catch { data[key] = raw } } } downloadFile('观澜-本地演示备份.json', JSON.stringify({ version: 1, exportedAt: new Date().toISOString(), data }, null, 2), 'application/json'); audit('导出本地备份', '观澜演示数据'); notice.value = '本地演示备份已导出。' }
  catch { notice.value = '浏览器限制了本地数据读取，暂时无法导出备份。' }
}
</script>
