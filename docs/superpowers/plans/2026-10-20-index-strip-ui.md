# Registry-driven index strip Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Doc02 StageB: display all16registered indices in originalVue market page, truthful per-item quality, registry-driven index history selection, responsive accessible browsing. Preserve11page structure/theme/navigation, fundtop10/search/follow, sectors, and actual candle aggregation.

**Spec:** User-supplied Desktop02功能优化与功能补充实施需求.md, literalpercentencoded filename, sections4–7/12. User already approved ordered execution in independentworktree, no mainmerge/proddeploy. Written design defines required behavior; use it without redundant approval.

**Architecture:** Consume existing MarketSnapshot.index_registry/index_statuses; never import backendregistry JSON into browserproduction or create parallel16-item identity table. A small presentation utility normalizes per-definition views from quotes/status. Add focused IndexQuoteStrip using existing stat-card classes/tokens, no new dependency. Keep StatCard for account pages but remove fixedfake sparkline; no invented history. IndexHistory receives definitions and registryversion fromLiveMarket, stableIDAPI and exactresponseidentity validation. Unconnectedhistory explicit message, no otherindexpoints retained. Node tests for normalization/clock/identity and actual Playwright DOM/layout/interaction tests with explicitly labeled syntheticfixtures derived from authorityJSON in test-only Node scripts.

## Global constraints

- Names/order/market/currency/source capabilities/provisional flags come fromAPIregistry. Missingregistry displaysunavailable rather than inventing16definition objects; othermarket tabs still work.
- Every card shows name,value,change points,percent,market,currency,sourceclock,quality/reason; unavailable invalid numeric values showdash, never0. Unknown_time may retain validated actualquote with explicitlabel; stale retainsrealclock and marks last-valid. Individualchange controls up/down/neutral usingexisting CSSmarketcolorpreference.
- Sourceclock formatted in declaredmarketzone or exactsource offset, neverpretendfetched/cache/browserclock is source. Provider delayunknown explicitpublicsource note, no “realtimeguaranteed” label.
- Desktop4–6visiblecards viahorizontaltrack; mobile scrollsnap,keyboardaccessible, documentoverflow<=1px. Stablecardheights toavoidflash/layoutjump. Provisional16thlabel remains.
- No newAPI,backendidentitylists,DBmigration,livefinancialmutation/modelcall/mainmerge/deploy. StageCforeignhistory/calendar/session work stays separate.

## Review Focus

- Shuffledquoteorder/missingindividual/duplicateID/wrongsymbolorcurrency/statusinvalid cannot relabel/reuse anotheritem or turnnull intozero. Definitionorder alone controls cards.
- Actual source time remains per-market including US DST; unknown/stalefallback neverbecomeslocalnow. Neutralzero must be neutral; allcardsamechange signs independentofarrayposition.
- Dynamicregistry version/key changes and rapid historyselection cannot showpreviousindexOHLC undernewtitle. An unsupported item shows explicitcapability text; oldfourcachedpayload remains backendcompatible.
- Wide/mobile/touch/keyboard do not overflowdocument; scrolling actually reaches16th; eachcard accessiblequalityreason. Fundranking/filter/details/follow andsector tabs remain.
- FixedStatCardbararray and fixedsub-index strip removed; no replacementdecorativechart masquerades as history; no extra11-page redesign.

## Task 1: Index presentation boundary

**Files:** new src/utils/indexPresentation.ts; tests/index-presentation.test.mjs; existing dataApi types onlyifneeded.

**Interfaces:** indexCards(snapshot) ordereddefinitions -> view with definition, quoteorundefined, formattedvalue/change/percent/tone/qualitylabel/reason/source label. Use stableID + matchingregistryexchange_code/market/currency, finitepositivequoteprice/finitechangepercent and exactuniquecandidate; statusinvalid/unavailable overridesdisplay. indexHistoryResponse(value,id) rejectswrongID/items shape and unsupportedquality. No sourceclockrepair.

- [ ] RED Nodecases16orderedcards, shuffledquotes,1missing,wrongID/symbol/market/currency,duplicateID,allstates,0neutral/negative/positiveindependentorder,null/NaN,provisionalflag,sourcezone/missingclock,wronghistoryresponseID andunconnectedempty.
- [ ] Implement minimumutility, keeprawquote clocks separate. Expected alltargettests pass, existingqualitytests pass. Commit.

## Task 2: Original UI index strip and registry selector

**Files:** new components/IndexQuoteStrip.vue; LiveMarket.vue,IndexHistory.vue,StatCard.vue; tests index-presentation onlyifbehaviorrequires; scripts/ui-layout-fixtures.mjs test-onlyauthority import.

**Interfaces:** Strip receives snapshot; carddata-testid/data-index-id expose stableIDsforDOMchecks, horizontalcontainer accessiblelabel. IndexHistory receives IndexDefinition[]/registryVersion, defaultverified沪深300bydeclaredlegacyalias(no duplicatednames), use index_id request & registry-version cachekey, validate exactid. CandleChartCard existing errorstring can carryhistory_not_connected, actualchartheight retained.

- [ ] RED actualbrowser assertions16cards(no4slice),dynamiccolors,nostaticbars,16selectoptions,unknown/invaliddash andunsupportedhistorytext. Update test-onlyfixture fromsharedJSON, explicit sourcefixturemarkers; productiondoesnotloadfixtures.
- [ ] Implement strip four-to-sixdesktop andmobile snap, sourceclock/status/reason, provisionallabel, uniqueIDkey; replace oldmain4+fixedsubstrip andtable withregistry-drivenrows. RemovefixedStatCardbararray (do not introducefakedecoration). Preservefund/sectortabs andallunrelatedpageclasses.
- [ ] Implement dynamicselector stableID and exacthistoryIDguard; clear/omitoldpoints onselectionchanges; errors/capability visible. Expected targetedbrowser+types+unit pass. Commit.

## Task 3: Responsive/interaction acceptance and docs

**Files:** new scripts/verify-index-strip.mjs; existing market-kline/alignment/chartpair/browser fixtures onlynecessarycompat; frontend README/API evidence docs; planprogress.

- [ ] Playwright checks1440/1920/2560/1024/390/320px,4–6desktopvisible,mobilehorizontal-scroll/snap,scrollto16th,nodocumentoverflow,keyboardfocus,missing1doesnotblank15,allqualitystates,sourceclockhonesty,rapidselectionwithlateoldresponse andwrongID rejection. Syntheticfixtures explicit, screenshots/summary underoutput/playwright/index-strip.
- [ ] Full frontendtype/lint/format/Node/build andexistingbrowserregressions; sourceverify; realLinuxCI withPG/Worker0skips. Inspectdesktop/mobile actualscreenshots. Documentactualchecks/limitations and allrulings. Commit.
- [ ] Onefreshwhole-branch review usingexecuting-plans, Important/Critical oneRED->GREENfixpass thenfullCI; archiveallrulings/minors, keepindependentbranchandcontinueDoc02StageC withown-marketrealOHLC/calendars. No merge/deploy/redundantapproval.
