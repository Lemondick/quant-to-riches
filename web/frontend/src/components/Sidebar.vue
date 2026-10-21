<template>
  <aside class="sidebar">
    <div class="brand">
      <div class="brand-mark">澜</div>
      <div>
        <div class="brand-name">观澜</div>
        <div class="brand-sub">GUANLAN · RESEARCH</div>
      </div>
    </div>

    <nav class="side-nav">
      <template v-for="group in groups" :key="group">
        <div class="nav-group">{{ group }}</div>
        <RouterLink
          v-for="item in grouped[group]"
          :key="item.path"
          :to="item.path"
          @pointerenter="preloadRoute(item.path)"
          @focus="preloadRoute(item.path)"
          @pointerdown="preloadRoute(item.path)"
          :aria-label="item.label"
          :title="item.label"
          class="nav-item"
          active-class="active"
        >
          <component :is="icons[item.icon as keyof typeof icons]" :size="17" :stroke-width="1.8" />
          <span>{{ item.label }}</span>
          <span v-if="item.badge" class="nav-badge">{{ item.badge }}</span>
        </RouterLink>
      </template>
    </nav>

    <div class="side-bottom">
      <RouterLink to="/admin" class="nav-item" active-class="active" aria-label="管理者后台" title="管理者后台" @pointerenter="preloadRoute('/admin')" @focus="preloadRoute('/admin')" @pointerdown="preloadRoute('/admin')"><UsersRound :size="17"/><span>管理者后台</span></RouterLink>
      <RouterLink to="/settings" class="nav-item" active-class="active" aria-label="账号与设置" title="账号与设置" @pointerenter="preloadRoute('/settings')" @focus="preloadRoute('/settings')" @pointerdown="preloadRoute('/settings')"><Settings2 :size="17"/><span>账号与设置</span></RouterLink>
      <div class="account-foot"><span class="status-dot"></span> 独立账户 · 专注研究</div>
    </div>
  </aside>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import {
  LayoutDashboard, ChartNoAxesCombined, WalletCards, Star, Map, Sparkles, BadgeDollarSign,
  Bot, Newspaper, UsersRound, Settings2, TerminalSquare, ClipboardList, CalendarDays,
  Scale, Gauge, Stethoscope, History, Megaphone, ShieldAlert, Receipt, Library,
  Target,
  Wallet,
  PiggyBank,
} from 'lucide-vue-next'
import { navItems, preloadRoute } from '../router'
const icons = {
  LayoutDashboard, ChartNoAxesCombined, WalletCards,
  Star, Map, Sparkles, BadgeDollarSign,
  Bot, Newspaper, UsersRound, Settings2, TerminalSquare, ClipboardList, CalendarDays,
  Scale,
  Gauge,
  Stethoscope,
  History,
  Megaphone,
  ShieldAlert,
  Receipt,
  Library,
  Target,
  Wallet,
  PiggyBank
}
const groups = ['资产管理', '市场观察', '投资研究', '高级工具']
const grouped = computed(() => Object.fromEntries(groups.map(g => [g, navItems.filter(i => i.group === g)])))
</script>
