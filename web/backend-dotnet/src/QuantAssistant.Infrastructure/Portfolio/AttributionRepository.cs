using Npgsql;
using QuantAssistant.Core.Portfolio;
using QuantAssistant.Infrastructure.Db;
using System.Text.Json.Nodes;

namespace QuantAssistant.Infrastructure.Portfolio;

public class AttributionRepository(string connectionString) : IAttributionRepository
{
    public void CreateReport(AttributionReport report, IReadOnlyCollection<AttributionDetail>? details = null)
    {
        using var conn = new NpgsqlConnection(connectionString);
        conn.Open();
        using var transaction = conn.BeginTransaction();
        InsertReport(new AccountSql(conn, transaction), report, details);
        transaction.Commit();
    }

    public static void InsertReport(AccountSql sql, AttributionReport report, IReadOnlyCollection<AttributionDetail>? details)
    {
        sql.Execute("""
            INSERT INTO attribution_reports(id,user_id,portfolio_symbol,benchmark_symbol,start_date,end_date,status,total_active_return,allocation_effect,selection_effect,interaction_effect,data_source,source_json)
            VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13::jsonb)
            """, report.Id, report.UserId, report.PortfolioSymbol, report.BenchmarkSymbol, report.StartDate, report.EndDate, report.Status,
            report.TotalActiveReturn, report.AllocationEffect, report.SelectionEffect, report.InteractionEffect, report.DataSource, report.SourceJson.ToJsonString());
        if (details is null) return;
        foreach (var d in details)
            sql.Execute("INSERT INTO attribution_details(report_id,sector_name,portfolio_weight,benchmark_weight,portfolio_return,benchmark_return,allocation,selection,interaction) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9)",
                report.Id, d.SectorName, d.PortfolioWeight, d.BenchmarkWeight, d.PortfolioReturn, d.BenchmarkReturn, d.Allocation, d.Selection, d.Interaction);
    }

    public bool CompleteReport(string userId, AttributionReport report, IReadOnlyCollection<AttributionDetail> details)
    {
        using var conn = new NpgsqlConnection(connectionString);
        conn.Open();
        using var transaction = conn.BeginTransaction();
        using var owned = new NpgsqlCommand("SELECT 1 FROM attribution_reports WHERE user_id=$1 AND id=$2 FOR UPDATE", conn, transaction);
        owned.Parameters.AddWithValue(userId);
        owned.Parameters.AddWithValue(report.Id);
        if (owned.ExecuteScalar() is null) return false;
        using var delete = new NpgsqlCommand("DELETE FROM attribution_details WHERE report_id=$1", conn, transaction);
        delete.Parameters.AddWithValue(report.Id);
        delete.ExecuteNonQuery();
        InsertDetails(conn, transaction, details);
        using var update = new NpgsqlCommand("UPDATE attribution_reports SET status='completed',data_source='manual_sector_input',source_json='{}'::jsonb,total_active_return=$1,allocation_effect=$2,selection_effect=$3,interaction_effect=$4,updated_at=now() WHERE user_id=$5 AND id=$6", conn, transaction);
        update.Parameters.AddWithValue(report.TotalActiveReturn!.Value);
        update.Parameters.AddWithValue(report.AllocationEffect!.Value);
        update.Parameters.AddWithValue(report.SelectionEffect!.Value);
        update.Parameters.AddWithValue(report.InteractionEffect!.Value);
        update.Parameters.AddWithValue(userId);
        update.Parameters.AddWithValue(report.Id);
        update.ExecuteNonQuery();
        transaction.Commit();
        return true;
    }

    private static void InsertDetails(NpgsqlConnection conn, NpgsqlTransaction transaction, IEnumerable<AttributionDetail> details)
    {
        using var batch = new NpgsqlBatch(conn, transaction);
        foreach (var detail in details)
        {
            var command = new NpgsqlBatchCommand("INSERT INTO attribution_details(report_id,sector_name,portfolio_weight,benchmark_weight,portfolio_return,benchmark_return,allocation,selection,interaction) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9)");
            foreach (var value in new object[] { detail.ReportId, detail.SectorName, detail.PortfolioWeight, detail.BenchmarkWeight, detail.PortfolioReturn, detail.BenchmarkReturn, detail.Allocation, detail.Selection, detail.Interaction })
                command.Parameters.AddWithValue(value);
            batch.BatchCommands.Add(command);
        }
        if (batch.BatchCommands.Count > 0) batch.ExecuteNonQuery();
    }

    public void UpsertDetails(IEnumerable<AttributionDetail> details)
    {
        using var conn = new NpgsqlConnection(connectionString);
        conn.Open();
        using var batch = conn.CreateBatch();
        foreach (var d in details)
        {
            var cmd = batch.CreateBatchCommand();
            cmd.CommandText = """
                INSERT INTO attribution_details (report_id, sector_name, portfolio_weight, benchmark_weight, portfolio_return, benchmark_return, allocation, selection, interaction)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                ON CONFLICT (report_id, sector_name) DO UPDATE SET 
                    portfolio_weight = EXCLUDED.portfolio_weight,
                    benchmark_weight = EXCLUDED.benchmark_weight,
                    portfolio_return = EXCLUDED.portfolio_return,
                    benchmark_return = EXCLUDED.benchmark_return,
                    allocation = EXCLUDED.allocation,
                    selection = EXCLUDED.selection,
                    interaction = EXCLUDED.interaction
                """;
            cmd.Parameters.AddWithValue(d.ReportId);
            cmd.Parameters.AddWithValue(d.SectorName);
            cmd.Parameters.AddWithValue(d.PortfolioWeight);
            cmd.Parameters.AddWithValue(d.BenchmarkWeight);
            cmd.Parameters.AddWithValue(d.PortfolioReturn);
            cmd.Parameters.AddWithValue(d.BenchmarkReturn);
            cmd.Parameters.AddWithValue(d.Allocation);
            cmd.Parameters.AddWithValue(d.Selection);
            cmd.Parameters.AddWithValue(d.Interaction);
            batch.BatchCommands.Add(cmd);
        }
        if (batch.BatchCommands.Count > 0)
        {
            batch.ExecuteNonQuery();
        }
    }

