<template>
  <PageHeader title="管理控制台" subtitle="集中查看账号、运行能力与审计记录。"><RouterLink class="btn" to="/settings"><Settings :size="15" />账号设置</RouterLink><button class="btn primary" :disabled="refreshing" @click="refresh()"><RefreshCw :size="15" />刷新概况</button></PageHeader>
  <BackendState :error="error" :refreshing="refreshing" :has-data="!!data" @retry="refresh()" /><p v-if="actionError" class="feedback error" role="alert">{{ actionError }}</p><p v-if="notice" class="feedback" role="status">{{ notice }}</p>
  <template v-if="data">
    <div class="page-context"><span class="data-badge">站点管理</span><span>账号权限与审计记录由服务器保存。</span></div>
    <div class="tabs-line section-tabs" role="group" aria-label="管理页面"><button v-for="item in tabs" :key="item" :aria-pressed="tab === item" :class="{ active: tab === item }" @click="tab = item">{{ item }}</button></div>
    <template v-if="tab === '管理概览'">
      <div class="stats-grid"><StatCard label="注册账号" :value="String(data.registered)" :delta="'人数上限 ' + data.limit" note="" :icon="UsersRound" /><StatCard label="启用账号" :value="String(data.items.filter(item => item.status === 'active').length)" delta="当前状态" note="" tone="green" :icon="UserCheck" /><StatCard label="停用账号" :value="String(data.items.filter(item => item.status !== 'active').length)" delta="记录继续保留" note="" tone="red" :icon="UserX" /><StatCard label="剩余名额" :value="String(Math.max(0, data.limit - data.registered))" delta="站点账号容量" note="" tone="purple" :icon="Database" /></div>
      <div class="two-col"><div class="panel card-pad"><div class="detail-title"><h2 class="card-title">账号容量</h2><span class="tag" :class="{ blue: !data.registration_enabled }">{{ data.registration_enabled ? '允许注册' : '已暂停注册' }}</span></div><div class="capacity-value">{{ data.registered }}<span>/ {{ data.limit }} 个账号</span></div><div class="progress"><i :style="{ width: Math.min(100, data.registered / data.limit * 100) + '%' }"></i></div><p class="subtle">停用账号仍占用名额。</p><button class="text-action section-space" @click="tab = '注册与审计'">管理容量与审计 <ArrowUpRight :size="13" /></button></div><div class="panel card-pad"><h2 class="card-title">运行能力</h2><div class="row-list"><div class="row"><span>在线身份服务</span><span class="tag">已连接</span></div><div class="row"><span>公开行情</span><span class="tag">快照服务</span></div><div class="row"><span>服务器状态</span><button class="text-action" @click="readRuntime()">读取运行状态</button></div></div><button class="text-action section-space" @click="readAudit()">读取审计</button></div></div>
    </template>
    <div v-if="tab === '管理概览' || tab === '用户管理'" class="panel table-panel section-space"><div class="panel-head"><div><h2>{{ tab === '管理概览' ? '最近活动账号' : '站点账号管理' }}</h2><p>当前管理员受到保护，账号操作保留审计。</p></div></div><FilterBar v-if="tab === '用户管理'" v-model="status" v-model:query="query" :options="['全部', 'active', 'disabled']" class="panel-filters" label="账号状态" search-label="搜索站点账号" /><DataTable table-id="native-users" label="站点真实账户" :headers="['用户名', '角色', '状态', '七日请求', '最近登录', '操作']" :rows="data.items.filter(item => (status === '全部' || item.status === status) && item.username.includes(query.trim())).map(item => [item.username, item.role, item.status, item.requests_7d, item.last_login_at ?? '—', item.id])" :numeric-columns="[3]" :min-width="880"><template #cell-5="{ value }"><button v-if="value !== session?.user.id" class="text-action" :disabled="mutating" @click="toggle(String(value))">切换启用状态</button><button class="text-action" :disabled="mutating" @click="revoke(String(value))">撤销会话</button></template></DataTable></div>
    <div v-if="tab === '注册与审计'" class="panel card-pad"><h2 class="card-title">新增账号与容量</h2><div class="row-list"><div class="row"><span>当前注册</span><span class="tag">{{ data.registration_enabled ? '开放' : '关闭' }}</span></div><div class="row"><span>账户容量</span><b>{{ data.registered }} / {{ data.limit }}</b></div></div><div class="inline-actions section-space"><button class="btn" :disabled="mutating" @click="registration()">{{ data.registration_enabled ? '关闭注册' : '开放注册' }}</button><button class="btn" @click="readAudit()">读取审计</button></div></div>
    <div v-if="tab === '运行与备份'" class="panel card-pad"><h2 class="card-title">服务运行状态</h2><p class="subtle">读取服务器的实时健康状态与 Worker 信息。</p><button class="btn section-space" @click="readRuntime()">读取运行状态</button></div>
    <div v-if="tab === '交易日志'" class="panel card-pad"><h2 class="card-title">交易日志</h2><p class="subtle">当前账号的成交与分红记录可在账本核对。</p><RouterLink class="btn section-space" to="/ledger">前往交易账本</RouterLink></div>
    <div v-if="audit" class="panel table-panel section-space"><div class="panel-head"><h2>站点审计记录</h2></div><DataTable table-id="native-admin-audit" label="站点审计日志" :headers="['时间', '操作', '操作者', '对象']" :rows="audit.items.map(item => [item.created_at, item.action, item.actor_id, item.target_id ?? '—'])" empty-title="暂无审计记录" /></div>
    <div v-if="runtime" class="panel card-pad section-space"><h2 class="card-title">服务运行状态</h2><p>业务就绪：{{ runtime.business_ready ? '是' : '否' }} · 调度器：{{ runtime.scheduler_running ? '运行中' : '未运行' }}</p><p>Worker 数量 {{ runtime.workers?.length ?? '—' }}</p></div>
  </template>
