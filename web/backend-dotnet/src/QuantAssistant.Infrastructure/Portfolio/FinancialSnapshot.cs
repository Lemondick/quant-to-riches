using System.Security.Cryptography;
using System.Text;
using System.Text.Json.Nodes;
using QuantAssistant.Infrastructure.Db;

namespace QuantAssistant.Infrastructure.Portfolio;

public static class FinancialSnapshot
{
    private static readonly (string Table,string Order)[] Tables=[("positions","symbol"),("instruments","symbol"),("transactions","id"),("dividend_events","id"),("corporate_action_events","id"),("ledger_corrections","id"),("portfolio_events","event_id"),("portfolio_valuations","as_of_date,run_id"),("audit_log","id")];
    public static JsonObject Read(AccountSql sql,string user)
    {
        var result=new JsonObject();foreach(var (table,order) in Tables){var rows=sql.Query($"SELECT * FROM {table} WHERE user_id=$1 ORDER BY {order}",user);foreach(var row in rows.OfType<JsonObject>())row.Remove("user_id");result[table]=rows;}return result;
    }
    public static string Hash(JsonNode value)=>Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(value.ToJsonString()))).ToLowerInvariant();
}
