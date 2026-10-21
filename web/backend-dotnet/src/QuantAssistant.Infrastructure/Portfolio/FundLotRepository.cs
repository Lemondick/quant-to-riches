using System.Globalization;
using System.Security.Cryptography;
using System.Text;
using System.Text.Encodings.Web;
using System.Text.Json;
using System.Text.Json.Nodes;
using QuantAssistant.Core.Errors;
using QuantAssistant.Core.Portfolio;
using QuantAssistant.Infrastructure.Db;

namespace QuantAssistant.Infrastructure.Portfolio;

public sealed record FundLot(string LotId, DateOnly ConfirmedOn, decimal RemainingUnits);
public sealed record FundLotSelection(string LotId, decimal Quantity);

public sealed class FundLotRepository(AccountDatabase database, FundFeeRepository fees)
{
    private static string Money(decimal value) => value.ToString(CultureInfo.InvariantCulture);
    private static decimal Amount(JsonNode row, string key) => decimal.Parse(row[key]!.GetValue<string>(), CultureInfo.InvariantCulture);
    private static DateOnly Date(JsonNode row, string key) => DateOnly.Parse(row[key]!.GetValue<string>());
    private static readonly JsonSerializerOptions FingerprintJson = new() { Encoder = JavaScriptEncoder.UnsafeRelaxedJsonEscaping };

