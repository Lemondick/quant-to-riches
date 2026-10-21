<template>
  <PageHeader title="邮件通知" subtitle="SMTP 配置、测试发送与发送记录。" />
  <BackendState :error="error" :refreshing="refreshing && !settings" :has-data="!!settings" @retry="loadSettings()" />
  <p v-if="notice" class="feedback" role="status">{{ notice }}</p>
  <p v-if="actionError" class="feedback error" role="alert">{{ actionError }}</p>
  <div v-if="settings" class="panel card-pad">
    <h2 class="card-title">SMTP 配置</h2>
    <p class="subtle">密码仅保存时提交，不回显、不落日志（走 Secret）。</p>
    <div class="form-grid section-space">
      <label>SMTP 服务器<input v-model.trim="form.host" class="field" placeholder="smtp.example.com" autocomplete="off" /></label>
      <label>端口<input v-model.number="form.port" class="field" type="number" min="1" max="65535" /></label>
      <label>用户名<input v-model.trim="form.user" class="field" autocomplete="username" /></label>
      <label>发件人地址<input v-model.trim="form.from" class="field" placeholder="noreply@example.com" autocomplete="off" /></label>
      <label>密码（留空则不修改）<input v-model="password" class="field" type="password" autocomplete="new-password" /></label>
    </div>
    <div class="inline-actions">
      <button class="btn primary" :disabled="mutating" @click="save()">{{ mutating ? '保存中…' : '保存配置' }}</button>
      <span class="subtle">保存后通过后端统一入口 EmailDeliveryService（指数退避重试 3 次）发送。</span>
    </div>
  </div>
  <div class="panel card-pad section-space">
    <h2 class="card-title">测试发送</h2>
    <p class="subtle">向指定地址发送一封测试邮件，验证 SMTP 配置是否可用。</p>
    <div class="inline-actions section-space">
      <label class="test-to">收件地址<input v-model.trim="testTo" class="field" placeholder="test@example.com" /></label>
      <button class="btn" :disabled="mutating || testing" @click="testSend()">{{ testing ? '发送中…' : '发送测试邮件' }}</button>
    </div>
  </div>
  <div class="panel table-panel section-space">
    <div class="panel-head"><div><h2>发送记录</h2><p>读取 email_send_log，记录统一发送入口的每次尝试。</p></div><button class="btn" :disabled="refreshing" @click="loadLog()">刷新记录</button></div>
    <DataTable table-id="email-send-log" label="邮件发送记录" :headers="['时间', '收件人', '主题', '模板', '状态', '尝试次数', '错误']"
      :rows="log.map(item => [item.created_at, item.to_addr, item.subject, item.template, emailStatusName(item.status), item.attempts, item.last_error ?? '—'])"
      :numeric-columns="[5]" :min-width="960" empty-title="暂无发送记录" empty-text="尚未通过统一入口发送过邮件。" />
  </div>
</template>
<script setup lang="ts">
import { ref, watch } from 'vue'
import PageHeader from './PageHeader.vue'
import BackendState from './BackendState.vue'
import DataTable from './DataTable.vue'
import { ApiError } from '../services/dataApi'
import { useAccountResource } from '../composables/useAccountResource'
import { defaultEmailSettings, emailStatusName, validateEmailSettings, type EmailLogEntry, type EmailSettings } from '../services/batch3Api'

/** 后端接口（流 A 并行开发）：GET/PUT api/v1/notify/email/settings（管理员）·
 *  GET api/v1/notify/email/log；测试发送走 POST api/v1/notify/email/test（假设，联调时确认）。 */
const { error, refreshing, session, read, mutate, mutating } = useAccountResource('admin-email-settings',
  (api, signal) => api.read<unknown>('/auth/session', signal))

const settings = ref<EmailSettings | null>(null)
const form = ref<EmailSettings>(defaultEmailSettings())
const password = ref('')
const testTo = ref('')
const testing = ref(false)
const notice = ref('')
const actionError = ref('')
const log = ref<EmailLogEntry[]>([])

async function loadSettings() {
  try {
    settings.value = await read<EmailSettings>('/notify/email/settings')
    form.value = { ...settings.value }
  } catch (failure) {
    if (!(failure instanceof ApiError && failure.status === 404)) {
      actionError.value = failure instanceof Error ? failure.message : '邮件配置读取失败。'
    }
  }
}

async function loadLog() {
  try {
    const value = await read<{ items: EmailLogEntry[] }>('/notify/email/log?limit=50')
    log.value = Array.isArray(value.items) ? value.items : []
  } catch (failure) {
    actionError.value = failure instanceof Error ? failure.message : '发送记录读取失败。'
  }
}

async function save() {
  notice.value = ''
  actionError.value = ''
  const invalid = validateEmailSettings(form.value)
  if (invalid) { actionError.value = invalid; return }
  try {
    const body: Record<string, unknown> = { ...form.value }
    if (password.value) body.password = password.value
    settings.value = await mutate<EmailSettings>('/notify/email/settings', body, 'PUT')
    form.value = { ...settings.value }
    password.value = ''
    notice.value = 'SMTP 配置已保存。'
  } catch (failure) {
    actionError.value = failure instanceof Error ? failure.message : '保存失败。'
  }
}

async function testSend() {
  notice.value = ''
  actionError.value = ''
  if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(testTo.value.trim())) { actionError.value = '测试收件地址格式无效。'; return }
  testing.value = true
  try {
    await mutate<unknown>('/notify/email/test', { to_addr: testTo.value.trim() })
    notice.value = '测试邮件已提交发送，请查收并核对发送记录。'
    await loadLog()
  } catch (failure) {
    actionError.value = failure instanceof Error ? failure.message : '测试发送失败。'
  } finally {
    testing.value = false
  }
}

watch(session, value => { if (value) { void loadSettings(); void loadLog() } }, { immediate: true })
</script>
<style scoped>
.test-to{display:inline-flex;align-items:center;gap:8px}
.test-to .field{min-width:240px}
</style>
