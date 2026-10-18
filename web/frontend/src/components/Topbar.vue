<template>
  <header class="topbar">
    <div class="crumb">工作空间&nbsp; / &nbsp;<span>{{ routeTitle }}</span></div>
    <div class="top-actions">
      <form class="global-search" @submit.prevent="searchWatchlist"><Search :size="16" aria-hidden="true" /><input v-model="search" aria-label="全局自选搜索" placeholder="搜索自选名称或代码" maxlength="80" /><button class="search-clear" type="submit" aria-label="搜索自选"><ArrowRight :size="14" aria-hidden="true" /></button></form>
      <div class="market-ticker"><template v-if="!liveBackend">沪深300 · 演示 <b>3733.85</b> <em class="up">+0.71%</em></template><RouterLink v-else to="/market" class="text-action">沪深300 · 行情快照</RouterLink></div>
      <RouterLink to="/settings?section=appearance" class="theme-select" aria-label="外观偏好"><Moon :size="14" aria-hidden="true" />深色</RouterLink>
      <div class="avatar" :style="{ background: preferences.avatarColor, color: '#fff' }">{{ (liveBackend ? activeBackendSession?.user.username : preferences.displayName)?.slice(0, 1) || '澜' }}</div><span class="admin-name">{{ liveBackend ? activeBackendSession?.user.username ?? '未登录' : preferences.displayName }}</span>
      <RouterLink to="/settings" class="icon-btn" aria-label="账号与设置"><Settings :size="17" aria-hidden="true" /></RouterLink>
    </div>
  </header>
</template>
<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ArrowRight, Moon, Search, Settings } from 'lucide-vue-next'
import { activeBackendSession } from '../composables/useAccountResource'
import { navItems } from '../router'
import { usePreferences } from '../composables/usePreferences'
const { preferences } = usePreferences()
const liveBackend = !!import.meta.env.VITE_API_BASE_URL?.trim()
const route = useRoute(), router = useRouter()
const search = ref('')
const routeTitle = computed(() => navItems.find(item => item.path === route.path)?.label ?? '研究工作台')
function searchWatchlist() { router.push({ path: '/watchlist', query: search.value.trim() ? { q: search.value.trim() } : {} }) }
</script>
