<template>
  <section class="panel model-settings" aria-labelledby="model-settings-title">
    <header class="model-heading">
      <div><h2 id="model-settings-title">AI 模型配置</h2><p>填写连接 → 读取模型 → 为研究角色选择模型并保存</p></div>
      <span class="tag" :class="{ blue: !fullyVerified }"><CheckCircle2 v-if="fullyVerified" :size="13" aria-hidden="true" /><Cable v-else :size="13" aria-hidden="true" />{{ statusText }}</span>
    </header>
    <p class="model-service-note">{{ api.configured ? '连接和凭据由后端服务管理；读取模型列表不会生成报告。' : '后端服务尚未接入，可先保存模型选择，接入后再验证连接。' }}</p>

    <div class="model-connections">
      <form class="model-connection stack-form" aria-label="兼容 API 连接" :aria-busy="providers.compatible.loading" @submit.prevent="connect('compatible')">
        <div class="model-section-heading"><h3>兼容 API 连接</h3><span class="model-connection-state">{{ providerStatus('compatible') }}</span></div>
        <label>兼容 API 地址<input v-model.trim="draft.baseUrl" class="field" type="url" placeholder="https://你的服务/v1" autocomplete="off" spellcheck="false" required :disabled="busy" aria-label="兼容 API 地址" /></label>
        <label>API Key<span class="model-key-field"><input v-model.trim="keys.compatible" class="field" :type="showKeys.compatible ? 'text' : 'password'" :placeholder="connections.compatible ? '后端已保存，可留空沿用' : '填写该 API 的 Key'" autocomplete="off" spellcheck="false" :required="!connections.compatible" :disabled="busy" aria-label="兼容 API Key" /><button class="model-key-toggle" type="button" :aria-label="showKeys.compatible ? '隐藏兼容 API Key' : '显示兼容 API Key'" :aria-pressed="showKeys.compatible" @click="showKeys.compatible = !showKeys.compatible"><EyeOff v-if="showKeys.compatible" :size="16" aria-hidden="true" /><Eye v-else :size="16" aria-hidden="true" /></button></span></label>
        <div class="inline-actions"><button class="btn" type="submit" :disabled="!api.configured || busy"><RefreshCw :size="14" aria-hidden="true" />{{ providers.compatible.loading ? '正在读取模型…' : '读取可用模型并保存连接' }}</button><button v-if="keys.compatible" class="text-action" type="button" :disabled="busy" @click="clearKey('compatible')">清除 Key</button></div>
        <p v-if="providers.compatible.error" class="feedback error" role="alert">{{ providers.compatible.error }}</p>
      </form>
      <form class="model-connection stack-form" aria-label="Jev 服务连接" :aria-busy="providers.jev.loading" @submit.prevent="connect('jev')">
        <div class="model-section-heading"><h3>Jev 服务连接</h3><span class="model-connection-state">{{ providerStatus('jev') }}</span></div>
        <p class="model-connection-copy">Jev 的服务地址与连接协议由后端配置。</p>
        <label>Jev 官方 API Key<span class="model-key-field"><input v-model.trim="keys.jev" class="field" :type="showKeys.jev ? 'text' : 'password'" :placeholder="connections.jev ? '后端已保存，可留空沿用' : '填写你自己的 Jev 官方 Key'" autocomplete="off" spellcheck="false" :required="!connections.jev" :disabled="busy" aria-label="Jev 官方 API Key" /><button class="model-key-toggle" type="button" :aria-label="showKeys.jev ? '隐藏 Jev Key' : '显示 Jev Key'" :aria-pressed="showKeys.jev" @click="showKeys.jev = !showKeys.jev"><EyeOff v-if="showKeys.jev" :size="16" aria-hidden="true" /><Eye v-else :size="16" aria-hidden="true" /></button></span></label>
        <div class="inline-actions"><button class="btn" type="submit" :disabled="!api.configured || busy"><RefreshCw :size="14" aria-hidden="true" />{{ providers.jev.loading ? '正在读取模型…' : '读取 Jev 可用模型并保存连接' }}</button><button v-if="keys.jev" class="text-action" type="button" :disabled="busy" @click="clearKey('jev')">清除 Key</button></div>
        <p v-if="providers.jev.error" class="feedback error" role="alert">{{ providers.jev.error }}</p>
      </form>
    </div>

    <details class="model-connection-help"><summary>服务连接说明</summary><p>兼容 API 和 Jev 分别读取各自 Key 可见的模型；模型列表可读取不代表有报告调用权限，实际生成时仍需校验。{{ sessionAvailable ? '未提交的 Key 仅暂存在当前标签页会话，关闭标签页后清除，不写入偏好导出或本地备份。' : '浏览器限制了会话保存，未提交的 Key 仅在本次打开期间保留。' }}当前研究页面仍使用演示流程。</p></details>

    <form class="model-role-form" aria-label="模型角色配置" @submit.prevent="save">
      <div class="model-section-heading"><div><h3>研究角色与模型</h3><p>从已读取列表选择，也可以直接填写模型 ID。</p></div><span v-if="dirty" class="unsaved-label">有未保存的配置</span></div>
      <div class="form-grid">
        <label v-for="role in modelRoles" :key="role.id" class="stack-label model-role-label"><span>{{ role.label }}<small>{{ role.provider === 'jev' ? 'Jev' : '兼容 API' }}</small></span><input v-model.trim="draft.models[role.id]" class="field" :list="'model-list-' + role.provider" :aria-label="role.label" :aria-describedby="'model-hint-' + role.id" maxlength="200" :disabled="busy" placeholder="选择或填写模型 ID" spellcheck="false" required /><span :id="'model-hint-' + role.id" class="model-role-hint">{{ missingModel(role.id, role.provider) ? '未在本次列表中找到此 ID，请确认该 Key 的模型权限。' : role.description }}</span></label>
      </div>
      <datalist v-for="provider in providerIds" :id="'model-list-' + provider" :key="provider"><option v-for="model in providers[provider].models" :key="model" :value="model" /></datalist>
      <p v-if="notice" class="feedback" role="status">{{ notice }}</p><p v-if="error" class="feedback error" role="alert">{{ error }}</p>
      <footer class="model-actions"><div class="inline-actions"><button class="btn" type="button" :disabled="busy" @click="read"><Download :size="14" aria-hidden="true" />读取模型配置</button><button class="btn" type="button" :disabled="busy" @click="fillDefaults"><RotateCcw :size="14" aria-hidden="true" />{{ api.configured ? '填入服务端默认值' : '填入示例默认值' }}</button></div><button class="btn primary" type="submit" :disabled="busy"><Save :size="14" aria-hidden="true" />{{ busy ? '正在处理…' : '保存模型配置' }}</button></footer>
      <p class="model-save-hint">{{ api.configured ? '配置保存到后端，同时在本浏览器保留模型选择。' : '当前保存到本浏览器；示例模型 ID 需以服务实际可用列表为准。' }}</p>
    </form>
  </section>
