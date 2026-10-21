<template>
  <LiveMarket v-if="liveBackend" />
  <template v-else>
  <div class="market-page">
    <PageHeader title="市场行情" subtitle="实时掌握指数走势、公募基金排行榜与行业概念板块。">
      <span class="data-badge">演示数据</span>
      <button class="btn" :disabled="isRefreshing" :aria-busy="isRefreshing" @click="handleRefresh">
        <RefreshCw :size="15" :class="{ 'spin-anim': isRefreshing }" />
        刷新行情
      </button>
    </PageHeader>

    <!-- 顶部主标签页切换：指数 / 基金榜 / 板块 -->
    <div class="market-nav-tabs">
      <button type="button" class="market-tab-btn" :class="{ active: currentTab === 'index' }"
        @click="currentTab = 'index'">
        指数
      </button>
      <button type="button" class="market-tab-btn" :class="{ active: currentTab === 'funds' }"
        @click="currentTab = 'funds'">
        基金榜
      </button>
      <button type="button" class="market-tab-btn" :class="{ active: currentTab === 'sectors' }"
        @click="currentTab = 'sectors'">
        板块
      </button>
    </div>

    <!-- ============================================== -->
    <!-- TAB 1: 指数 (INDICES)                          -->
    <!-- ============================================== -->
    <div v-if="currentTab === 'index'" class="tab-content-fade">
      <!-- 指数头部4卡片 -->
      <div class="stats-grid">
        <StatCard label="沪深300" value="3,733.85" delta="+26.37  +0.71%" note="今日分时" :icon="TrendingUp" />
        <StatCard label="上证指数" value="3,886.20" delta="+35.41  +0.92%" note="今日分时" tone="green" :icon="Activity" />
        <StatCard label="深证成指" value="12,064.17" delta="+138.25 +1.16%" note="今日分时"
          :icon="ChartNoAxesColumnIncreasing" />
        <StatCard label="创业板指" value="2,652.18" delta="-6.32  -0.24%" note="今日分时" tone="red"
          :icon="ChartNoAxesColumnDecreasing" />
      </div>

      <!-- 快速副指数条 -->
      <div class="sub-indices-bar">
        <div class="sub-index-item">
          <span class="sub-index-name">科创50</span>
          <span class="sub-index-val">1,048.32</span>
          <span class="sub-index-delta up">+1.88%</span>
        </div>
        <div class="sub-index-item">
          <span class="sub-index-name">中证500</span>
          <span class="sub-index-val">5,980.12</span>
          <span class="sub-index-delta up">+0.65%</span>
        </div>
        <div class="sub-index-item">
          <span class="sub-index-name">恒生指数</span>
          <span class="sub-index-val">20,634.50</span>
          <span class="sub-index-delta up">+0.42%</span>
        </div>
        <div class="sub-index-item">
          <span class="sub-index-name">富时A50</span>
          <span class="sub-index-val">12,985.00</span>
          <span class="sub-index-delta up">+0.78%</span>
        </div>
      </div>

      <!-- 主走势图与盘面分析 -->
      <div class="two-col">
        <ChartCard v-model:range="indexRange" title="主要指数走势" :subtitle="indexRange === '分时' ? '今日分时演示对比' : indexRange + '样本涨跌幅对比'" :ranges="['分时', '近5日', '近1月', '近3月']" :legends="[
          { name: '沪深300', value: '+0.71%', color: '#4d8dff' },
          { name: '上证指数', value: '+0.92%', color: '#42ddb1' },
          { name: '深证成指', value: '+1.16%', color: '#ff9d54' }
        ]" />

        <div class="market-side-panels">
          <!-- 市场盘面广度 -->
          <div class="panel card-pad market-breadth-card">
            <div class="panel-header-inline">
              <div>
                <div class="card-title">市场情绪与盘面</div>
                <p class="subtle">两市上涨 / 下跌家数比</p>
              </div>
              <span class="tag">多头占优</span>
            </div>

            <div class="breadth-ratio-bar">
              <div class="breadth-fill up" style="width: 65%;">3,248 上涨 (65%)</div>
              <div class="breadth-fill down" style="width: 35%;">1,742 下跌 (35%)</div>
            </div>

            <div class="breadth-stats-row">
              <div class="breadth-stat">
                <span class="subtle">涨停家数</span>
                <b class="up">72 家</b>
              </div>
              <div class="breadth-stat">
                <span class="subtle">跌停家数</span>
                <b class="down">2 家</b>
              </div>
              <div class="breadth-stat">
                <span class="subtle">全市场成交额</span>
                <b>18,452 亿</b>
              </div>
              <div class="breadth-stat">
                <span class="subtle">较昨日量比</span>
                <b class="up">+1,240 亿</b>
              </div>
            </div>
          </div>

          <!-- 行业涨跌实时速览 -->
          <div class="panel card-pad">
            <div class="panel-header-inline">
              <div>
                <div class="card-title">行业涨跌速览</div>
                <p class="subtle">热门行业实时动能</p>
              </div>
              <button class="text-link-btn" @click="currentTab = 'sectors'">
                查看全部板块 →
              </button>
            </div>
            <div class="row-list">
              <div class="row">
                <span class="industry-name">房地产开发</span>
                <span class="subtle">领涨: 保利发展</span>
                <b class="up">+2.81%</b>
              </div>
              <div class="row">
                <span class="industry-name">证券期货</span>
                <span class="subtle">领涨: 东方财富</span>
                <b class="up">+2.14%</b>
              </div>
              <div class="row">
                <span class="industry-name">半导体与芯片</span>
                <span class="subtle">领涨: 中芯国际</span>
                <b class="up">+1.98%</b>
              </div>
              <div class="row">
                <span class="industry-name">新能源汽车</span>
                <span class="subtle">领涨: 比亚迪</span>
                <b class="up">+1.42%</b>
              </div>
              <div class="row">
                <span class="industry-name">银行</span>
                <span class="subtle">领跌: 招商银行</span>
                <b class="down">-0.31%</b>
              </div>
              <div class="row">
                <span class="industry-name">医药生物</span>
                <span class="subtle">领跌: 恒瑞医药</span>
                <b class="down">-0.72%</b>
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- 核心宽基与大盘权重跟踪 -->
      <div class="panel table-panel">
        <div class="panel-head">
          <div>
            <h2>权重蓝筹与核心ETF盯盘</h2>
            <p>关键指数权重成分股及核心宽基流动性表现</p>
          </div>
          <button class="btn ghost" @click="currentTab = 'funds'">切换至基金榜单</button>
        </div>
        <div class="table-wrap">
          <table>
            <thead>
              <tr>
                <th>标的名称</th>
                <th>代码</th>
                <th>类型</th>
                <th>现价</th>
                <th>涨跌幅</th>
                <th>成交额</th>
                <th>主力净流入</th>
                <th>市场影响力</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="stock in coreStocks" :key="stock.code">
                <td><b>{{ stock.name }}</b></td>
                <td class="subtle">{{ stock.code }}</td>
                <td><span class="type-badge">{{ stock.type }}</span></td>
                <td>¥{{ stock.price }}</td>
                <td><span :class="stock.delta.startsWith('+') ? 'up-tag' : 'down-tag'">{{ stock.delta }}</span></td>
                <td>{{ stock.volume }}</td>
                <td :class="stock.flow.startsWith('+') ? 'up' : 'down'">{{ stock.flow }}</td>
                <td>
                  <div class="impact-indicator">
                    <div :style="{ width: stock.impact + '%' }"></div>
                  </div>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>

    <!-- ============================================== -->
    <!-- TAB 2: 基金榜 (FUND LEADERBOARD)               -->
    <!-- ============================================== -->
    <div v-else-if="currentTab === 'funds'" class="tab-content-fade">
      <div class="fund-context-line"><span class="data-badge">演示数据</span><span>以下为 {{ fundList.length }} 只基金样本，收益、规模与评级均用于界面演示。</span></div>
      <div class="fund-kpi-grid">
        <div class="panel fund-kpi-card gold-border">
          <div class="kpi-top"><span class="kpi-tag gold"><Trophy :size="13" aria-hidden="true" />本期领涨</span><span class="subtle">{{ selectedPeriod.label }}</span></div>
          <div class="kpi-main-name" :title="fundSummary.top?.name">{{ fundSummary.top?.name ?? '暂无匹配基金' }}</div>
          <div class="kpi-metrics">
            <div><span class="subtle">本期收益</span><div class="kpi-num" :class="returnTone(fundSummary.top?.[fundPeriod])">{{ fundSummary.top?.[fundPeriod] ?? '—' }}</div></div>
            <div><span class="subtle">最新净值</span><div class="kpi-sub-num">{{ fundSummary.top?.nav ?? '—' }}</div></div>
            <div><span class="subtle">样本排名</span><div class="kpi-sub-num">{{ fundSummary.count ? '1 / ' + fundSummary.count : '—' }}</div></div>
          </div>
        </div>
        <div class="panel fund-kpi-card blue-border">
          <div class="kpi-top"><span class="kpi-tag blue"><ChartNoAxesColumnIncreasing :size="13" aria-hidden="true" />样本平均表现</span><span class="subtle">{{ selectedPeriod.label }}</span></div>
          <div class="kpi-main-name">{{ fundCategory === '全部' ? '全部类型' : fundCategory }} · {{ fundSummary.count }} 只样本</div>
          <div class="kpi-metrics">
            <div><span class="subtle">平均收益</span><div class="kpi-num" :class="returnTone(formatReturn(fundSummary.average))">{{ formatReturn(fundSummary.average) }}</div></div>
            <div><span class="subtle">上涨样本</span><div class="kpi-sub-num">{{ fundSummary.positiveCount }} 只</div></div>
            <div><span class="subtle">上涨占比</span><div class="kpi-sub-num">{{ fundSummary.count ? (fundSummary.positiveCount / fundSummary.count * 100).toFixed(0) + '%' : '—' }}</div></div>
          </div>
        </div>
        <div class="panel fund-kpi-card purple-border">
          <div class="kpi-top"><span class="kpi-tag purple"><Layers :size="13" aria-hidden="true" />本期末位</span><span class="subtle">{{ selectedPeriod.label }}</span></div>
          <div class="kpi-main-name" :title="fundSummary.last?.name">{{ fundSummary.last?.name ?? '暂无匹配基金' }}</div>
          <div class="kpi-metrics">
            <div><span class="subtle">本期收益</span><div class="kpi-num" :class="returnTone(fundSummary.last?.[fundPeriod])">{{ fundSummary.last?.[fundPeriod] ?? '—' }}</div></div>
            <div><span class="subtle">最新净值</span><div class="kpi-sub-num">{{ fundSummary.last?.nav ?? '—' }}</div></div>
            <div><span class="subtle">样本排名</span><div class="kpi-sub-num">{{ fundSummary.count ? fundSummary.count + ' / ' + fundSummary.count : '—' }}</div></div>
          </div>
        </div>
        <div class="panel fund-kpi-card green-border">
          <div class="kpi-top"><span class="kpi-tag green"><Star :size="13" aria-hidden="true" />我的关注</span><span class="subtle">{{ selectedPeriod.label }}</span></div>
          <div class="kpi-main-name">{{ followedSummary.count ? '已关注样本的本期表现' : '关注基金，持续跟踪表现' }}</div>
          <div class="kpi-metrics">
            <div><span class="subtle">平均收益</span><div class="kpi-num" :class="returnTone(formatReturn(followedSummary.average))">{{ formatReturn(followedSummary.average) }}</div></div>
            <div><span class="subtle">筛选内关注</span><div class="kpi-sub-num">{{ followedSummary.count }} 只</div></div>
            <div><span class="subtle">关注总数</span><div class="kpi-sub-num">{{ savedFunds.length }} 只</div></div>
          </div>
        </div>
      </div>

      <div class="panel table-panel">
        <div class="panel-head">
          <div><h2>全市场公募基金榜单</h2><p>按所选周期比较样本表现，排名与摘要同步更新。</p></div>
          <div class="period-toggle-group" role="group" aria-label="基金收益周期">
            <button v-for="period in fundPeriods" :key="period.key" type="button" class="period-btn" :class="{ active: fundPeriod === period.key }" :aria-pressed="fundPeriod === period.key" @click="fundPeriod = period.key">{{ period.label }}</button>
          </div>
        </div>
        <FilterBar v-model="fundCategory" v-model:query="fundSearch" :options="fundCategories" class="panel-filters" label="基金类型" search-label="搜索基金名称、代码或基金经理" placeholder="搜索基金名称、代码或基金经理…">
          <button class="sort" type="button" @click="sortFundsAsc = !sortFundsAsc"><ArrowUpDown :size="14" aria-hidden="true" />{{ selectedPeriod.label }}{{ sortFundsAsc ? '升序 ↑' : '降序 ↓' }}</button>
        </FilterBar>
        <DataTable table-id="market-funds" label="公募基金榜单" :headers="fundHeaders" :rows="fundRows" :numeric-columns="[3, 4, 5, 7]" :locked-columns="[0, 4]" :highlight-column="4" :sortable="false" :min-width="1060" empty-title="未匹配到相关基金" empty-text="试试其他基金类型、名称、代码或基金经理。">
          <template #summary>显示 <b>{{ filteredFunds.length }}</b> / {{ fundList.length }} 只样本<span class="summary-divider">·</span>{{ selectedPeriod.label }}排名</template>
          <template #cell-0="{ value, row, rowIndex }">
            <div class="fund-name-cell"><span class="rank-badge" :class="{ top1: fundRanks.get(String(row[1])) === 1, top2: fundRanks.get(String(row[1])) === 2, top3: fundRanks.get(String(row[1])) === 3 }">{{ fundRanks.get(String(row[1])) }}</span><div class="fund-name-text"><b>{{ value }}</b><span class="micro-badge">{{ filteredFunds[rowIndex].tag }}</span></div></div>
          </template>
          <template #cell-1="{ value }"><span class="subtle font-mono">{{ value }}</span></template>
          <template #cell-2="{ value }"><span class="type-badge">{{ value }}</span></template>
          <template #cell-4="{ value }"><span :class="parseFloat(String(value)) > 0 ? 'up-tag' : parseFloat(String(value)) < 0 ? 'down-tag' : ''">{{ value }}</span></template>
          <template #cell-8="{ value }"><span class="star-rating" :aria-label="String(value).length + ' 星演示评级'">{{ value }}</span></template>
          <template #cell-9="{ row }"><button class="fav-btn" :class="{ saved: isSaved(String(row[1])) }" :aria-pressed="isSaved(String(row[1]))" :aria-label="(isSaved(String(row[1])) ? '取消关注：' : '关注：') + row[0]" @click="toggleSave(String(row[1]))"><Star :size="14" :fill="isSaved(String(row[1])) ? 'currentColor' : 'none'" aria-hidden="true" /><span>{{ isSaved(String(row[1])) ? '已自选' : '关注' }}</span></button></template>
          <template #empty-action><button class="btn" @click="resetFundFilters">清除筛选</button></template>
        </DataTable>
      </div>
    </div>

    <!-- ============================================== -->
    <!-- TAB 3: 板块 (SECTORS / BOARDS)                 -->
    <!-- ============================================== -->
    <div v-else-if="currentTab === 'sectors'" class="tab-content-fade">
      <!-- 板块顶层核心汇总3卡片 -->
      <div class="sector-kpi-grid">
        <div class="panel sector-kpi-card">
          <div class="kpi-top">
            <span class="kpi-tag gold">
              <Flame :size="13" /> 今日强势领涨
            </span>
            <span class="subtle">行业板块</span>
          </div>
          <div class="kpi-main-name">房地产开发与服务</div>
          <div class="kpi-metrics">
            <div>
              <span class="subtle">板块涨跌</span>
              <div class="kpi-num up">+2.81%</div>
            </div>
            <div>
              <span class="subtle">领涨龙头</span>
              <div class="kpi-sub-num up">保利发展 +6.24%</div>
            </div>
            <div>
              <span class="subtle">主力流入</span>
              <div class="kpi-sub-num up">+18.4亿</div>
            </div>
          </div>
        </div>

        <div class="panel sector-kpi-card">
          <div class="kpi-top">
            <span class="kpi-tag blue">
              <Layers :size="13" /> 主力资金聚集
            </span>
            <span class="subtle">核心赛道</span>
          </div>
          <div class="kpi-main-name">半导体与集成电路</div>
          <div class="kpi-metrics">
            <div>
              <span class="subtle">主力净流入</span>
              <div class="kpi-num up">+¥38.6亿</div>
            </div>
            <div>
              <span class="subtle">总成交额</span>
              <div class="kpi-sub-num">562.4亿</div>
            </div>
            <div>
              <span class="subtle">板块涨跌</span>
              <div class="kpi-sub-num up">+1.98%</div>
            </div>
          </div>
        </div>

        <div class="panel sector-kpi-card">
          <div class="kpi-top">
            <span class="kpi-tag purple">
              <Sparkles :size="13" /> 概念题材热度TOP1
            </span>
            <span class="subtle">热门题材</span>
          </div>
          <div class="kpi-main-name">华为昇腾 / 算力租赁</div>
          <div class="kpi-metrics">
            <div>
              <span class="subtle">题材涨跌</span>
              <div class="kpi-num up">+3.65%</div>
            </div>
            <div>
              <span class="subtle">涨停个股</span>
              <div class="kpi-sub-num up">5 家涨停</div>
            </div>
            <div>
              <span class="subtle">领涨龙头</span>
              <div class="kpi-sub-num up">软通动力 +12.4%</div>
            </div>
          </div>
        </div>
      </div>

      <!-- 热门板块微缩磁贴（前6大焦点板块可视化） -->
      <div class="hot-sectors-grid">
        <div v-for="sec in hotSectorsPreview" :key="sec.name" class="panel hot-sector-card"
          :class="sec.delta.startsWith('+') ? 'sec-up' : 'sec-down'">
          <div class="sec-top">
            <span class="sec-name">{{ sec.name }}</span>
            <span :class="sec.delta.startsWith('+') ? 'up-tag' : 'down-tag'">
              {{ sec.delta }}
            </span>
          </div>
          <div class="sec-stock-row">
            <span class="subtle">龙头：{{ sec.stock }}</span>
            <span :class="sec.stockDelta.startsWith('+') ? 'up' : 'down'">
              {{ sec.stockDelta }}
            </span>
          </div>
          <div class="sec-bar-wrap">
            <div class="sec-bar-fill" :class="sec.delta.startsWith('+') ? 'up-bar' : 'down-bar'"
              :style="{ width: Math.min(Math.abs(parseFloat(sec.delta)) * 25, 100) + '%' }"></div>
          </div>
          <div class="sec-foot">
            <span class="subtle">主力流入: {{ sec.flow }}</span>
            <span class="subtle">涨跌比: {{ sec.upDown }}</span>
          </div>
        </div>
      </div>

      <!-- 板块详细排行榜与工具栏 -->
      <div class="panel table-panel">
        <div class="panel-head">
          <div>
            <h2>板块全景排行榜</h2>
            <p>行业板块、概念题材与地域板块全维监控，洞悉主力资金流向与领涨龙头</p>
          </div>
          <div class="period-toggle-group">
            <button v-for="s in ['涨幅榜', '跌幅榜', '主力净流入', '成交额榜']" :key="s" class="period-btn"
              :class="{ active: sectorSort === s }" @click="sectorSort = s">
              {{ s }}
            </button>
          </div>
        </div>

        <div class="table-tools">
          <div class="category-tabs">
            <button v-for="t in ['行业板块', '概念板块', '地域板块']" :key="t" :class="{ active: sectorType === t }"
              @click="sectorType = t">
              {{ t }}
            </button>
          </div>

          <div class="searchbox">
            <Search :size="15" />
            <input v-model="sectorSearch" placeholder="搜索板块名称或领涨个股..." />
          </div>

          <div class="stats-counter subtle">
            共计匹配 {{ filteredSectors.length }} 个板块
          </div>
        </div>

        <!-- 板块表格 -->
        <div class="table-wrap">
          <table>
            <thead>
              <tr>
                <th style="width: 60px;">排名</th>
                <th>板块名称</th>
                <th>分类</th>
                <th>板块指数</th>
                <th>涨跌幅</th>
                <th>领涨龙头 (涨跌幅)</th>
                <th>主力净流入</th>
                <th>总成交额</th>
                <th>上涨 / 下跌家数</th>
                <th>换手率</th>
                <th>5日涨幅</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(s, idx) in filteredSectors" :key="s.name" class="sector-row">
                <td>
                  <span class="rank-badge" :class="{
                    top1: idx === 0,
                    top2: idx === 1,
                    top3: idx === 2
                  }">
                    {{ idx + 1 }}
                  </span>
                </td>
                <td>
                  <b class="sector-title-text">{{ s.name }}</b>
                </td>
                <td><span class="type-badge">{{ s.type }}</span></td>
                <td class="font-mono">{{ s.indexVal }}</td>
                <td>
                  <span :class="s.change.startsWith('+') ? 'up-tag' : 'down-tag'">
                    {{ s.change }}
                  </span>
                </td>
                <td>
                  <div class="leader-cell">
                    <span class="leader-name">{{ s.leader }}</span>
                    <span :class="s.leaderDelta.startsWith('+') ? 'up' : 'down'">
                      {{ s.leaderDelta }}
                    </span>
                  </div>
                </td>
                <td :class="s.flow.startsWith('+') ? 'up' : 'down'">
                  <b>{{ s.flow }}</b>
                </td>
                <td class="subtle">{{ s.volume }}</td>
                <td>
                  <span class="up">{{ s.upCount }}</span>
                  <span class="subtle"> / </span>
                  <span class="down">{{ s.downCount }}</span>
                </td>
                <td class="subtle">{{ s.turnover }}</td>
                <td :class="s.day5.startsWith('+') ? 'up' : 'down'">
                  {{ s.day5 }}
                </td>
              </tr>
            </tbody>
          </table>

          <div v-if="!filteredSectors.length" class="empty">
            <div class="empty-icon">
              <Search :size="30" />
            </div>
            <b>未搜索到对应板块</b>
            <span>请检查输入的板块或个股关键字</span>
          </div>
        </div>
      </div>
    </div>
  </div>
  </template>
