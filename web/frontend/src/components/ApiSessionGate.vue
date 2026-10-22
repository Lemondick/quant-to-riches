<template>
  <form class="panel card-pad stack-form" aria-label="登录后端账户" @submit.prevent="login">
    <h2>登录账户</h2>
    <template v-if="!pendingToken">
      <label>用户名<input v-model.trim="username" class="field" autocomplete="username" required maxlength="32" /></label>
      <label>密码<input v-model="password" class="field" type="password" autocomplete="current-password" required maxlength="128" /></label>
    </template>
    <template v-else>
      <p class="subtle">输入验证器的 6 位动态验证码，或使用一次性恢复码。</p>
      <label>验证码或恢复码<input v-model.trim="code" class="field" autocomplete="one-time-code" required maxlength="64" autofocus /></label>
      <button class="text-action" type="button" :disabled="busy" @click="resetChallenge">返回密码登录</button>
    </template>
    <p v-if="error" class="feedback error" role="alert">{{ error }}</p>
    <button class="btn primary" :disabled="busy">{{ busy ? '正在验证' : pendingToken ? '验证并登录' : '登录' }}</button>
  </form>
</template>
<script setup lang="ts">
import { ref } from 'vue'
import { createDataApi, clearEtagCache } from '../services/dataApi'
import { apiCache } from '../services/apiCache'
const emit = defineEmits<{ 'signed-in': [] }>()
const username = ref(''), password = ref(''), busy = ref(false), error = ref('')
const pendingToken = ref(''), code = ref('')
const api = createDataApi(import.meta.env.VITE_API_BASE_URL ?? '')
function resetChallenge() { pendingToken.value = ''; code.value = ''; error.value = '' }
async function login() {
  busy.value = true; error.value = ''
  try {
    const result = pendingToken.value ? await api.verifyTwoFactor(pendingToken.value, code.value) : await api.login(username.value, password.value)
    if ('two_factor_required' in result && result.two_factor_required) {
      pendingToken.value = result.pending_token
      return
    }
    resetChallenge(); apiCache.invalidate(); clearEtagCache(); window.dispatchEvent(new Event('focus')); emit('signed-in')
  }
  catch (failure) { error.value = failure instanceof Error ? failure.message : '登录失败，请重试。' }
  finally { password.value = ''; code.value = ''; busy.value = false }
}
</script>