</template>
<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, reactive, ref, watch } from 'vue'
import { Cable, CheckCircle2, Download, Eye, EyeOff, RefreshCw, RotateCcw, Save } from 'lucide-vue-next'
import { useModelConfig } from '../composables/useModelConfig'
import { createModelConfigApi } from '../services/modelConfig'
import { copyModelConfiguration, defaultModelConfiguration, modelRoles, normalizeApiBaseUrl, validateModelConfiguration, type ModelConnections, type ModelProvider, type ModelRole } from '../utils/modelConfig'

const api = createModelConfigApi(import.meta.env.VITE_API_BASE_URL ?? '')
const { configuration, keys, sessionAvailable, saveConfiguration } = useModelConfig()
const draft = reactive(copyModelConfiguration(configuration.value))
const providerIds: ModelProvider[] = ['compatible', 'jev']
const connections = reactive<ModelConnections>({ compatible: false, jev: false })
const providers = reactive({
  compatible: { models: [] as string[], verified: false, loading: false, error: '' },
  jev: { models: [] as string[], verified: false, loading: false, error: '' },
})
const showKeys = reactive({ compatible: false, jev: false })
const notice = ref(''), error = ref(''), working = ref(false)
const busy = computed(() => working.value || providerIds.some(provider => providers[provider].loading))
const lifecycle = new AbortController()
onBeforeUnmount(() => lifecycle.abort())
const dirty = computed(() => JSON.stringify(draft) !== JSON.stringify(configuration.value))
const fullyVerified = computed(() => !dirty.value && providerIds.every(provider => providers[provider].verified) && modelRoles.every(role => !missingModel(role.id, role.provider)))
const statusText = computed(() => dirty.value ? '配置尚未保存' : fullyVerified.value ? '模型列表已验证' : '等待连接验证')

