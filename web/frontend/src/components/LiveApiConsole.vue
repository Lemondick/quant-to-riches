<template>
  <PageHeader title="API 控制台" subtitle="后端全部路由的统一调试入口；表单由路由快照自动生成，走现有 API client 调用。">
    <span class="tag" :class="{ blue: snapshot.source === 'openapi.json' }">快照来源：{{ snapshot.source === 'openapi.json' ? 'openapi.json' : '控制器扫描（openapi.json 尚未生成）' }}</span>
    <span class="tag">{{ snapshot.route_count }} 条路由 · {{ snapshot.unexposed_count }} 条无前端入口</span>
  </PageHeader>
  <BackendState :error="error" :refreshing="refreshing && !session" :has-data="!!session" @retry="refresh()" />
  <template v-if="session">
    <div class="page-context"><span class="data-badge">{{ session.user.username }}</span><span>写操作会携带 CSRF Token，结果以 JSON 显示，不做二次加工。</span></div>
    <div class="api-console">
      <section class="panel api-routes" aria-label="路由列表">
        <div class="panel-head"><div><h2>路由</h2><p>{{ filtered.length }} / {{ snapshot.route_count }}</p></div></div>
        <div class="api-filters">
          <input v-model.trim="query" class="field" type="search" placeholder="搜索路径 / 控制器 / 动作" aria-label="搜索路由" />
          <select v-model="controllerFilter" class="field" aria-label="按控制器筛选">
            <option value="">全部控制器</option>
            <option v-for="name in controllers" :key="name" :value="name">{{ name }}</option>
          </select>
          <label class="api-check"><input v-model="onlyUnexposed" type="checkbox" />仅看无前端入口</label>
        </div>
        <ul class="api-route-list">
          <li v-for="route in filtered" :key="route.method + route.path">
            <button :class="{ active: selected === route }" @click="select(route)">
              <span class="method" :class="route.method.toLowerCase()">{{ route.method }}</span>
              <span class="path">{{ route.path }}</span>
              <span v-if="!route.hasFrontendEntry" class="tag red">无入口</span>
            </button>
          </li>
        </ul>
        <p v-if="!filtered.length" class="subtle api-empty">没有匹配的路由。</p>
      </section>
      <section class="panel api-detail" aria-label="路由调试">
        <div v-if="!selected" class="api-empty subtle">从左侧选择一条路由，自动生成调用表单。</div>
        <template v-else>
          <div class="panel-head"><div><h2><span class="method" :class="selected.method.toLowerCase()">{{ selected.method }}</span> {{ selected.path }}</h2><p>{{ selected.controller }} · {{ selected.action }}{{ selected.summary ? ' · ' + selected.summary : '' }} · {{ selected.hasFrontendEntry ? '前端已有调用' : '前端无入口' }}</p></div></div>
          <form class="stack-form" @submit.prevent="execute">
            <template v-for="param in selected.params" :key="param.kind + param.name">
              <div v-if="param.kind !== 'body'" class="form-grid">
                <label>{{ paramLabel(param) }}<input v-if="inputKind(param.type) !== 'checkbox'" v-model="textModel(param.name).value" class="field" :type="inputKind(param.type)" :required="param.required" :placeholder="param.type" /><input v-else v-model="boolModel(param.name).value" type="checkbox" /></label>
              </div>
              <fieldset v-else class="api-body">
                <legend>请求体{{ param.bodyFields ? `（${param.type}）` : '（JSON）' }}</legend>
                <div v-if="param.bodyFields" class="form-grid">
                  <label v-for="field in param.bodyFields" :key="field.name">{{ field.name }}<span class="subtle"> · {{ field.type }}{{ field.required ? ' · 必填' : '' }}</span>
                    <input v-if="inputKind(field.type) !== 'checkbox' && inputKind(field.type) !== 'textarea-lines'" v-model="textModel('body.' + field.name).value" class="field" :type="inputKind(field.type)" :required="field.required" :placeholder="field.type" />
                    <input v-else-if="inputKind(field.type) === 'checkbox'" v-model="boolModel('body.' + field.name).value" type="checkbox" />
                    <textarea v-else v-model="textModel('body.' + field.name).value" class="field" rows="3" placeholder="每行一个值" />
                  </label>
                </div>
                <textarea v-else v-model="textModel('body').value" class="field" rows="6" placeholder='{"key": "value"}' aria-label="请求体 JSON" />
              </fieldset>
            </template>
            <p v-if="!selected.params.length" class="subtle">该路由无参数，可直接调用。</p>
            <p v-if="formError" class="feedback error" role="alert">{{ formError }}</p>
            <div class="inline-actions"><button class="btn primary" type="submit" :disabled="executing">{{ executing ? '调用中' : '执行调用' }}</button><button class="btn" type="button" @click="resetForm">清空表单</button></div>
          </form>
          <div v-if="response" class="api-response">
            <h3>响应 <span class="tag" :class="{ green: response.ok, red: !response.ok }">{{ response.ok ? '成功' : '失败' }}</span></h3>
            <pre>{{ response.text }}</pre>
          </div>
        </template>
      </section>
    </div>
  </template>
