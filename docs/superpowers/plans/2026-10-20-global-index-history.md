# Global index history and market sessions implementation plan

**Goal:** Continue approved Doc02 StageC: real dailyOHLC per verifiedindex, own-market calendars/sessions and refresh windows, no inventeddata or universalCNclock. Keep StageA stableID/API and StageB originalUI, oldfourhistorycache compatibility, deadline/nonoverlap/coldcorepriority.

**Authority:** user Desktop02功能优化与功能补充实施需求.md sections6/7/10–13. Existing isolatedworktree, no mainmerge/proddeploy, nofinancialwrites/paidmodels. Superpowers plugin helpers were removed midrun; preserve established RED/GREEN/evidence/ledger workflow with explicit commands. No reapproval needed for authorized ordered work. Applicable karpathy+aspnet-core rules apply.

## Files and interfaces

- Shared `exchange_calendars.json` authority extendsverified2026HK/US/JP holidays; new `exchange_sessions.json` authority owns full/halfday session intervals, sourceURLs/revision/verifiedyears. Python/C# read sameembeddedresources, no duplicatedhourstable. Calendaroverride remainsholidayrevision; sessionfacts have separateversion/evidence. Missingmarket/year/schedule remainsunknown, noCNfallback.
- Pythonmarket_calendar/sessionreader and nativeCore market sessions; existingDataQuality uses own-market latest/boundary and actualauctionphase. PreserveCNtests. Halfdays,NYDST,Tokyo15:25preclose/15:30auction andHKrandomCAS honestbounds.
- `public_charts.py` delegatesfocusedhistoryadapters ifneeded. Registryhistory mapping enrichedonlyverifiedidentities. KnownTencent15historyidentityprobe: BJonly1bar; exactEastmoneyBJreturns640 andcode/name/market verified. YahooN225 sourceerrors recorded; do not weakenTLS/guessfutures. USrequests640returns641includingintradaycurrentbar;validatebounded641andsliceaftervalidationifdesired. HKlastfieldnotverifiedsharevolume, omitunsupportedvolume/unit ratherthanmislabelturnover. Partialcurrentbar explicit.
- NativehistorypreferredID uses `{symbol}` oldfour, `{index_id}` otherconnecteditems; exactresponseid/symbol validation. Unverifiedhistory explicitunavailable. Workeraccepts validatedindex_id. No newAPI/migration. Refresherhistoryauxboundedrotation, notqueueall16against20-accountquota; quotesbounded20.
- Existing frontendselectors/candleperiods remain; partial/latestsource/historycounts surfacedonlynecessarycaption, notredesign. Fundtaxonomy/sectorlaterD/E.

## Evidence already captured before code

- OfficialNYSE2026holiday/earlyclose/corehours page, officialJPX2026calendar+cashhours/preclose pages, officialHKEXCT075/25full/halfdates+securitieshours. Localnotes `output/indices-c/implementation-notes.txt`; evidenceURLs recordindurabledecisions.
- Publichistoryprobe2026-10-20T14:43Z saved15Tencentpayloads; exactBJseparateEastmoney640observations. Yahoo5ytimeoutand2yTLSerror retainedasfailure, no usablehistoryclaimyet. First probe usedwrongrelativeoutputpath, recapturedaftercorrectrootmkdir; no productiondatachanged.

## Task 1: Verified global calendar/session quality

- [x] RED sharedPython/.NETcases CNunchanged,HKholidayOct19vsCNopen,JPholidayOct12vsUSopen,USNYDSTMarch/November,HKhalfFeb16/Dec24/Dec31andnoafternoon,USNov27/Dec24close13,JP09/11:30/12:30/15:25/15:30,missingyear/unknownmarket,latestpriorholiday/missingcrossyear,closing/lunchboundaries andauctioncapability. InvalidsharedschedulecannotfallbackCN.
- [x] Implementsharedresources/readers+embedding+own-marketmetadata. Addseparatesessionrevision/sourcefieldswhereuseful. ExistingcalendarteststhatassumedHKunconfigurednowuseEmptyfixture, notsilentlydeleteunknownmarketcoverage. ExplicitstageAforeignunknown testsuseEmptyforeigncalendarwhenneeded.
- [x] Runfocused/source/fullPython/Worker/nativechecks; realLinuxPGCI required. Commitonlytaskfiles, publishtaskbranchforCI. Recordallrulings.

## Task 2: Per-index true daily history

- [x] RED exactprovideridentity/type/market/currency/timezone,wrongcode/futures/nonfinite/inconsistentOHLC/duplicate/unordered/future/knowncloseddate,partialtodaybar,missingvolume,isolatedsourcefailures,atmost641Tencent/640publishedbound, oldfouraliases,registeredunknown,unsupportedhistorynotqueued.
- [x] ImplementTencentCN/HK/USverifiedhistoryandEastmoneyBJ plusNikkeiYahoo cashhistoryonlyifindependentlyverified. Updateoneauthorityhistorycapability/evidence withoutconfusingquoteverification/productstatus. IfallverifiedpublicsourcesfailNikkei, keepunavailablehonestandcaptureexactlimitation; do notstopother15. No cross-sourcepointstitching or fakeNAV/intraday/volume.
- [x] Validateallactualsavedpayloadswithoutpermanentlivepricefixtures, boundedliveprobe throughactualadapterwhereappropriate; runWorker/Pythoncontracts/source. Commit.

## Task 3: Native history routing and own-market refresh

- [x] RED realPGold4samecacheinputs/noextrajobs,newIDcacheisolation/wrongresponseidentity/unconnectednoqueue. RefreshduringUSwhileCNclosed,JP/HKlunch/halfday,holidays/DST; allclosedretainslastvalidandcooldown/manualrefresh, nooverlap, corebeforeaux, boundedhistoryrotationnot16jobs.
- [x] ImplementnativepreferredroutingandWorkerinput; deriveallquote/historylistsfromregistry, max20quotesandboundedaux. Routinegatedbyanyactualmarketwindow; per-marketbatchcacheskeylive/closedphase soopeningcannotreuseclosedTTL, closedquotesretainoriginalclockswithlongerTTL, missingcalendar staysunknown. Explicitrefresh/coldstart mayprepareall, neverreceipt-as-source.
- [x] Keeprestingcachedsnapshotavailablethroughweekend/holidaywithoutpromotingfresh; adjustderivedsnapshotretentiononlyifnecessary, testemptyguardaccountprivacyunchanged. Publishmarket_sessions/refreshwindowmetadatathroughexistingAPI ifneeded; truthfulUIcopy. RunactualPGCIandexistingrefresh/latency/UIchecks. Commit.

## Task 4: StageC acceptance

- [x] Reviewwholefinaldiffinaseparateauthorpass, recordlimitationsclearly(noadditionalagentswithoutapplicableauthorization). FullLinuxCIrealPG/Worker0skip, frontendNode/type/lint/format/build+connectedbrowser,sourcechecks,actualhistory/qualityliveobservationswithprovidererrorsdistinctfromcodepass.
- [x] UpdateAPI/docs/evidence/centralprogress, allrulings/minorsandremainingD/E. No mainmerge/deploy; continuefundtaxonomyinventoryandimplementation.