    public void UpdateReportStatus(string reportId, string status, decimal activeReturn, decimal allocation, decimal selection, decimal interaction)
    {
        using var conn = new NpgsqlConnection(connectionString);
        conn.Open();
        using var cmd = conn.CreateCommand();
        cmd.CommandText = """
            UPDATE attribution_reports SET 
                status = $1, 
                total_active_return = $2, 
                allocation_effect = $3, 
                selection_effect = $4, 
                interaction_effect = $5,
                updated_at = NOW() 
            WHERE id = $6
            """;
        cmd.Parameters.AddWithValue(status);
        cmd.Parameters.AddWithValue(activeReturn);
        cmd.Parameters.AddWithValue(allocation);
        cmd.Parameters.AddWithValue(selection);
        cmd.Parameters.AddWithValue(interaction);
        cmd.Parameters.AddWithValue(reportId);
        cmd.ExecuteNonQuery();
    }

    public AttributionReport? GetReport(string userId, string reportId)
    {
        using var conn = new NpgsqlConnection(connectionString);
        conn.Open();
        return ReadReport(conn, null, userId, reportId);
    }

    public AttributionReportView? GetReportView(string userId, string reportId)
    {
        using var conn = new NpgsqlConnection(connectionString);
        conn.Open();
        using var transaction = conn.BeginTransaction(System.Data.IsolationLevel.RepeatableRead);
        var report = ReadReport(conn, transaction, userId, reportId);
        if (report is null) return null;
        var details = ReadDetails(conn, transaction, reportId);
        transaction.Commit();
        return new(report, details);
    }

    private static AttributionReport? ReadReport(NpgsqlConnection conn, NpgsqlTransaction? transaction, string userId, string reportId)
    {
        using var cmd = conn.CreateCommand();
        cmd.Transaction = transaction;
        cmd.CommandText = "SELECT id, user_id, portfolio_symbol, benchmark_symbol, start_date, end_date, total_active_return, allocation_effect, selection_effect, interaction_effect, status,data_source,source_json FROM attribution_reports WHERE user_id = $1 AND id = $2";
        cmd.Parameters.AddWithValue(userId);
        cmd.Parameters.AddWithValue(reportId);

        using var reader = cmd.ExecuteReader();
        if (reader.Read())
        {
            return new AttributionReport
            {
                Id = reader.GetString(0),
                UserId = reader.GetString(1),
                PortfolioSymbol = reader.GetString(2),
                BenchmarkSymbol = reader.GetString(3),
                StartDate = reader.GetFieldValue<DateOnly>(4),
                EndDate = reader.GetFieldValue<DateOnly>(5),
                TotalActiveReturn = reader.IsDBNull(6) ? null : reader.GetDecimal(6),
                AllocationEffect = reader.IsDBNull(7) ? null : reader.GetDecimal(7),
                SelectionEffect = reader.IsDBNull(8) ? null : reader.GetDecimal(8),
                InteractionEffect = reader.IsDBNull(9) ? null : reader.GetDecimal(9),
                Status = reader.GetString(10),
                DataSource = reader.GetString(11),
                SourceJson = JsonNode.Parse(reader.GetString(12))!.AsObject()
            };
        }
        return null;
    }

    public List<AttributionDetail> GetDetails(string reportId)
    {
        using var conn = new NpgsqlConnection(connectionString);
        conn.Open();
        return ReadDetails(conn, null, reportId);
    }

    private static List<AttributionDetail> ReadDetails(NpgsqlConnection conn, NpgsqlTransaction? transaction, string reportId)
    {
        using var cmd = conn.CreateCommand();
        cmd.Transaction = transaction;
        cmd.CommandText = "SELECT report_id, sector_name, portfolio_weight, benchmark_weight, portfolio_return, benchmark_return, allocation, selection, interaction FROM attribution_details WHERE report_id = $1 ORDER BY sector_name";
        cmd.Parameters.AddWithValue(reportId);

        var list = new List<AttributionDetail>();
        using var reader = cmd.ExecuteReader();
        while (reader.Read())
        {
            list.Add(new AttributionDetail
            {
                ReportId = reader.GetString(0),
                SectorName = reader.GetString(1),
                PortfolioWeight = reader.GetDecimal(2),
                BenchmarkWeight = reader.GetDecimal(3),
                PortfolioReturn = reader.GetDecimal(4),
                BenchmarkReturn = reader.GetDecimal(5),
                Allocation = reader.GetDecimal(6),
                Selection = reader.GetDecimal(7),
                Interaction = reader.GetDecimal(8)
            });
        }
        return list;
    }
}