</template>
<script setup lang="ts">
import { ref, watch } from 'vue'
import { Settings, RefreshCw, UsersRound, UserCheck, UserX, Database, ArrowUpRight } from 'lucide-vue-next'
import StatCard from './StatCard.vue'
import FilterBar from './FilterBar.vue'
import PageHeader from './PageHeader.vue'
import DataTable from './DataTable.vue'
import BackendState from './BackendState.vue'
import { useAccountResource } from '../composables/useAccountResource'
type Account = { id: string; username: string; role: string; status: string; requests_7d: number; last_login_at: string | null }
type Audit = { items: { created_at: string; action: string; actor_id: string; target_id: string | null }[] }
const { data, error, refreshing, refresh, mutate, mutating, session, read } = useAccountResource('admin-users', (api, signal) => api.read<{ items: Account[]; registered: number; limit: number; registration_enabled: boolean }>('/admin/users', signal))
const tabs = ['管理概览', '用户管理', '运行与备份', '交易日志', '注册与审计'], tab = ref('管理概览'), status = ref('全部'), query = ref('')
const actionError = ref(''), notice = ref(''), audit = ref<Audit>(), runtime = ref<{ business_ready: boolean; scheduler_running: boolean; workers: unknown[] }>()
watch(() => session.value?.cache_scope, () => { audit.value = undefined; runtime.value = undefined; notice.value = ''; actionError.value = '' })
async function action(path: string, body = {}) { actionError.value = ''; notice.value = ''; try { await mutate(path, body); notice.value = '操作已保存并记录审计。' } catch (failure) { actionError.value = failure instanceof Error ? failure.message : '操作失败。' } }
function registration() { return action('/admin/registration', { enabled: !data.value?.registration_enabled }) }
function toggle(id: string) { const item = data.value?.items.find(value => value.id === id); if (item) return action(`/admin/users/${encodeURIComponent(id)}/status`, { status: item.status === 'active' ? 'disabled' : 'active' }) }
function revoke(id: string) { return action(`/admin/users/${encodeURIComponent(id)}/revoke-sessions`) }
async function readAudit() { try { audit.value = await read('/admin/audit?limit=50') } catch (failure) { actionError.value = failure instanceof Error ? failure.message : '审计暂不可用。' } }
async function readRuntime() { try { runtime.value = await read('/admin/runtime') } catch (failure) { actionError.value = failure instanceof Error ? failure.message : '运行状态暂不可用。' } }
</script>
