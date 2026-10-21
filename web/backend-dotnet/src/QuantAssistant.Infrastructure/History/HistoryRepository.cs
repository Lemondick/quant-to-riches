using System.Text;
using System.Data;
using System.Globalization;
using Npgsql;
using QuantAssistant.Core.History;

namespace QuantAssistant.Infrastructure.History;

/// <summary>PostgreSQL 历史查询仓储：ledger/actions/corrections 三 section 分页 + CSV 导出 + onboarding。SQL 参数化，按 user_id 隔离。</summary>
public sealed class HistoryRepository(string connectionString) : IHistoryRepository
{
    private static readonly string[] Sections = ["ledger", "actions", "corrections"];
    private const int ExportLimit = 100_000;

    // ledger：交易 + 分红联合视图（含冲正标记），对应旧站点 LEDGER_HISTORY。
    private const string LedgerSql =
        "SELECT t.id, CASE WHEN lower(coalesce(t.source,''))='dca' THEN 'dca' " +
        "WHEN t.action IN ('buy','add') THEN 'buy' " +
        "WHEN t.action IN ('sell','reduce') THEN 'sell' ELSE t.action END AS entry_type, " +
        "t.symbol, coalesce(i.name,t.symbol) AS name, t.asset_type, " +
        "t.quantity, t.price, t.gross_amount, t.fees, t.cash_delta AS net_amount, " +
        "t.traded_at AS occurred_at, t.note, t.reversed_by_correction_id IS NOT NULL AS reversed " +
        "FROM transactions t LEFT JOIN instruments i ON i.symbol=t.symbol AND i.user_id=t.user_id " +
        "WHERE t.user_id=@uid " +
        "UNION ALL " +
        "SELECT d.id, 'dividend', d.symbol, coalesce(i.name,d.symbol), d.asset_type, " +
        "NULL, NULL, d.gross_amount, d.fees, d.net_amount, d.paid_at, d.note, " +
        "d.reversed_by_correction_id IS NOT NULL AS reversed " +
        "FROM dividend_events d LEFT JOIN instruments i ON i.symbol=d.symbol AND i.user_id=d.user_id " +
        "WHERE d.user_id=@uid";

    private const string ActionsSql =
        "SELECT * FROM corporate_action_events WHERE user_id=@uid";

    private const string CorrectionsSql =
        "SELECT c.*, coalesce(t.symbol,d.symbol) AS symbol FROM ledger_corrections c " +
        "LEFT JOIN transactions t ON c.target_type='transaction' AND c.target_id=t.id AND t.user_id=@uid " +
        "LEFT JOIN dividend_events d ON c.target_type='dividend' AND c.target_id=d.id AND d.user_id=@uid " +
        "WHERE c.user_id=@uid";

    public HistoryPage Query(string userId, string section, int page, int limit, string? symbol, DateOnly? start, DateOnly? end, bool export)
        => QueryAsync(userId, section, page, limit, symbol, start, end, export).GetAwaiter().GetResult();

