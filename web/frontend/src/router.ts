import { createRouter, createWebHistory } from 'vue-router'
import { createRoutePreloader } from './services/routePreload'
import LiveAssetOverview from './components/LiveAssetOverview.vue'
const AssetOverview = import.meta.env.VITE_API_BASE_URL ? LiveAssetOverview : () => import('./views/AssetOverview.vue')
const PortfolioAnalysis = import.meta.env.VITE_API_BASE_URL ? () => import('./components/LivePortfolioAnalysis.vue') : () => import('./views/PortfolioAnalysis.vue')
const TradeLedger = import.meta.env.VITE_API_BASE_URL ? () => import('./components/LiveTradeLedger.vue') : () => import('./views/TradeLedger.vue')
const Watchlist = import.meta.env.VITE_API_BASE_URL ? () => import('./components/LiveWatchlist.vue') : () => import('./views/Watchlist.vue')
const Market = import.meta.env.VITE_API_BASE_URL ? () => import('./components/LiveMarket.vue') : () => import('./views/Market.vue')
const AIStock = import.meta.env.VITE_API_BASE_URL ? () => import('./components/LiveResearch.vue') : () => import('./views/AIStock.vue')
const FundReview = import.meta.env.VITE_API_BASE_URL ? () => import('./components/LiveFundReview.vue') : () => import('./views/FundReview.vue')
const QuantStrategy = import.meta.env.VITE_API_BASE_URL ? () => import('./components/LiveQuantStrategy.vue') : () => import('./views/QuantStrategy.vue')
const News = import.meta.env.VITE_API_BASE_URL ? () => import('./components/LiveNews.vue') : () => import('./views/News.vue')
const Admin = import.meta.env.VITE_API_BASE_URL ? () => import('./components/LiveAdmin.vue') : () => import('./views/Admin.vue')
const AccountSettings = () => import('./views/AccountSettings.vue')
const ApiConsole = () => import('./components/LiveApiConsole.vue')
const ReportTasks = () => import('./components/LiveReportTasks.vue')
const Calendar = () => import('./components/LiveCalendar.vue')
const Rebalance = import.meta.env.VITE_API_BASE_URL ? () => import('./components/LiveRebalance.vue') : () => import('./views/Rebalance.vue')
const Valuation = import.meta.env.VITE_API_BASE_URL ? () => import('./components/LiveValuation.vue') : () => import('./views/Valuation.vue')
const FundHealth = import.meta.env.VITE_API_BASE_URL ? () => import('./components/LiveFundHealth.vue') : () => import('./views/FundHealth.vue')
const PortfolioBacktest = import.meta.env.VITE_API_BASE_URL ? () => import('./components/LivePortfolioBacktest.vue') : () => import('./views/PortfolioBacktest.vue')
const Announcements = import.meta.env.VITE_API_BASE_URL ? () => import('./components/LiveAnnouncements.vue') : () => import('./views/Announcements.vue')
const Risk = import.meta.env.VITE_API_BASE_URL ? () => import('./components/LiveRisk.vue') : () => import('./views/Risk.vue')
const Cost = import.meta.env.VITE_API_BASE_URL ? () => import('./components/LiveCost.vue') : () => import('./views/Cost.vue')
const ResearchFeed = import.meta.env.VITE_API_BASE_URL ? () => import('./components/LiveResearchFeed.vue') : () => import('./views/ResearchFeed.vue')
const VirtualScrollLab = () => import('./views/VirtualScrollLab.vue')

