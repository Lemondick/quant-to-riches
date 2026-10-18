<template>
  <form class="panel card-pad stack-form" aria-label="登录后端账户" @submit.prevent="login">
    <h2>登录账户</h2>
    <label>用户名<input v-model.trim="username" class="field" autocomplete="username" required maxlength="32" /></label>
    <label>密码<input v-model="password" class="field" type="password" autocomplete="current-password" required maxlength="128" /></label>
    <p v-if="error" class="feedback error" role="alert">{{ error }}</p>
    <button class="btn primary" :disabled="busy">{{ busy ? '正在登录' : '登录' }}</button>
  </form>
</template>
<script setup lang="ts">
import { ref } from 'vue'
import { createDataApi } from '../services/dataApi'
import { apiCache } from '../services/apiCache'
const emit = defineEmits<{ 'signed-in': [] }>()
const username = ref(''), password = ref(''), busy = ref(false), error = ref('')
const api = createDataApi(import.meta.env.VITE_API_BASE_URL ?? '')
async function login() {
  busy.value = true; error.value = ''
  try { await api.login(username.value, password.value); apiCache.invalidate(); window.dispatchEvent(new Event('focus')); emit('signed-in') }
  catch (failure) { error.value = failure instanceof Error ? failure.message : '登录失败，请重试。' }
  finally { password.value = ''; busy.value = false }
}
</script>