    public async Task<HistoryPage> QueryAsync(string userId, string section, int page, int limit, string? symbol, DateOnly? start, DateOnly? end, bool export, CancellationToken cancellation = default)
    {
        if (!Sections.Contains(section))
        {
            throw new UnknownHistorySectionError(section);
        }

        if (start is not null && end is not null && start > end)
        {
            throw new ArgumentException("开始日期不能晚于结束日期");
        }
        if (page is < 1 or > 100_000 || limit is < 1 or > 500)
            throw new ArgumentException("分页参数越界");
        if (symbol?.Trim().Length > 32)
            throw new ArgumentException("标的代码最多32个字符");

        var (innerSql, timeColumn) = section switch
        {
            "ledger" => (LedgerSql, "occurred_at"),
            "actions" => (ActionsSql, "effective_at"),
            "corrections" => (CorrectionsSql, "effective_at"),
            _ => throw new UnknownHistorySectionError(section),
        };

        var filtered = BuildFiltered(innerSql, symbol, start, end, timeColumn);

        await using var conn = new NpgsqlConnection(connectionString);
        await conn.OpenAsync(cancellation);
        // Count and rows belong to the same snapshot, including concurrent ledger writes.
        await using var transaction = await conn.BeginTransactionAsync(IsolationLevel.RepeatableRead, cancellation);

        var total = await Count(conn, transaction, filtered, userId, symbol, start, end, cancellation);
        if (export && total > ExportLimit)
        {
            throw new HistoryExportTooLargeError();
        }

        var selectedPage = export ? 1 : page;
        var selectedLimit = export ? ExportLimit : limit;
        var items = await ReadPage(conn, transaction, filtered, timeColumn, section == "ledger", userId, symbol, start, end, selectedPage, selectedLimit, cancellation);
        await transaction.CommitAsync(cancellation);

        var result = new HistoryPage(items, total, selectedPage, selectedLimit, !export && page * limit < total);
        if (export)
        {
            result = result with { Csv = CsvText(items) };
        }

        return result;
    }

    public OnboardingStatus Onboarding(string userId)
    {
        // batch5-#7：4 次 count(*) round-trip 合并为单条语句（标量子查询），4 → 1。
        using var conn = Open();
        using var cmd = conn.CreateCommand();
        cmd.CommandText = """
            SELECT (SELECT count(*) FROM positions WHERE user_id=@uid) AS positions,
                   (SELECT count(*) FROM positions WHERE user_id=@uid AND asset_type='cash') AS cash,
                   (SELECT count(*) FROM portfolio_valuations WHERE user_id=@uid) AS valuation,
                   (SELECT count(*) FROM market_advisor_reports WHERE user_id=@uid AND status='completed') AS reports
            """;
        cmd.Parameters.AddWithValue("uid", userId);
        using var reader = cmd.ExecuteReader();
        reader.Read();
        var positions = reader.GetInt32(0);
        var cash = reader.GetInt32(1);
        var valuation = reader.GetInt32(2);
        var reports = reader.GetInt32(3);

        return new OnboardingStatus(
        [
            new OnboardingStep("portfolio", "建立账户、持仓和现金", positions > 0 && cash > 0, "/pages/import/index"),
            new OnboardingStep("baseline", "完成首个有效净值基线", valuation > 0, "/pages/home/index"),
            new OnboardingStep("models", "配置自己的模型服务", false, "/pages/settings/index"),
            new OnboardingStep("report", "生成第一份研究报告", reports > 0, "/pages/home/index"),
        ]);
    }

    private static int ScalarCount(NpgsqlConnection conn, string sql, string userId)
    {
        using var cmd = conn.CreateCommand();
        cmd.CommandText = sql;
        cmd.Parameters.AddWithValue("uid", userId);
        return Convert.ToInt32(cmd.ExecuteScalar());
    }

    private static async Task<int> Count(NpgsqlConnection conn, NpgsqlTransaction transaction, string filteredSql, string userId, string? symbol, DateOnly? start, DateOnly? end, CancellationToken cancellation)
    {
        using var cmd = conn.CreateCommand();
        cmd.Transaction = transaction;
        cmd.CommandText = $"SELECT count(*) FROM ({filteredSql}) history";
        AddParams(cmd, userId, symbol, start, end);
        return Convert.ToInt32(await cmd.ExecuteScalarAsync(cancellation));
    }

    private static async Task<List<Dictionary<string, object?>>> ReadPage(NpgsqlConnection conn, NpgsqlTransaction transaction, string filteredSql, string timeColumn, bool ledger, string userId, string? symbol, DateOnly? start, DateOnly? end, int page, int limit, CancellationToken cancellation)
    {
        using var cmd = conn.CreateCommand();
        cmd.Transaction = transaction;
        var tieBreaker = ledger ? ", entry_type DESC" : "";
        cmd.CommandText = $"SELECT * FROM ({filteredSql}) history ORDER BY {timeColumn} DESC, id DESC{tieBreaker} LIMIT {limit} OFFSET {(page - 1) * limit}";
        AddParams(cmd, userId, symbol, start, end);

        var items = new List<Dictionary<string, object?>>();
        await using var reader = await cmd.ExecuteReaderAsync(cancellation);
        while (await reader.ReadAsync(cancellation))
        {
            var row = new Dictionary<string, object?>();
            for (var i = 0; i < reader.FieldCount; i++)
            {
                row[reader.GetName(i)] = Normalize(reader.IsDBNull(i) ? null : reader.GetValue(i));
            }

            items.Add(row);
        }

        return items;
    }