</template>
<script setup lang="ts">
import { computed, reactive, ref } from 'vue'
import PageHeader from './PageHeader.vue'
import BackendState from './BackendState.vue'
import { useAccountResource } from '../composables/useAccountResource'
import { ApiError, createDataApi } from '../services/dataApi'
import snapshot from '../api/route-snapshot.json'

type SnapshotParam = { name: string; kind: 'path' | 'query' | 'body'; type: string; required?: boolean; bodyFields?: { name: string; type: string; required?: boolean }[] }
type SnapshotRoute = { method: string; path: string; controller: string; action: string; summary: string; params: SnapshotParam[]; hasFrontendEntry: boolean }
const routes = (snapshot as { routes: SnapshotRoute[] }).routes

const { session, error, refreshing, refresh } = useAccountResource('api-console', (api, signal) => api.read<{ user: { id: string } }>('/auth/session', signal))
const api = createDataApi(import.meta.env.VITE_API_BASE_URL ?? '')

const query = ref(''), controllerFilter = ref(''), onlyUnexposed = ref(false)
const controllers = computed(() => [...new Set(routes.map(route => route.controller))].sort())
const filtered = computed(() => routes.filter(route =>
  (!onlyUnexposed.value || !route.hasFrontendEntry) &&
  (!controllerFilter.value || route.controller === controllerFilter.value) &&
  (!query.value || (route.path + ' ' + route.controller + ' ' + route.action).toLowerCase().includes(query.value.toLowerCase()))))
const selected = ref<SnapshotRoute | null>(null)
const values = reactive<Record<string, string | boolean>>({})
const executing = ref(false), formError = ref('')
const response = ref<{ ok: boolean; text: string } | null>(null)

function select(route: SnapshotRoute) { selected.value = route; resetForm() }
function textModel(key: string) {
  return computed({ get: () => { const v = values[key]; return typeof v === 'boolean' ? String(v) : (v ?? '') }, set: (next: string) => { values[key] = next } })
}
function boolModel(key: string) {
  return computed({ get: () => values[key] === true, set: (next: boolean) => { values[key] = next } })
}
function resetForm() { for (const key of Object.keys(values)) delete values[key]; formError.value = ''; response.value = null }

