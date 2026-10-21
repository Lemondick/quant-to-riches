<template>
  <PageHeader title="账号与设置" subtitle="管理个人资料、模型配置、研究偏好和工作台外观。"><template v-if="activeSection !== 'models'"><span v-if="dirty" class="unsaved-label">有未保存的更改</span><button class="btn" :disabled="!dirty" @click="discard">放弃更改</button><button class="btn primary" type="submit" form="preferences-form" :disabled="!dirty"><Save :size="15" aria-hidden="true" />保存更改</button></template></PageHeader>
  <div v-if="activeSection !== 'models'" class="page-context"><span class="data-badge">本浏览器偏好</span><span>保存后应用于工作台，刷新页面仍保留。</span></div>
  <template v-if="activeSection !== 'models'"><p v-if="notice" class="feedback" role="status">{{ notice }}</p><p v-if="error" class="feedback error" role="alert">{{ error }}</p></template>
  <BackendState v-if="account" :error="account.error.value" :refreshing="account.refreshing.value" :has-data="!!account.data.value" @retry="account.refresh()" /><div class="setting-grid">
    <nav class="panel setting-menu" aria-label="设置分类"><button v-for="section in sections" :key="section.id" :class="{ active: activeSection === section.id }" :aria-current="activeSection === section.id ? 'page' : undefined" @click="activeSection = section.id">{{ section.label }}</button></nav>
    <LiveModelSettings v-if="activeSection === 'models' && liveBackend" /><ModelSettings v-else-if="activeSection === 'models'" />
    <component :is="activeSection === 'security' ? 'div' : 'form'" v-else id="preferences-form" class="panel form-panel" @submit.prevent="activeSection !== 'security' && save()">
      <template v-if="activeSection === 'profile'">
        <h2 class="card-title">个人资料</h2><p class="subtle">显示名称与头像会同步到顶部账户入口。</p>
        <div class="profile-preview section-space"><div class="avatar profile-avatar" :style="{ background: draft.avatarColor }">{{ draft.displayName.slice(0, 1) || 'A' }}</div><div><b>{{ draft.displayName || '观澜研究员' }}</b><div class="avatar-colors"><button v-for="color in avatarColors" :key="color.value" type="button" :style="{ background: color.value }" :aria-label="color.name + '头像'" :aria-pressed="draft.avatarColor === color.value" :class="{ selected: draft.avatarColor === color.value }" @click="draft.avatarColor = color.value"></button></div></div></div>
        <label class="form-row"><span class="form-label">用户名</span><input class="field" :value="liveBackend ? account?.session.value?.user.username ?? '登录后显示' : draft.username" readonly aria-label="用户名" /></label>
        <label class="form-row"><span class="form-label">显示名称</span><input v-model.trim="draft.displayName" class="field" maxlength="30" required aria-label="显示名称" /></label>
        <label class="form-row"><span class="form-label">联系邮箱</span><input v-model.trim="draft.email" class="field" type="email" :required="!liveBackend" aria-label="联系邮箱" /></label>
        <label class="form-row"><span class="form-label">显示时区</span><select v-model="draft.timezone" class="field" aria-label="显示时区"><option value="Asia/Shanghai">Asia/Shanghai (UTC+8)</option><option value="Asia/Tokyo">Asia/Tokyo (UTC+9)</option></select></label>
        <p class="form-hint">交易录入时间明确使用北京时间，其他时间展示跟随已保存的显示时区。</p>
      </template>
      <template v-else-if="activeSection === 'security'"><LiveAccountSettings v-if="liveBackend" embedded /><template v-else><h2 class="card-title">登录与安全</h2><p class="subtle">当前是本地演示工作台，尚未连接在线身份服务。</p></template></template>
      <template v-else-if="activeSection === 'notifications'">
        <h2 class="card-title">通知设置</h2><p class="subtle">保存提醒偏好，当前尚未连接通知投递服务。</p><label class="switch-row section-space"><span><b>每日行情提醒</b><small>用于未来通知服务的行情摘要偏好。</small></span><input v-model="draft.dailyReminder" type="checkbox" role="switch" /></label><label class="switch-row"><span><b>重大政策提醒</b><small>用于未来通知服务的政策与事件偏好。</small></span><input v-model="draft.policyReminder" type="checkbox" role="switch" /></label>
      </template>
      <template v-else-if="activeSection === 'research'">
        <h2 class="card-title">研究偏好</h2><p class="subtle">设置默认市场和财务数据的颜色约定。</p><label class="form-row"><span class="form-label">默认市场</span><select v-model="draft.market" class="field" aria-label="默认市场"><option>中国 A 股</option><option disabled>港股（暂无样本）</option><option disabled>美股（暂无样本）</option></select></label><label class="form-row"><span class="form-label">涨跌颜色</span><select v-model="draft.marketColors" class="field" aria-label="涨跌颜色"><option value="green-up">绿涨红跌</option><option value="red-up">红涨绿跌</option></select></label><p class="form-hint">仅改变涨跌数据的颜色，账号状态和成功提示保留原有语义。</p>
      </template>
      <template v-else-if="activeSection === 'appearance'">
        <h2 class="card-title">外观设置</h2><p class="subtle">沿用深蓝风格，按阅读习惯微调。</p><label class="form-row"><span class="form-label">深色层次</span><select v-model="draft.appearance" class="field" aria-label="深色层次"><option value="standard">标准深蓝</option><option value="soft">柔和深蓝</option></select></label><label class="form-row"><span class="form-label">默认表格密度</span><select v-model="draft.density" class="field" aria-label="默认表格密度"><option value="comfortable">舒适</option><option value="compact">紧凑</option></select></label><p class="form-hint">默认密度适用于尚未单独设置视图的表格。</p><label class="switch-row section-space"><span><b>减少动态效果</b><small>停止装饰动画，图表直接展示完整数据。</small></span><input v-model="draft.reducedMotion" type="checkbox" role="switch" /></label>
      </template>
      <template v-else>
        <h2 class="card-title">数据与隐私</h2><p class="subtle">工作台偏好保存在当前浏览器。清除浏览器站点数据会移除本地记录。</p><div class="row-list section-space"><div class="row"><span>在线同步</span><span class="tag blue">未连接</span></div><div class="row"><span>当前数据位置</span><b>本浏览器</b></div></div><div class="inline-actions section-space"><button class="btn" type="button" @click="exportPreferences"><Download :size="15" aria-hidden="true" />导出已保存偏好</button><button class="btn" type="button" @click="resetOpen = true"><RotateCcw :size="15" aria-hidden="true" />恢复默认偏好</button></div><p class="form-hint section-space">恢复偏好只调整个人资料、提醒与外观，账户持仓和研究记录继续保留。</p>
        <div v-if="liveBackend && account" class="section-space"><h3 class="card-title">整账户数据导出</h3><p class="subtle">聚合后端已有接口（持仓总览、账本、历史分区、自选偏好、投顾/归因报告、提醒），打包为单个 JSON 下载。后端暂无统一导出接口，故由前端聚合实现，各分区独立获取、互不影响。</p><div class="inline-actions"><button class="btn primary" type="button" :disabled="accountExport.exporting.value" @click="accountExport.exportAll()"><Download :size="15" aria-hidden="true" />{{ accountExport.exporting.value ? `正在导出（${accountExport.progress.value}）` : '导出整账户数据' }}</button></div><p v-if="accountExport.notice.value" class="feedback" role="status">{{ accountExport.notice.value }}</p><p v-if="accountExport.error.value" class="feedback error" role="alert">{{ accountExport.error.value }}</p></div>
      </template>
    </component>
  </div>
  <AppDialog v-model="resetOpen" title="恢复默认偏好" description="将个人资料、提醒和外观恢复为初始演示设置。账户持仓、账本、收藏和研究记录继续保留。"><template #footer><button class="btn" @click="resetOpen = false">取消</button><button class="btn primary" @click="resetPreferences">恢复默认</button></template></AppDialog>