    /// <summary>内层 SQL 加 symbol/日期过滤，返回完整过滤 SQL（用 @uid/@sym/@start/@end 命名占位）。</summary>
    private static string BuildFiltered(string innerSql, string? symbol, DateOnly? start, DateOnly? end, string timeColumn)
    {
        var clauses = new List<string>();
        if (!string.IsNullOrWhiteSpace(symbol))
        {
            clauses.Add("strpos(upper(symbol), upper(@sym)) > 0");
        }

        if (start is not null)
        {
            clauses.Add($"{timeColumn} >= (CAST(@start AS timestamp) AT TIME ZONE INTERVAL '+08:00')");
        }

        if (end is not null)
        {
            // SQL date arithmetic also handles DateOnly.MaxValue without .NET overflow.
            clauses.Add($"{timeColumn} < (CAST(CAST(@end AS date) + 1 AS timestamp) AT TIME ZONE INTERVAL '+08:00')");
        }

        return $"SELECT * FROM ({innerSql}) filtered" + (clauses.Count > 0 ? " WHERE " + string.Join(" AND ", clauses) : "");
    }

    private static void AddParams(NpgsqlCommand cmd, string userId, string? symbol, DateOnly? start, DateOnly? end)
    {
        cmd.Parameters.AddWithValue("uid", userId);
        if (!string.IsNullOrWhiteSpace(symbol))
        {
            cmd.Parameters.AddWithValue("sym", symbol!.Trim());
        }

        if (start is not null)
        {
            cmd.Parameters.AddWithValue("start", start.Value);
        }

        if (end is not null)
        {
            cmd.Parameters.AddWithValue("end", end.Value);
        }
    }

    private static object? Normalize(object? value) => value switch
    {
        DateTime dt when dt.Kind == DateTimeKind.Unspecified => DateTime.SpecifyKind(dt, DateTimeKind.Utc),
        DateTime dt => dt.ToUniversalTime(),
        _ => value,
    };

    private static string CsvText(IReadOnlyList<Dictionary<string, object?>> items)
    {
        if (items.Count == 0)
        {
            return "";
        }

        var sb = new StringBuilder();
        var fields = items[0].Keys.ToList();
        sb.Append(string.Join(",", fields.Select(EscapeCsvCell))).Append("\r\n");
        foreach (var row in items)
        {
            sb.Append(string.Join(",", fields.Select(f => EscapeCsvCell(row.TryGetValue(f, out var v) ? v : null)))).Append("\r\n");
        }

        return sb.ToString();
    }

    private static string EscapeCsvCell(object? value)
    {
        if (value is null)
        {
            return "";
        }

        var s = value switch
        {
            DateOnly date => date.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture),
            DateTime time => time.ToString("O", CultureInfo.InvariantCulture),
            DateTimeOffset time => time.ToString("O", CultureInfo.InvariantCulture),
            IFormattable number => number.ToString(null, CultureInfo.InvariantCulture),
            _ => value.ToString() ?? "",
        };
        var trimmed = s.TrimStart();
        if (trimmed.Length > 0 && trimmed[0] is '=' or '+' or '-' or '@')
        {
            s = "'" + s;
        }

        return "\"" + s.Replace("\"", "\"\"") + "\"";
    }

    private NpgsqlConnection Open()
    {
        var conn = new NpgsqlConnection(connectionString);
        conn.Open();
        return conn;
    }
}