function invalidate(provider: ModelProvider) { providers[provider].verified = false; providers[provider].models = []; providers[provider].error = '' }
watch(() => draft.baseUrl, () => { invalidate('compatible'); connections.compatible = false })
for (const provider of providerIds) watch(() => keys[provider], () => invalidate(provider))
watch(() => [draft.baseUrl, ...modelRoles.map(role => draft.models[role.id])], () => { notice.value = ''; error.value = '' })
function missingModel(role: ModelRole, provider: ModelProvider) { return providers[provider].verified && !providers[provider].models.includes(draft.models[role]) }
function providerStatus(provider: ModelProvider) {
  const state = providers[provider]
  return state.loading ? '正在连接' : state.error ? '连接失败' : state.verified ? `已读取 ${state.models.length} 个模型` : connections[provider] ? '连接已保存 · 待验证' : '待配置'
}
function clearKey(provider: ModelProvider) { keys[provider] = ''; showKeys[provider] = false; notice.value = '已清除当前标签页暂存的 Key；后端已保存的连接不受影响。' }

async function connect(provider: ModelProvider) {
  if (busy.value) return
  const state = providers[provider]
  state.error = ''; notice.value = ''; error.value = ''
  state.verified = false; state.models = []
  try {
    const baseUrl = provider === 'compatible' ? normalizeApiBaseUrl(draft.baseUrl) : ''
    if (!keys[provider] && !connections[provider]) throw new Error('请先填写 API Key。')
    state.loading = true
    const models = provider === 'compatible'
      ? await api.connectCompatible(baseUrl, keys[provider], lifecycle.signal)
      : await api.connectJev(keys[provider], lifecycle.signal)
    if (lifecycle.signal.aborted) return
    if (provider === 'compatible') {
      draft.baseUrl = baseUrl
      saveConfiguration({ ...configuration.value, baseUrl })
    }
    keys[provider] = ''
    showKeys[provider] = false
    // Clear draft credentials before marking this particular connection verified.
    await nextTick()
    connections[provider] = true
    state.models = models; state.verified = true
    notice.value = `已读取 ${models.length} 个${provider === 'jev' ? ' Jev' : ''}模型并保存后端连接，请选择角色模型后保存。`
  } catch (cause) { if (!lifecycle.signal.aborted) state.error = (cause as Error).message }
  finally { state.loading = false }
}
async function read() {
  if (busy.value) return
  working.value = true; notice.value = ''; error.value = ''
  try {
    if (api.configured) {
      const result = await api.read(lifecycle.signal)
      Object.assign(draft, result.configuration)
      saveConfiguration(result.configuration)
      await nextTick()
      Object.assign(connections, result.connections)
      for (const provider of providerIds) invalidate(provider)
    } else Object.assign(draft, copyModelConfiguration(configuration.value))
    await nextTick()
    notice.value = api.configured ? '已读取后端模型配置；可分别读取模型列表来验证连接。' : '已读取本浏览器保存的模型配置。'
  } catch (cause) { if (!lifecycle.signal.aborted) error.value = (cause as Error).message }
  finally { working.value = false }
}
async function fillDefaults() {
  if (busy.value) return
  working.value = true; notice.value = ''; error.value = ''
  try {
    const defaults = api.configured ? await api.defaults(lifecycle.signal) : defaultModelConfiguration
    Object.assign(draft.models, defaults.models)
    await nextTick()
    notice.value = api.configured ? '已填入服务端默认模型，保存后生效。' : '已填入参考图中的示例模型，保存后生效。'
  } catch (cause) { if (!lifecycle.signal.aborted) error.value = (cause as Error).message }
  finally { working.value = false }
}
async function save() {
  if (busy.value) return
  notice.value = ''; error.value = validateModelConfiguration(draft)
  if (error.value) return
  const config = copyModelConfiguration(draft)
  if (config.baseUrl) config.baseUrl = normalizeApiBaseUrl(config.baseUrl)
  working.value = true
  try {
    if (api.configured) await api.save(config, lifecycle.signal)
    if (lifecycle.signal.aborted) return
    Object.assign(draft, config)
    saveConfiguration(config)
    await nextTick()
    notice.value = api.configured ? '模型配置已保存到后端。' : '模型配置已保存到本浏览器，接入后端后可验证连接。'
  } catch (cause) { if (!lifecycle.signal.aborted) error.value = (cause as Error).message }
  finally { working.value = false }
}
</script>