</template>
<script setup lang="ts">
import LiveAccountSettings from '../components/LiveAccountSettings.vue'
const liveBackend = !!import.meta.env.VITE_API_BASE_URL?.trim()
import BackendState from '../components/BackendState.vue'
import { useAccountResource } from '../composables/useAccountResource'
import { computed, reactive, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { Download, RotateCcw, Save } from 'lucide-vue-next'
import PageHeader from '../components/PageHeader.vue'
import AppDialog from '../components/AppDialog.vue'
import LiveModelSettings from '../components/LiveModelSettings.vue'
import ModelSettings from '../components/ModelSettings.vue'
import { defaultPreferences, usePreferences } from '../composables/usePreferences'
import { downloadFile } from '../utils/format'
import { useAccountExport } from '../composables/useAccountExport'
const account = liveBackend ? useAccountResource('settings-profile', (api, signal) => api.read<Record<string, unknown>>('/preferences', signal)) : undefined
const { preferences } = usePreferences()
const draft = reactive({ ...preferences.value })
if (liveBackend && draft.email === defaultPreferences.email) draft.email = ''
const sections = [{ id: 'profile', label: '个人资料' }, { id: 'models', label: '模型配置' }, { id: 'security', label: '登录与安全' }, { id: 'notifications', label: '通知设置' }, { id: 'research', label: '研究偏好' }, { id: 'privacy', label: '数据与隐私' }, { id: 'appearance', label: '外观设置' }]
const route = useRoute()
const activeSection = ref('profile'), notice = ref(''), error = ref(''), resetOpen = ref(false)
watch(() => route.query.section, value => { activeSection.value = sections.some(section => section.id === value) ? String(value) : 'profile' }, { immediate: true })
const avatarColors = [{ name: '蓝色', value: '#4d8dff' }, { name: '紫色', value: '#9d72ff' }, { name: '青色', value: '#238a91' }]
const dirty = computed(() => JSON.stringify(draft) !== JSON.stringify({ ...preferences.value, email: liveBackend && preferences.value.email === defaultPreferences.email ? '' : preferences.value.email }))
function save() {
  error.value = ''; notice.value = ''
  if (!draft.displayName.trim() || ((!liveBackend || draft.email) && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(draft.email))) { activeSection.value = 'profile'; error.value = '请填写显示名称和有效的联系邮箱。'; return }
  preferences.value = { ...draft, displayName: draft.displayName.trim(), email: draft.email.trim() }
  notice.value = '偏好已保存，工作台显示已更新。'
}
function discard() { Object.assign(draft, preferences.value); error.value = ''; notice.value = '已恢复为上次保存的偏好。' }
function exportPreferences() { downloadFile('观澜-工作台偏好.json', JSON.stringify(preferences.value, null, 2), 'application/json') }
const accountExport = useAccountExport(path => account!.read(path), () => account?.session.value?.user.username ?? 'unknown')
function resetPreferences() { preferences.value = { ...defaultPreferences }; Object.assign(draft, preferences.value); resetOpen.value = false; notice.value = '已恢复默认偏好。' }
</script>
