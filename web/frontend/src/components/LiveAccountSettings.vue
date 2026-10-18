<template>
  <PageHeader v-if="!embedded" title="账号与设置" subtitle="当前登录账户与真实会话管理。"><button class="btn" :disabled="refreshing" @click="refresh()">刷新会话</button></PageHeader>
  <BackendState :error="error" :refreshing="refreshing" :has-data="!!data" @retry="refresh()" />
  <p v-if="notice" class="feedback" role="status">{{ notice }}</p><p v-if="actionError" class="feedback error" role="alert">{{ actionError }}</p>
  <template v-if="session && data">
    <div class="panel card-pad"><h2>{{ session.user.username }}</h2><p>账户角色 {{ session.user.role === 'admin' ? '管理员' : '普通用户' }}</p><button class="btn" :disabled="mutating" @click="logout()">退出当前登录</button><button class="btn" :disabled="mutating" @click="revokeOthers()">撤销其他设备会话</button></div>
    <div class="panel table-panel section-space"><DataTable table-id="native-sessions" label="账户登录会话" :headers="['设备', '创建时间', '过期时间', '当前设备']" :rows="data.items.map(item => [item.device, item.created_at, item.expires_at, item.current ? '是' : '否'])" empty-title="暂无会话" /></div>
    <form class="panel card-pad stack-form section-space" aria-label="修改账户密码" @submit.prevent="password()"><h2>修改密码</h2><label>当前密码<input v-model="currentPassword" class="field" type="password" autocomplete="current-password" required /></label><label>新密码<input v-model="newPassword" class="field" type="password" autocomplete="new-password" minlength="15" required /></label><button class="btn primary" :disabled="mutating" type="submit">更新密码</button></form>
    <p class="feedback">修改密码后需要重新登录。</p>
    <div v-if="!embedded" class="panel card-pad section-space"><h2>外观偏好</h2><label>界面风格<select v-model="preferences.appearance" class="field"><option value="standard">标准</option><option value="soft">柔和</option></select></label><label>动效<input v-model="preferences.reducedMotion" type="checkbox" />减少动效</label></div>
    <LiveModelSettings v-if="!embedded" />
  </template>
</template>
<script setup lang="ts">
import { ref, watch } from 'vue'
import PageHeader from './PageHeader.vue'
import DataTable from './DataTable.vue'
import BackendState from './BackendState.vue'
import { useAccountResource } from '../composables/useAccountResource'
import LiveModelSettings from './LiveModelSettings.vue'
import { usePreferences } from '../composables/usePreferences'
withDefaults(defineProps<{ embedded?: boolean }>(), { embedded: false })
const { preferences } = usePreferences()
type Session = { session_id: string; device: string; created_at: string; expires_at: string; current: boolean }
const { data, error, refreshing, refresh, session, mutate, mutating } = useAccountResource('account-sessions', (api, signal) => api.read<{ items: Session[] }>('/auth/sessions', signal))
const currentPassword = ref(''), newPassword = ref(''), notice = ref(''), actionError = ref('')
watch(() => session.value?.cache_scope, () => { currentPassword.value = ''; newPassword.value = '' })
async function act(path: string, body: unknown, message: string) { actionError.value = ''; try { await mutate(path, body); notice.value = message } catch (failure) { actionError.value = failure instanceof Error ? failure.message : '账户操作失败。' } }
async function logout() { await act('/auth/logout', {}, '已退出当前登录。'); window.dispatchEvent(new Event('quant-auth-invalidated')) }
function revokeOthers() { return act('/auth/sessions/revoke-others', {}, '其他设备会话已撤销。') }
async function password() { try { await act('/auth/password', { current_password: currentPassword.value, new_password: newPassword.value }, '密码已更新，请重新登录。') } finally { currentPassword.value = ''; newPassword.value = '' } }
</script>