    private static string PositionFingerprint(JsonNode row, string symbol)
    {
        var timestamp = DateTimeOffset.Parse(row["updated_at"]!.GetValue<string>(), CultureInfo.InvariantCulture).ToOffset(TimeSpan.FromHours(8));
        // Match Python datetime.__str__, including six fractional digits only
        // when present. Existing lot fingerprints remain valid after import.
        var formatted = timestamp.ToString(timestamp.Ticks % TimeSpan.TicksPerSecond == 0 ? "yyyy-MM-dd HH:mm:sszzz" : "yyyy-MM-dd HH:mm:ss.ffffffzzz", CultureInfo.InvariantCulture);
        var json = JsonSerializer.Serialize(new[] { symbol, "fund", row["quantity"]!.GetValue<string>(), row["as_of_date"]!.GetValue<string>(), formatted }, FingerprintJson);
        return Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(json))).ToLowerInvariant();
    }

    private static JsonObject View(AccountSql sql, string userId, string symbol)
    {
        var position = sql.Query("SELECT quantity,as_of_date,updated_at FROM positions WHERE user_id=$1 AND symbol=$2 AND asset_type='fund' AND quantity>0", userId, symbol).FirstOrDefault();
        var fingerprint = position is null ? null : PositionFingerprint(position, symbol);
        var snapshot = sql.Query("SELECT id,position_fingerprint,verified_on,evidence_reference FROM fund_lot_snapshots WHERE user_id=$1 AND symbol=$2 ORDER BY id DESC LIMIT 1", userId, symbol).FirstOrDefault();
        var items = snapshot is null ? new JsonArray() : sql.Query("SELECT lot_id,confirmed_on,remaining_units FROM fund_holding_lots WHERE user_id=$1 AND snapshot_id=$2 ORDER BY confirmed_on,lot_id", userId, snapshot["id"]!.GetValue<long>());
        var status = snapshot is null ? "missing" : "reconciled"; var blockers = new JsonArray();
        if (position is null) { status = "unavailable"; blockers.Add("本地主账本尚无该基金的已确认持仓"); }
        else if (snapshot is null) blockers.Add("请按确认单核对每个批次的剩余份额和买入确认日");
        else if (snapshot["position_fingerprint"]!.GetValue<string>() != fingerprint || items.Sum(i => Amount(i!, "remaining_units")) != Amount(position, "quantity"))
        { status = "stale"; blockers.Add("持仓已变化，请重新核对所有剩余批次；不会推断已卖出的批次"); }
        return new() { ["symbol"] = symbol, ["status"] = status,
            ["position_quantity"] = position?["quantity"]?.DeepClone(), ["position_as_of_date"] = position?["as_of_date"]?.DeepClone(),
            ["position_fingerprint"] = fingerprint, ["snapshot_id"] = snapshot?["id"]?.DeepClone(),
            ["verified_on"] = snapshot?["verified_on"]?.DeepClone(), ["evidence_reference"] = snapshot?["evidence_reference"]?.DeepClone(),
            ["items"] = items, ["blockers"] = blockers };
    }

    public JsonObject List(string userId, string symbol) => database.Read(sql => View(sql, userId, FundFeeRepository.Symbol(symbol)));

    public JsonObject Save(string userId, string symbol, FundLot[] items, DateOnly verifiedOn, string reference, string fingerprint, bool confirmed)
    {
        if (!confirmed) throw new LedgerStateError(ErrorCodes.FundLotsUnconfirmed);
        symbol = FundFeeRepository.Symbol(symbol);
        if (items.Length is < 1 or > 100 || items.Any(l => l is null || string.IsNullOrWhiteSpace(l.LotId) || l.LotId.Length > 128
                || l.ConfirmedOn > verifiedOn || l.RemainingUnits <= 0 || l.RemainingUnits >= 100000000000000000000m || decimal.Round(l.RemainingUnits, 8) != l.RemainingUnits)
            || items.Select(l => l.LotId).Distinct().Count() != items.Length || string.IsNullOrWhiteSpace(reference) || reference.Length > 1000
            || fingerprint.Length != 64 || verifiedOn > FundFeeRepository.Today()) throw new ArgumentException("基金批次核对参数无效");
        return database.WriteNonLedger(userId, sql =>
        {
            var current = View(sql, userId, symbol);
            if (current["position_fingerprint"]?.GetValue<string>() != fingerprint || current["position_quantity"] is null)
                throw new LedgerStateError(ErrorCodes.FundLotsStale);
            if (verifiedOn < Date(current, "position_as_of_date")) throw new ArgumentException("核对日期早于持仓日期");
            if (items.Sum(l => l.RemainingUnits) != Amount(current, "position_quantity")) throw new LedgerStateError(ErrorCodes.FundLotsMismatch);
            var ordered = items.OrderBy(l => l.ConfirmedOn).ThenBy(l => l.LotId, StringComparer.Ordinal).ToArray();
            var same = current["status"]!.GetValue<string>() == "reconciled" && Date(current, "verified_on") == verifiedOn
                && current["evidence_reference"]!.GetValue<string>() == reference.Trim()
                && current["items"]!.AsArray().Deserialize<FundLot[]>(FinanceJson.Options)!.SequenceEqual(ordered);
            if (same) return current;
            var requestJson = JsonSerializer.Serialize(new { symbol, fingerprint, verified_on = verifiedOn, evidence_reference = reference.Trim(), items = ordered }, FinanceJson.Options);
            var requestHash = Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(requestJson))).ToLowerInvariant();
            var id = (long)sql.Scalar("INSERT INTO fund_lot_snapshots(user_id,symbol,position_fingerprint,request_fingerprint,verified_on,evidence_reference,created_at) VALUES($1,$2,$3,$4,$5,$6,now()) RETURNING id", userId, symbol, fingerprint, requestHash, verifiedOn, reference.Trim())!;
            foreach (var item in ordered) sql.Execute("INSERT INTO fund_holding_lots(user_id,snapshot_id,lot_id,confirmed_on,remaining_units) VALUES($1,$2,$3,$4,$5)", userId, id, item.LotId, item.ConfirmedOn, item.RemainingUnits);
            return View(sql, userId, symbol);
        });
    }

    public JsonObject Quote(string userId, string symbol, long snapshotId, FundLotSelection[] selections, DateOnly confirmedOn, decimal assumedNav)
    {
        symbol = FundFeeRepository.Symbol(symbol);
        if (selections.Length is < 1 or > 100 || selections.Any(s => s is null) || assumedNav <= 0 || decimal.Round(assumedNav, 8) != assumedNav
            || assumedNav >= 10000000000m || confirmedOn < FundFeeRepository.Today()) throw new ArgumentException("赎回规划参数无效");
        return database.WriteNonLedger(userId, sql =>
        {
            var current = View(sql, userId, symbol);
            if (current["status"]!.GetValue<string>() != "reconciled" || current["snapshot_id"]!.GetValue<long>() != snapshotId) throw new LedgerStateError(ErrorCodes.FundLotsStale);
            var schedule = fees.Current(sql, userId, symbol, confirmedOn);
            var byId = current["items"]!.AsArray().Deserialize<FundLot[]>(FinanceJson.Options)!.ToDictionary(l => l.LotId);
            var selected = new HashSet<string>(); var items = new JsonArray(); var grossTotal = 0m; var feesTotal = 0m;
            foreach (var selection in selections)
            {
                if (!selected.Add(selection.LotId) || !byId.TryGetValue(selection.LotId, out var lot) || selection.Quantity <= 0
                    || selection.Quantity > lot.RemainingUnits || decimal.Round(selection.Quantity, 8) != selection.Quantity) throw new LedgerStateError(ErrorCodes.FundLotsSelectionInvalid);
                decimal gross;
                try { gross = FundFeeRules.Round(selection.Quantity * assumedNav); } catch (OverflowException) { throw new ArgumentException("规划金额超出可计算范围"); }
                if (gross <= 0) throw new ArgumentException("批次估算金额必须为正");
                var days = confirmedOn.DayNumber - lot.ConfirmedOn.DayNumber; var rate = FundFeeRules.RedemptionRate(schedule, days);
                var fee = FundFeeRules.Round(gross * rate); grossTotal += gross; feesTotal += fee;
                items.Add(JsonSerializer.SerializeToNode(new { lot_id = lot.LotId, quantity = selection.Quantity, confirmed_on = lot.ConfirmedOn,
                    holding_days = days, gross_amount = gross, rate, fee }, FinanceJson.Options));
            }
            return new JsonObject { ["symbol"] = symbol, ["snapshot_id"] = snapshotId, ["confirmed_on"] = confirmedOn.ToString("yyyy-MM-dd"),
                ["assumed_nav"] = Money(assumedNav), ["items"] = items, ["total_gross"] = Money(grossTotal), ["total_fee"] = Money(feesTotal),
                ["estimated_net"] = Money(grossTotal - feesTotal), ["cash_credit"] = "0", ["fee_evidence_reference"] = schedule.EvidenceReference,
                ["basis"] = "selected_reconciled_lots_assumed_nav", ["research_only"] = true };
        });
    }
}