const routePreloader = createRoutePreloader({
  '/': () => import.meta.env.VITE_API_BASE_URL ? Promise.resolve(LiveAssetOverview) : import('./views/AssetOverview.vue'),
  '/portfolio': PortfolioAnalysis, '/ledger': TradeLedger, '/watchlist': Watchlist,
  '/market': Market, '/ai-stock': AIStock, '/fund-review': FundReview,
  '/quant': QuantStrategy, '/news': News, '/admin': Admin, '/settings': AccountSettings,
  '/api-console': ApiConsole,
  '/reports': ReportTasks,
  '/calendar': Calendar,
  '/rebalance': Rebalance,
  '/valuation': Valuation,
  '/health': FundHealth,
  '/portfolio-backtest': PortfolioBacktest,
  '/announcements': Announcements,
  '/risk': Risk,
  '/cost': Cost,
  '/research': ResearchFeed,
  '/lab/virtual-scroll': VirtualScrollLab,
})
export const preloadRoute = routePreloader.preload
export const warmRouteComponents = routePreloader.warm

export const navItems = [
  { path: '/', label: '资产总览', group: '资产管理', icon: 'LayoutDashboard' },
  { path: '/portfolio', label: '组合分析', group: '资产管理', icon: 'ChartNoAxesCombined' },
  { path: '/ledger', label: '交易账本', group: '资产管理', icon: 'WalletCards' },
  { path: '/watchlist', label: '我的自选', group: '市场观察', icon: 'Star' },
  { path: '/market', label: '市场行情', group: '市场观察', icon: 'Map' },
  { path: '/ai-stock', label: 'AI选股', group: '投资研究', icon: 'Sparkles', badge: 'AI' },
  { path: '/fund-review', label: '基金复核', group: '投资研究', icon: 'BadgeDollarSign' },
  { path: '/quant', label: '量化策略', group: '投资研究', icon: 'Bot' },
  { path: '/news', label: '市场资讯', group: '投资研究', icon: 'Newspaper' },
  { path: '/rebalance', label: '再平衡', group: '投资研究', icon: 'Scale' },
  { path: '/valuation', label: '估值仪表盘', group: '投资研究', icon: 'Gauge' },
  { path: '/health', label: '基金体检', group: '投资研究', icon: 'Stethoscope' },
  { path: '/portfolio-backtest', label: '组合回测', group: '投资研究', icon: 'History' },
  { path: '/announcements', label: '公告流', group: '投资研究', icon: 'Megaphone' },
  { path: '/risk', label: '风险看板', group: '投资研究', icon: 'ShieldAlert' },
  { path: '/cost', label: '持有成本', group: '资产管理', icon: 'Receipt' },
  { path: '/research', label: '研报资讯', group: '投资研究', icon: 'Library' },
  { path: '/admin', label: '管理者后台', group: '系统', icon: 'UsersRound' },
  { path: '/settings', label: '账号与设置', group: '系统', icon: 'Settings2' },
  { path: '/api-console', label: 'API 控制台', group: '高级工具', icon: 'TerminalSquare', badge: '171' },
  { path: '/reports', label: '报告任务', group: '高级工具', icon: 'ClipboardList' },
  { path: '/calendar', label: '交易日历', group: '高级工具', icon: 'CalendarDays' }
]

export default createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', component: AssetOverview },
    { path: '/portfolio', component: PortfolioAnalysis },
    { path: '/ledger', component: TradeLedger },
    { path: '/watchlist', component: Watchlist },
    { path: '/market', component: Market },
    { path: '/ai-stock', component: AIStock },
    { path: '/fund-review', component: FundReview },
    { path: '/quant', component: QuantStrategy },
    { path: '/news', component: News },
    { path: '/admin', component: Admin },
    { path: '/settings', component: AccountSettings },
    { path: '/api-console', component: ApiConsole },
    { path: '/reports', component: ReportTasks },
    { path: '/calendar', component: Calendar },
    { path: '/rebalance', component: Rebalance },
    { path: '/valuation', component: Valuation },
    { path: '/health', component: FundHealth },
    { path: '/portfolio-backtest', component: PortfolioBacktest },
    { path: '/announcements', component: Announcements },
    { path: '/risk', component: Risk },
    { path: '/cost', component: Cost },
    { path: '/research', component: ResearchFeed },
    { path: '/lab/virtual-scroll', component: VirtualScrollLab }
  ]
})