</template>

<script setup lang="ts">
import LiveMarket from '../components/LiveMarket.vue'
const liveBackend = !!import.meta.env.VITE_API_BASE_URL?.trim()

import { ref, computed, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import DataTable from '../components/DataTable.vue'
import FilterBar from '../components/FilterBar.vue'
import { useFollowedFunds } from '../composables/useFollowedFunds'
import { fundList, fundPeriods, rankFunds, summarizeFunds, formatReturn, type FundPeriod } from '../data/funds'
import {
  RefreshCw,
  Trophy,
  ArrowUpDown,
  TrendingUp,
  Activity,
  ChartNoAxesColumnIncreasing,
  ChartNoAxesColumnDecreasing,
  Search,
  Star,
  Flame,
  Layers,
  Sparkles
} from 'lucide-vue-next'
import PageHeader from '../components/PageHeader.vue'
import StatCard from '../components/StatCard.vue'
import ChartCard from '../components/ChartCard.vue'

// 标签页状态：指数(index) | 基金榜(funds) | 板块(sectors)
const route = useRoute()
const router = useRouter()
const initialTab = String(route.query.tab ?? '')
const currentTab = ref<'index' | 'funds' | 'sectors'>(initialTab === 'funds' || initialTab === 'sectors' ? initialTab : 'index')
watch(currentTab, tab => router.replace({ query: { ...route.query, tab: tab === 'index' ? undefined : tab } }))
const isRefreshing = ref(false)
const indexRange = ref('分时')

const handleRefresh = () => {
  isRefreshing.value = true
  setTimeout(() => {
    isRefreshing.value = false
  }, 700)
}

// -----------------------------------------------------------------
// TAB 1: 指数数据
// -----------------------------------------------------------------
const coreStocks = [
  { name: '沪深300ETF', code: '510300', type: '宽基ETF', price: '3.842', delta: '+0.71%', volume: '94.8亿', flow: '+18.4亿', impact: 92 },
  { name: '贵州茅台', code: '600519', type: '大盘消费', price: '1,542.00', delta: '+1.85%', volume: '88.5亿', flow: '+12.6亿', impact: 88 },
  { name: '宁德时代', code: '300750', type: '新能源', price: '268.40', delta: '+2.41%', volume: '76.2亿', flow: '+15.2亿', impact: 85 },
  { name: '中国平安', code: '601318', type: '金融权重', price: '48.92', delta: '+1.62%', volume: '45.1亿', flow: '+8.9亿', impact: 78 },
  { name: '中芯国际', code: '688981', type: '硬科技', price: '86.50', delta: '+5.41%', volume: '92.6亿', flow: '+24.5亿', impact: 82 },
  { name: '招商银行', code: '600036', type: '银行龙头', price: '38.15', delta: '-0.65%', volume: '34.8亿', flow: '-4.2亿', impact: 72 }
]

// -----------------------------------------------------------------
// TAB 2: 基金榜数据
// -----------------------------------------------------------------
const fundCategories = ['全部', '股票型', '混合型', '指数型/ETF', '债券型', 'QDII/海外']
function loadFundFilters() {
  try {
    const value = JSON.parse(localStorage.getItem('guanlan.fund-filters.v1') ?? '{}')
    return {
      category: fundCategories.includes(value?.category) ? String(value.category) : '全部',
      period: fundPeriods.find(period => period.key === value?.period)?.key ?? 'dayChange',
      query: typeof value?.query === 'string' ? value.query : '',
      ascending: value?.ascending === true
    }
  } catch { return { category: '全部', period: 'dayChange' as FundPeriod, query: '', ascending: false } }
}
const initialFilters = loadFundFilters()
const fundCategory = ref(initialFilters.category)
const fundPeriod = ref<FundPeriod>(initialFilters.period)
const fundSearch = ref(initialFilters.query)
const sortFundsAsc = ref(initialFilters.ascending)
watch([fundCategory, fundPeriod, fundSearch, sortFundsAsc], () => {
  try {
    localStorage.setItem('guanlan.fund-filters.v1', JSON.stringify({ category: fundCategory.value, period: fundPeriod.value, query: fundSearch.value, ascending: sortFundsAsc.value }))
  } catch { /* View remains usable when browser storage is unavailable. */ }
})

const { savedFunds, isSaved, toggleSave } = useFollowedFunds()
function resetFundFilters() { fundCategory.value = '全部'; fundSearch.value = '' }
function returnTone(value?: string) {
  const number = parseFloat(value ?? '')
  return number > 0 ? 'up' : number < 0 ? 'down' : ''
}
const selectedPeriod = computed(() => fundPeriods.find(period => period.key === fundPeriod.value)!)
const filteredFunds = computed(() => rankFunds(fundList, { period: fundPeriod.value, category: fundCategory.value, query: fundSearch.value, ascending: sortFundsAsc.value }))
const fundSummary = computed(() => summarizeFunds(filteredFunds.value, fundPeriod.value))
const followedSummary = computed(() => summarizeFunds(filteredFunds.value.filter(fund => isSaved(fund.code)), fundPeriod.value))
const fundRanks = computed(() => new Map(rankFunds(filteredFunds.value, { period: fundPeriod.value, category: '全部', query: '', ascending: false }).map((fund, index) => [fund.code, index + 1])))
const fundHeaders = computed(() => ['基金名称', '代码', '类型', '最新净值', selectedPeriod.value.label === '日涨跌' ? '日涨跌幅' : selectedPeriod.value.label + '收益率', fundPeriod.value === 'dayChange' ? '近1月参考' : '日涨跌参考', '基金经理', '规模', '演示评级', '操作'])
const fundRows = computed(() => filteredFunds.value.map(fund => [fund.name, fund.code, fund.type, fund.nav, fund[fundPeriod.value], fundPeriod.value === 'dayChange' ? fund.m1 : fund.dayChange, fund.manager, fund.size, '★'.repeat(fund.stars), fund.code]))

// -----------------------------------------------------------------
// TAB 3: 板块数据
// -----------------------------------------------------------------
const sectorType = ref('行业板块')
const sectorSort = ref('涨幅榜')
const sectorSearch = ref('')

const hotSectorsPreview = [
  { name: '房地产开发', delta: '+2.81%', stock: '保利发展', stockDelta: '+6.24%', flow: '+18.4亿', upDown: '86/12' },
  { name: '证券期货', delta: '+2.14%', stock: '东方财富', stockDelta: '+4.82%', flow: '+28.5亿', upDown: '46/3' },
  { name: '半导体集成电路', delta: '+1.98%', stock: '中芯国际', stockDelta: '+5.41%', flow: '+38.6亿', upDown: '112/18' },
  { name: '算力与AI硬件', delta: '+1.86%', stock: '工业富联', stockDelta: '+4.15%', flow: '+22.8亿', upDown: '95/20' },
  { name: '新能源汽车', delta: '+1.42%', stock: '比亚迪', stockDelta: '+3.12%', flow: '+14.6亿', upDown: '130/35' },
  { name: '医药生物', delta: '-0.72%', stock: '恒瑞医药', stockDelta: '-1.82%', flow: '-8.6亿', upDown: '32/115' }
]

const sectorList = [
  { name: '房地产开发', type: '行业板块', indexVal: '2,450.8', change: '+2.81%', leader: '保利发展', leaderDelta: '+6.24%', flow: '+18.4亿', volume: '245.2亿', upCount: 86, downCount: 12, turnover: '3.82%', day5: '+5.40%' },
  { name: '证券', type: '行业板块', indexVal: '1,620.4', change: '+2.14%', leader: '东方财富', leaderDelta: '+4.82%', flow: '+28.5亿', volume: '680.1亿', upCount: 46, downCount: 3, turnover: '4.91%', day5: '+7.80%' },
  { name: '半导体', type: '行业板块', indexVal: '3,892.1', change: '+1.98%', leader: '中芯国际', leaderDelta: '+5.41%', flow: '+38.6亿', volume: '562.4亿', upCount: 112, downCount: 18, turnover: '5.20%', day5: '+11.20%' },
  { name: '计算机设备', type: '行业板块', indexVal: '2,180.5', change: '+1.86%', leader: '工业富联', leaderDelta: '+4.15%', flow: '+22.8亿', volume: '420.8亿', upCount: 95, downCount: 20, turnover: '4.15%', day5: '+8.90%' },
  { name: '汽车整车', type: '行业板块', indexVal: '2,740.3', change: '+1.42%', leader: '比亚迪', leaderDelta: '+3.12%', flow: '+14.6亿', volume: '388.9亿', upCount: 130, downCount: 35, turnover: '3.40%', day5: '+4.20%' },
  { name: '白酒与食品', type: '行业板块', indexVal: '4,210.6', change: '+1.15%', leader: '贵州茅台', leaderDelta: '+1.85%', flow: '+12.1亿', volume: '295.4亿', upCount: 62, downCount: 22, turnover: '2.10%', day5: '+3.10%' },
  { name: '华为昇腾', type: '概念板块', indexVal: '1,890.3', change: '+3.65%', leader: '软通动力', leaderDelta: '+12.4%', flow: '+16.5亿', volume: '188.0亿', upCount: 42, downCount: 4, turnover: '6.45%', day5: '+14.20%' },
  { name: '光伏设备', type: '行业板块', indexVal: '1,720.5', change: '+0.88%', leader: '隆基绿能', leaderDelta: '+2.10%', flow: '+7.2亿', volume: '210.5亿', upCount: 68, downCount: 24, turnover: '2.95%', day5: '+3.50%' },
  { name: '电网设备', type: '行业板块', indexVal: '1,940.2', change: '+0.82%', leader: '国电南瑞', leaderDelta: '+2.20%', flow: '+6.5亿', volume: '186.2亿', upCount: 78, downCount: 34, turnover: '2.40%', day5: '+2.80%' },
  { name: '消费电子', type: '行业板块', indexVal: '2,360.8', change: '+0.65%', leader: '立讯精密', leaderDelta: '+2.05%', flow: '+8.9亿', volume: '312.0亿', upCount: 88, downCount: 42, turnover: '3.62%', day5: '+4.50%' },
  { name: '银行', type: '行业板块', indexVal: '2,890.4', change: '-0.31%', leader: '招商银行', leaderDelta: '-0.65%', flow: '-4.2亿', volume: '154.0亿', upCount: 14, downCount: 28, turnover: '0.90%', day5: '+0.80%' },
  { name: '医药生物', type: '行业板块', indexVal: '3,450.2', change: '-0.72%', leader: '恒瑞医药', leaderDelta: '-1.82%', flow: '-8.6亿', volume: '280.5亿', upCount: 32, downCount: 115, turnover: '2.80%', day5: '-1.40%' },
  { name: '煤炭开采', type: '行业板块', indexVal: '1,820.6', change: '-0.95%', leader: '中国神华', leaderDelta: '-1.24%', flow: '-5.4亿', volume: '112.3亿', upCount: 8, downCount: 26, turnover: '1.20%', day5: '-2.10%' },
  { name: '上海国企改革', type: '概念板块', indexVal: '1,560.2', change: '+2.45%', leader: '上海建工', leaderDelta: '+5.60%', flow: '+11.2亿', volume: '168.4亿', upCount: 58, downCount: 8, turnover: '3.90%', day5: '+6.80%' },
  { name: '长三角经济带', type: '地域板块', indexVal: '2,920.0', change: '+1.12%', leader: '宁波银行', leaderDelta: '+2.30%', flow: '+15.8亿', volume: '430.0亿', upCount: 198, downCount: 65, turnover: '2.80%', day5: '+3.90%' }
]

const filteredSectors = computed(() => {
  let list = sectorList.filter(s => {
    const matchType = sectorType.value === '行业板块'
      ? s.type === '行业板块'
      : s.type === sectorType.value
    const q = sectorSearch.value.trim().toLowerCase()
    const matchSearch = !q || s.name.toLowerCase().includes(q) || s.leader.toLowerCase().includes(q)
    return matchType && matchSearch
  })

  return list.sort((a, b) => {
    if (sectorSort.value === '涨幅榜') {
      return parseFloat(b.change) - parseFloat(a.change)
    } else if (sectorSort.value === '跌幅榜') {
      return parseFloat(a.change) - parseFloat(b.change)
    } else if (sectorSort.value === '主力净流入') {
      const getNum = (str: string) => {
        const val = parseFloat(str)
        return isNaN(val) ? 0 : val
      }
      return getNum(b.flow) - getNum(a.flow)
    } else {
      return parseFloat(b.volume) - parseFloat(a.volume)
    }
  })
})
</script>

<style scoped>
.market-page {
  width: 100%;
}

/* ── 顶部主标签页（完全对应图片风格） ── */
.market-nav-tabs {
  display: flex;
  align-items: center;
  gap: 28px;
  padding: 2px 2px 14px;
  margin-bottom: 14px;
  border-bottom: 1px solid rgba(38, 59, 87, 0.45);
}

.market-tab-btn {
  background: transparent;
  border: none;
  font-size: 16px;
  font-weight: 600;
  color: #8da3bf;
  cursor: pointer;
  padding: 4px 2px 8px;
  position: relative;
  transition: all 0.22s cubic-bezier(0.4, 0, 0.2, 1);
  letter-spacing: 0.6px;
}

.market-tab-btn:hover {
  color: #e5eefc;
}

.market-tab-btn.active {
  color: #5896ff;
  text-shadow: 0 0 16px rgba(77, 141, 255, 0.45);
}

.market-tab-btn.active::after {
  content: "";
  position: absolute;
  bottom: -2px;
  left: 0;
  right: 0;
  height: 2.5px;
  background: linear-gradient(90deg, #4d8dff, #6ea8ff);
  border-radius: 2px;
  box-shadow: 0 0 10px rgba(77, 141, 255, 0.65);
}

/* 标签切换淡入动画 */
.tab-content-fade {
  animation: fadeIn 0.35s ease both;
}

/* ── 指数副条 ── */
.sub-indices-bar {
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 9px 16px;
  margin: 12px 0;
  background: rgba(16, 28, 47, 0.6);
  border: 1px solid rgba(38, 59, 87, 0.5);
  border-radius: 8px;
  overflow-x: auto;
}

.sub-index-item {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  white-space: nowrap;
}

.sub-index-name {
  color: var(--text-3);
}

.sub-index-val {
  font-weight: 600;
  color: #dce8fa;
}

.sub-index-delta {
  font-weight: 600;
}

/* ── 市场行情广度 ── */
.market-side-panels {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.market-breadth-card {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.panel-header-inline {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
}

.text-link-btn {
  background: transparent;
  border: none;
  color: var(--accent);
  font-size: 11px;
  cursor: pointer;
  padding: 0;
  transition: color 0.2s;
}

.text-link-btn:hover {
  color: #79abff;
  text-decoration: underline;
}

.breadth-ratio-bar {
  display: flex;
  height: 24px;
  border-radius: 6px;
  overflow: hidden;
  font-size: 11px;
  font-weight: 600;
}

.breadth-fill {
  display: flex;
  align-items: center;
  justify-content: center;
  transition: width 0.6s ease;
}

.breadth-fill.up {
  background: linear-gradient(90deg, #1f5a43, #2a835f);
  color: #7effd3;
}

.breadth-fill.down {
  background: linear-gradient(90deg, #7c2d3a, #541d26);
  color: #ff919d;
}

.breadth-stats-row {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 8px;
  padding-top: 4px;
  border-top: 1px solid rgba(34, 51, 77, 0.5);
}

.breadth-stat {
  display: flex;
  flex-direction: column;
  gap: 3px;
  font-size: 11px;
}

.industry-name {
  font-weight: 500;
}

/* ── 基金榜 KPI Cards ── */
.fund-kpi-grid,
.sector-kpi-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
  gap: 12px;
  margin-bottom: 14px;
}

.fund-kpi-card,
.sector-kpi-card {
  padding: 14px 16px;
  position: relative;
  overflow: hidden;
  animation: fadeInUp 0.4s ease both;
}

.fund-kpi-card.gold-border {
  border-color: rgba(255, 185, 56, 0.35);
  background: radial-gradient(350px 150px at 100% 0%, rgba(255, 185, 56, 0.08), transparent 70%), var(--card-bg);
}

.fund-kpi-card.blue-border {
  border-color: rgba(77, 141, 255, 0.35);
  background: radial-gradient(350px 150px at 100% 0%, rgba(77, 141, 255, 0.08), transparent 70%), var(--card-bg);
}

.fund-kpi-card.purple-border {
  border-color: rgba(157, 114, 255, 0.35);
  background: radial-gradient(350px 150px at 100% 0%, rgba(157, 114, 255, 0.08), transparent 70%), var(--card-bg);
}

.fund-kpi-card.green-border {
  border-color: rgba(53, 223, 176, 0.35);
  background: radial-gradient(350px 150px at 100% 0%, rgba(53, 223, 176, 0.08), transparent 70%), var(--card-bg);
}

.kpi-top {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 6px;
}

.kpi-tag {
  font-size: 10px;
  font-weight: 600;
  padding: 2px 7px;
  border-radius: 4px;
  display: inline-flex;
  align-items: center;
  gap: 4px;
}

.kpi-tag.gold {
  background: rgba(255, 185, 56, 0.16);
  color: #ffc459;
}

.kpi-tag.blue {
  background: rgba(77, 141, 255, 0.16);
  color: #6ea8ff;
}

.kpi-tag.purple {
  background: rgba(157, 114, 255, 0.16);
  color: #bfa6ff;
}

.kpi-tag.green {
  background: rgba(53, 223, 176, 0.16);
  color: #61f2c7;
}

.kpi-main-name {
  font-size: 14px;
  font-weight: 600;
  color: #e6f0ff;
  margin: 6px 0 10px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.kpi-metrics {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 6px;
  padding-top: 6px;
  border-top: 1px solid rgba(42, 64, 94, 0.4);
}

.kpi-metrics .subtle {
  font-size: 10px;
  color: var(--text-3);
}

.kpi-num {
  font-size: 16px;
  font-weight: 700;
  margin-top: 2px;
}

.kpi-sub-num {
  font-size: 13px;
  font-weight: 600;
  margin-top: 3px;
  color: #c9d8ec;
}

/* ── 周期筛选按钮组 ── */
.period-toggle-group {
  display: flex;
  background: rgba(19, 36, 60, 0.8);
  border: 1px solid rgba(42, 66, 97, 0.6);
  border-radius: 7px;
  padding: 2px;
}

.period-btn {
  border: none;
  background: transparent;
  color: #8da3bf;
  padding: 4px 10px;
  border-radius: 5px;
  font-size: 11px;
  transition: all 0.2s;
  cursor: pointer;
}

.period-btn:hover {
  color: #e5eefc;
}

.period-btn.active {
  background: linear-gradient(135deg, #2e6fe1, #4b8cff);
  color: #fff;
  box-shadow: 0 2px 8px rgba(46, 111, 225, 0.35);
}

/* ── 分类药丸按钮 ── */
.category-tabs {
  display: flex;
  gap: 6px;
  overflow-x: auto;
}

.category-tabs button {
  border: 1px solid rgba(41, 62, 91, 0.7);
  background: rgba(19, 36, 59, 0.7);
  color: #9cafc8;
  padding: 6px 12px;
  border-radius: 7px;
  font-size: 12px;
  white-space: nowrap;
  transition: all 0.2s;
  cursor: pointer;
}

.category-tabs button:hover {
  color: var(--text-1);
  border-color: var(--accent);
}

.category-tabs button.active {
  background: rgba(77, 141, 255, 0.15);
  border-color: var(--accent);
  color: #79aeff;
  font-weight: 600;
}

/* ── 排名徽章 ── */
.rank-badge {
  display: inline-grid;
  place-items: center;
  width: 22px;
  height: 22px;
  border-radius: 6px;
  background: rgba(30, 48, 73, 0.7);
  color: #8fa5c2;
  font-size: 11px;
  font-weight: 600;
}

.rank-badge.top1 {
  background: linear-gradient(135deg, #f7b731, #e18c12);
  color: #fff;
  box-shadow: 0 0 10px rgba(247, 183, 49, 0.5);
}

.rank-badge.top2 {
  background: linear-gradient(135deg, #a5b1c2, #778ca3);
  color: #fff;
  box-shadow: 0 0 8px rgba(165, 177, 194, 0.4);
}

.rank-badge.top3 {
  background: linear-gradient(135deg, #d38753, #aa6332);
  color: #fff;
  box-shadow: 0 0 8px rgba(211, 135, 83, 0.4);
}

.font-mono {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
}

.fund-name-cell {
  display: flex;
  align-items: center;
  gap: 8px;
}

.micro-badge {
  font-size: 10px;
  background: rgba(77, 141, 255, 0.12);
  color: #6ea8ff;
  padding: 1px 5px;
  border-radius: 4px;
}

.type-badge {
  font-size: 10px;
  background: rgba(34, 54, 83, 0.6);
  color: #9cb1ce;
  padding: 2px 6px;
  border-radius: 4px;
  border: 1px solid rgba(50, 75, 110, 0.5);
}

.up-tag {
  color: var(--green);
  font-weight: 600;
}

.down-tag {
  color: var(--red);
  font-weight: 600;
}

.star-rating {
  color: #ffb938;
  letter-spacing: 1px;
  font-size: 12px;
}

.fav-btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  background: rgba(22, 38, 62, 0.8);
  border: 1px solid rgba(43, 67, 99, 0.7);
  color: #8da3bf;
  padding: 4px 8px;
  border-radius: 6px;
  font-size: 11px;
  cursor: pointer;
  transition: all 0.2s;
}

.fav-btn:hover,
.fav-btn.saved {
  border-color: #ffb938;
  color: #ffc252;
}

/* ── 板块焦点磁贴 ── */
.hot-sectors-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
  gap: 12px;
  margin-bottom: 14px;
}

.hot-sector-card {
  padding: 12px 14px;
  display: flex;
  flex-direction: column;
  gap: 8px;
  transition: transform 0.25s, border-color 0.25s;
}

.hot-sector-card:hover {
  transform: translateY(-2px);
  border-color: var(--border-hover);
}

.sec-top {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.sec-name {
  font-size: 14px;
  font-weight: 600;
  color: #e6f0ff;
}

.sec-stock-row {
  display: flex;
  justify-content: space-between;
  font-size: 11px;
}

.sec-bar-wrap {
  height: 5px;
  background: rgba(30, 48, 73, 0.7);
  border-radius: 4px;
  overflow: hidden;
}

.sec-bar-fill {
  height: 100%;
  border-radius: 4px;
  transition: width 0.5s ease;
}

.sec-bar-fill.up-bar {
  background: linear-gradient(90deg, #2fb68f, #48e2b5);
  box-shadow: 0 0 6px rgba(47, 182, 143, 0.5);
}

.sec-bar-fill.down-bar {
  background: linear-gradient(90deg, #d34250, #ff6877);
  box-shadow: 0 0 6px rgba(211, 66, 80, 0.5);
}

.sec-foot {
  display: flex;
  justify-content: space-between;
  font-size: 10px;
}

.sector-title-text {
  font-size: 13px;
  color: #e1edfc;
}

.leader-cell {
  display: flex;
  align-items: center;
  gap: 8px;
}

.leader-name {
  color: #cddbec;
}

.stats-counter {
  margin-left: auto;
  font-size: 11px;
}

.spin-anim {
  animation: spin 0.8s linear infinite;
}

@keyframes spin {
  from {
    transform: rotate(0deg);
  }

  to {
    transform: rotate(360deg);
  }
}

.impact-indicator {
  width: 90px;
  height: 6px;
  background: rgba(32, 52, 79, 0.7);
  border-radius: 3px;
  overflow: hidden;
}

.impact-indicator>div {
  height: 100%;
  background: linear-gradient(90deg, #3d80f0, #61a6ff);
  border-radius: 3px;
}
@media(max-width:1200px) {
  .fund-kpi-grid { grid-template-columns:repeat(2,minmax(0,1fr)); }
  .period-toggle-group { flex-wrap:wrap; }
  .market-page .panel-head { gap:12px; }
}
@media(max-width:640px) {
  .fund-kpi-grid,.sector-kpi-grid,.sectors-grid { grid-template-columns:1fr; }
  .kpi-main-name { font-size:13px; }
  .kpi-top { gap:6px; }
  .market-nav-tabs { gap:22px; }
  .period-toggle-group { width:100%; }
  .period-btn { flex:1 0 30%; min-height:32px; font-size:12px; }
}
</style>
