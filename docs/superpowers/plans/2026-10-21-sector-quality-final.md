# 板块质量与最终回归计划

基线0c404aabea3db48fcc9d5fc7bca7a0c364700a51，复用独立工作区，codex/sector-quality-final；授权按《02》阶段E继续，无main合并/生产部署/财务写入/模型。仅在最终按恢复可用的executing-plans技能做一次完整分支独立审查，不按任务分派。应用karpathy最小改动原则。

## 盘点与成功标准

既有market_sectors.py/原生质量层和测试已经按source+category+code隔离，东方财富报价分页总数不冒充官方目录总数，新浪不提供行情时间时留空；单源失败保留其他源。保留这些实现，不新增Provider/API/迁移。

UI盘点发现板块速览把零涨跌染上涨色，列表未单独显示invalid/unavailable质量；根目录状态在某条目录旧/报价缺失/时间未知同时存在时只显示首个分支。增量统一板块展示助手，拒绝非数值/明确无效报价，保留真实0和旧来源时间，组合目录/报价/时间状态。维持原布局、供应商/类别筛选、16指数与基金目录独立。

## Task 1：板块展示与读取边界

- [ ] RED：相同名称不同源/类别不混合，真实0/缺值/invalid分开，目录覆盖未知与报价分页完整独立，目录旧+报价未知时间同时展示。
- [ ] UI最小接入：速览中性0、列表质量列/原时钟、组合目录状态。后端既有身份、源失败、时钟与覆盖测试继续执行；真实公共接口只读探测并记录失败，不宣称实时/覆盖完整。
- [ ] 浏览器六宽度、同名不同源/类别、单源不可用、目录旧/未知时间/缺报价/真实0；Node/type/lint/format/build、原11页/指数/目录/K线/两卡回归。
## Task 2：完整回归和验收

- [ ] Linux真实PG/Python/Worker全套零跳过、独立作者整体复核、需求指定八项验收归档；main/服务器不动。


Task1 Interfaces：消费既有MarketSector/SectorCatalog和PublicDataQuality；仅增加展示助手与质量列，原API/schema不新增。复核发现Stamp会覆盖上游拒绝状态并接受旧新浪抓取时钟，因此最小修复原生读取层并写5个失败复现。Expected：RED5/5失败，GREEN MarketFreshness14/14通过，前端60/60；后续完整本地套件无失败，PG跳过明确限制由Task2Linux补齐。

Task2 Interfaces：消费Task1提交源码、连接模式构建与原CI，核对新板块/指数/基金目录和原页面。Expected：四个LinuxCI作业success；Python/Worker/真实PG.NET及工具/报告tests=passed且0失败/错误/跳过；最终审查无未处理Critical/Important，归档仅Markdown。保留旧审批拦截的临时目录，不再删除。
