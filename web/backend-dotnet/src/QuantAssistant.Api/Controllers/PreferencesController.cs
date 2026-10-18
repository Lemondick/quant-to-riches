using System.Text.Json.Nodes;
using Microsoft.AspNetCore.Mvc;
using QuantAssistant.Core.Auth;
using QuantAssistant.Core.Portfolio;
using QuantAssistant.Infrastructure.Documents;

namespace QuantAssistant.Api.Controllers;

[ApiController]
[Route("api/v1/preferences")]
public sealed class PreferencesController(UserDocumentRepository documents) : ControllerBase
{
    public sealed record WatchlistItem(string Symbol, string Name, string AssetType);
    public sealed record WatchlistBody(WatchlistItem[] Items);
    public sealed record NewsBody(string[] Ids);
    private string UserId => User.FindFirst("sub")?.Value ?? throw new AuthFailure("authentication_required");
    private static JsonObject Defaults() => new() { ["watchlist"] = new JsonArray(), ["read_news_ids"] = new JsonArray(), ["revision"] = 0L, ["updated_at"] = null };

    [HttpGet]
    public async Task<IActionResult> Get(CancellationToken cancellation) => Ok(await documents.ReadAsync(UserId, "preferences", Defaults, cancellation));

    [HttpPut("watchlist")]
    public async Task<IActionResult> Watchlist([FromBody] WatchlistBody body, CancellationToken cancellation)
    {
        if (body.Items.Length > 100) throw new ArgumentException("自选最多保存100项");
        var entries = new JsonArray();
        var symbols = new HashSet<string>(StringComparer.Ordinal);
        foreach (var item in body.Items)
        {
            var type = item.AssetType switch { "stock" => AssetType.Stock, "fund" => AssetType.Fund, "index" => AssetType.Index, _ => throw new ArgumentException("自选资产类型无效") };
            var symbol = item.Symbol.Trim();
            if (symbol.Length!=6 || symbol.Any(ch=>!char.IsAsciiDigit(ch)) || !symbols.Add(symbol) || string.IsNullOrWhiteSpace(item.Name) || item.Name.Length > 80) throw new ArgumentException("自选代码重复或名称无效");
            entries.Add(new JsonObject { ["symbol"] = symbol, ["name"] = item.Name.Trim(), ["asset_type"] = item.AssetType });
        }
        return Ok(await documents.UpdateAsync(UserId, "preferences", Defaults, current => { current["watchlist"] = entries; return current; }, cancellation));
    }

    [HttpPost("news/read")]
    public async Task<IActionResult> NewsRead([FromBody] NewsBody body, CancellationToken cancellation)
    {
        if (body.Ids.Length > 2000 || body.Ids.Any(id => string.IsNullOrWhiteSpace(id) || id.Length > 256)) throw new ArgumentException("新闻编号无效");
        return Ok(await documents.UpdateAsync(UserId, "preferences", Defaults, current =>
        {
            var ids = current["read_news_ids"]!.AsArray().Select(id => id!.GetValue<string>()).Concat(body.Ids.Select(id => id.Trim())).Distinct().TakeLast(2000);
            current["read_news_ids"] = new JsonArray(ids.Select(id => (JsonNode?)JsonValue.Create(id)).ToArray());
            return current;
        }, cancellation));
    }
}
