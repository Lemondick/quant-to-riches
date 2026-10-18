<template>
  <section class="panel model-settings" aria-label="账户模型连接">
    <header class="model-heading"><div><h2>AI 模型配置</h2><p>填写连接 → 读取模型 → 为研究角色选择模型并保存</p></div><span class="tag blue">账户模型连接</span></header><BackendState :error="error" :refreshing="refreshing" :has-data="!!data" @retry="refresh()" />
    <p v-if="message" class="feedback" role="status">{{ message }}</p><p v-if="failure" class="feedback error" role="alert">{{ failure }}</p>
    <template v-if="data"><p class="model-service-note">当前配置 {{ data.configured ? '已就绪' : '尚未就绪' }}{{ data.active ? ' · 存在运行中的研究' : '' }}</p>
      <div class="model-connections"><form class="model-connection stack-form" @submit.prevent="discover"><div class="model-section-heading"><h3>兼容 API 连接</h3></div><label>兼容 API 地址<input v-model.trim="endpoint" class="field" type="url" autocomplete="off" required /></label><label>API Key<input v-model="providerKey" class="field" type="password" autocomplete="off" :placeholder="data.provider.has_key ? '已安全保存，可留空复用' : '请输入 API Key'" /></label><button class="btn" type="submit" :disabled="mutating || data.active">发现模型并保存连接</button></form>
      <form class="model-connection stack-form" @submit.prevent="discoverDecision"><div class="model-section-heading"><h3>决策模型连接</h3></div><label>决策模型连接 Key<input v-model="decisionKey" class="field" type="password" autocomplete="off" :placeholder="data.jev.has_key ? '已安全保存，可留空复用' : '请输入决策模型 Key'" /></label><button class="btn" :disabled="mutating || data.active" type="submit">发现决策模型</button></form>
      </div><form class="stack-form model-role-form" @submit.prevent="save"><div class="model-section-heading"><h3>研究角色与模型</h3></div><div class="form-grid"><label v-for="role in roles" :key="role">{{ labels[role] }}<select v-model="chosen[role]" class="field" required><option value="">请选择</option><option v-for="model in role === 'decision' ? data.jev.models : data.provider.models" :key="model" :value="model">{{ model }}</option></select></label></div><button class="btn primary" :disabled="mutating || data.active" type="submit">保存模型选择</button></form>
    </template>
  </section>
</template>
<script setup lang="ts">
import { reactive, ref, watch } from 'vue'
import BackendState from './BackendState.vue'
import { useAccountResource } from '../composables/useAccountResource'
type Role = 'news' | 'strategy' | 'reviewer' | 'decision'
type Settings = { configured: boolean; active: boolean; revision: number; models: Record<Role, string>; provider: { endpoint: string; revision: number; has_key: boolean; models: string[] }; jev: { revision: number; has_key: boolean; models: string[] } }
const { data, error, refreshing, refresh, session, mutate, mutating } = useAccountResource('model-settings', (api, signal) => api.read<Settings>('/advisor/model-settings', signal))
const roles: Role[] = ['news', 'strategy', 'reviewer', 'decision'], labels = { news: '资讯分析模型', strategy: '策略模型', reviewer: '复核模型', decision: '决策模型' }
const endpoint = ref(''), providerKey = ref(''), decisionKey = ref(''), message = ref(''), failure = ref('')
const chosen = reactive<Record<Role, string>>({ news: '', strategy: '', reviewer: '', decision: '' })
watch(data, value => { if (value) { endpoint.value = value.provider.endpoint; Object.assign(chosen, value.models) } })
watch(() => session.value?.cache_scope, () => { endpoint.value = ''; providerKey.value = ''; decisionKey.value = ''; message.value = ''; failure.value = ''; roles.forEach(role => { chosen[role] = '' }) })
async function send(path: string, body: unknown, method: string) { failure.value = ''; message.value = ''; try { await mutate(path, body, method); message.value = '配置已由后端保存。' } catch (error) { failure.value = error instanceof Error ? error.message : '连接更新失败。' } }
async function discover() { try { await send('/advisor/models/discover', { endpoint: endpoint.value, revision: data.value!.provider.revision, api_key: providerKey.value }, 'POST') } finally { providerKey.value = '' } }
async function discoverDecision() { try { await send('/advisor/jev-models/discover', { revision: data.value!.jev.revision, api_key: decisionKey.value }, 'POST') } finally { decisionKey.value = '' } }
function save() { return send('/advisor/model-settings', { models: { ...chosen }, revision: data.value!.revision }, 'PUT') }
</script>