function paramLabel(param: SnapshotParam) {
  const kind = param.kind === 'path' ? '路径参数' : '查询参数'
  return `${kind} · ${param.name}（${param.type}${param.required ? ' · 必填' : ''}）`
}
function inputKind(type: string) {
  const t = type.toLowerCase()
  if (t === 'bool' || t === 'boolean') return 'checkbox'
  if (/^(int|long|decimal|double|float|number)/.test(t)) return 'number'
  if (t.includes('datetime')) return 'datetime-local'
  if (t === 'dateonly' || t === 'date') return 'date'
  if (t.endsWith('[]')) return 'textarea-lines'
  if (t.includes('json')) return 'textarea-json'
  return 'text'
}
function coerce(type: string, value: string | boolean): unknown {
  const t = type.toLowerCase()
  if (t === 'bool' || t === 'boolean') return value === true || value === 'true'
  if (/^(int|long)/.test(t)) { const n = parseInt(String(value), 10); if (!Number.isFinite(n)) throw new Error(`参数须为整数：${value}`); return n }
  if (/^(decimal|double|float|number)/.test(t)) { const n = Number(value); if (!Number.isFinite(n)) throw new Error(`参数须为数字：${value}`); return n }
  if (t.endsWith('[]')) return String(value).split('\n').map(line => line.trim()).filter(Boolean)
  if (t.includes('json')) return JSON.parse(String(value))
  return String(value)
}
function buildRequest(route: SnapshotRoute) {
  let path = route.path
  const queryParts: string[] = []
  let body: Record<string, unknown> | undefined
  for (const param of route.params) {
    if (param.kind === 'path') {
      const raw = values[param.name]
      const text = typeof raw === 'boolean' ? String(raw) : (raw ?? '').trim()
      if (!text && param.required) throw new Error(`路径参数 ${param.name} 为必填。`)
      path = path.split(`{${param.name}}`).join(encodeURIComponent(text))
    } else if (param.kind === 'query') {
      const raw = values[param.name]
      if (raw === undefined || raw === '' || raw === null) continue
      queryParts.push(`${encodeURIComponent(param.name)}=${encodeURIComponent(String(raw))}`)
    } else {
      if (param.bodyFields) {
        const obj: Record<string, unknown> = {}
        for (const field of param.bodyFields) {
          const raw = values['body.' + field.name]
          if (raw === undefined || raw === '' || raw === null) {
            if (field.required) throw new Error(`请求体字段 ${field.name} 为必填。`)
            continue
          }
          obj[field.name] = coerce(field.type, raw)
        }
        body = obj
      } else {
        const raw = String(values['body'] ?? '').trim()
        body = raw ? JSON.parse(raw) as Record<string, unknown> : {}
      }
    }
  }
  return { path: queryParts.length ? `${path}?${queryParts.join('&')}` : path, body }
}
async function execute() {
  const route = selected.value, current = session.value
  if (!route || !current) return
  formError.value = ''
  executing.value = true
  try {
    const { path, body } = buildRequest(route)
    const result = route.method === 'GET'
      ? await api.read<unknown>(path, undefined, current.cache_scope)
      : await api.write<unknown>(path, body ?? {}, current.csrf_token, current.cache_scope, route.method)
    response.value = { ok: true, text: JSON.stringify(result, null, 2) }
  } catch (failure) {
    const detail = failure instanceof ApiError
      ? { http_status: failure.status, error_code: failure.code ?? null, message: failure.message }
      : { message: failure instanceof Error ? failure.message : String(failure) }
    response.value = { ok: false, text: JSON.stringify(detail, null, 2) }
  } finally {
    executing.value = false
  }
}
</script>
<style scoped>
.api-console{display:grid;grid-template-columns:minmax(280px,380px) minmax(0,1fr);gap:14px;align-items:start}
.api-filters{display:grid;gap:8px;padding:0 16px 12px}
.api-check{display:flex;align-items:center;gap:8px;font-size:13px;color:var(--text-2)}
.api-route-list{list-style:none;margin:0;padding:0 8px 12px;max-height:60vh;overflow:auto;display:grid;gap:4px}
.api-route-list button{width:100%;display:flex;align-items:center;gap:8px;text-align:left;background:transparent;border:1px solid transparent;border-radius:8px;padding:8px 10px;color:var(--text-1)}
.api-route-list button:hover{background:rgba(122,162,255,.08)}
.api-route-list button.active{border-color:rgba(122,162,255,.4);background:rgba(122,162,255,.1)}
.api-route-list .path{font-family:ui-monospace,monospace;font-size:12px;word-break:break-all;flex:1}
.method{font-size:11px;font-weight:700;padding:2px 8px;border-radius:6px;background:#2a3b57;color:#bcd2f5;flex:none}
.method.get{background:rgba(46,160,90,.2);color:#7bd88f}
.method.post{background:rgba(77,141,255,.2);color:#8ab9ff}
.method.put{background:rgba(220,150,40,.2);color:#f0b95a}
.method.delete{background:rgba(220,70,70,.2);color:#f08a8a}
.method.patch{background:rgba(150,110,220,.2);color:#c4a8f0}
.api-detail .panel-head h2{font-size:15px;display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.api-body{border:1px solid var(--line);border-radius:10px;padding:12px;margin:0}
.api-body legend{font-size:13px;color:var(--text-2);padding:0 6px}
.api-response{margin-top:12px}
.api-response pre{background:#0a1322;border:1px solid var(--line);border-radius:10px;padding:12px;overflow:auto;max-height:50vh;font-size:12px}
.api-empty{padding:16px}
@media(max-width:1200px){.api-console{grid-template-columns:1fr}.api-route-list{max-height:40vh}}
</style>
