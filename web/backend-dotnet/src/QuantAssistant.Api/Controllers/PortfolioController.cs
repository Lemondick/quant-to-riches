using Microsoft.AspNetCore.Mvc;
using QuantAssistant.Core.Abstractions;
using System.Globalization;
using System.Text.Json;
using QuantAssistant.Api.Services;
using QuantAssistant.Core.Auth;
using QuantAssistant.Infrastructure.Db;

namespace QuantAssistant.Api.Controllers;

/// <summary>组合查询接口（对应旧站点 routes.py 的 /portfolio）。</summary>
[ApiController]
[Route("api/v1/portfolio")]
public sealed class PortfolioController(ILedgerRepository ledger, AccountDisplayCache cache) : ControllerBase
{
    [HttpGet]
    public async Task<IActionResult> Portfolio(CancellationToken cancellation)
    {
        var userId = CurrentUserId();
        Response.Headers.CacheControl = "no-store";
        return Ok(await cache.Read(userId, "portfolio", (HttpContext.Items[typeof(AuthSession)] as AuthSession)?.DataVersion, async token =>
        {
        var positions = await ledger.ListPositionsAsync(userId, token);
        var cashPositions = positions.Where(p => p.AssetType == Core.Portfolio.AssetType.Cash).ToArray();
        var total = positions.Sum(p => p.MarketValue);

        return JsonSerializer.SerializeToNode(new
        {
            as_of_date = positions.Count == 0 ? (DateOnly?)null : positions.Max(p => p.AsOfDate),
            positions = positions.Select(p => new
            {
                symbol = p.Symbol,
                asset_type = ToDbAssetType(p.AssetType),
                name = p.Name,
                quantity = p.Quantity.ToString(CultureInfo.InvariantCulture),
                average_cost = p.AverageCost.ToString(CultureInfo.InvariantCulture),
                market_value = p.MarketValue.ToString(CultureInfo.InvariantCulture),
                as_of_date = p.AsOfDate,
            }),
            bucket_weights = positions.Where(p => p.MarketValue > 0).GroupBy(p => ToDbAssetType(p.AssetType))
                .OrderBy(g => g.Key).Select(g => new { name = g.Key, weight = (g.Sum(p => p.MarketValue) / total).ToString(CultureInfo.InvariantCulture) }),
            cash = cashPositions.Sum(p => p.MarketValue),
            cash_symbol = cashPositions.Length == 1 ? cashPositions[0].Symbol : null,
            total_value = total,
        }, FinanceJson.Options)!.AsObject();
        }, cancellation));
    }

    private static string ToDbAssetType(Core.Portfolio.AssetType t) => t switch
    {
        Core.Portfolio.AssetType.Stock => "stock",
        Core.Portfolio.AssetType.Fund => "fund",
        Core.Portfolio.AssetType.Index => "index",
        Core.Portfolio.AssetType.Cash => "cash",
        _ => "stock",
    };

    private string CurrentUserId() => User.FindFirst("sub")?.Value ?? User.FindFirst("user_id")?.Value ?? throw new QuantAssistant.Core.Auth.AuthFailure("authentication_required");
}
