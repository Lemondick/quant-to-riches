using System.Text.Json.Nodes;
using Microsoft.AspNetCore.Mvc;
using QuantAssistant.Api.Contracts;
using QuantAssistant.Api.Middleware;
using QuantAssistant.Api.Services;
using QuantAssistant.Core.Auth;
using QuantAssistant.Core.Errors;

namespace QuantAssistant.Api.Controllers;

/// <summary>
/// batch2 任务6：组合回测后端编排。
/// POST api/v1/backtest/portfolio：输入校验（权重和=100%、日期合法、再平衡/dca 参数合法）
/// 后透调 worker op portfolio.backtest（契约§2）；校验失败走现有 invalid_request 错误契约（422）。
/// </summary>
[ApiController]
[Route("api/v1/backtest/portfolio")]
public sealed class PortfolioBacktestController(ComputeService compute) : ControllerBase
{
    public sealed record FundWeight(string Code, decimal Weight);
    public sealed record RebalanceBody(string Mode, string? Period = null, double? ThresholdPct = null);
    public sealed record DcaBody(decimal Amount, string Freq);
    public sealed record BacktestBody(List<FundWeight> Funds, DateOnly Start, DateOnly End,
        RebalanceBody? Rebalance = null, DcaBody? Dca = null);

    private string Id => User.FindFirst("sub")?.Value ?? throw new AuthFailure("authentication_required");

    [HttpPost]
    public async Task<IActionResult> Run([FromBody] BacktestBody body, CancellationToken cancellation = default)
    {
        try
        {
            Validate(body);
        }
        catch (ArgumentException error)
        {
            return UnprocessableEntity(new ErrorResponse(ErrorCodes.InvalidRequest, error.Message, HttpContext.GetRequestId()));
        }
        var payload = new JsonObject
        {
            ["funds"] = new JsonArray(body.Funds.Select(f => (JsonNode)new JsonObject
            {
                ["code"] = f.Code.Trim(), ["weight"] = (double)f.Weight,
            }).ToArray()),
            ["start"] = body.Start.ToString("yyyy-MM-dd"), ["end"] = body.End.ToString("yyyy-MM-dd"),
        };
        if (body.Rebalance is not null)
        {
            var rebalance = new JsonObject { ["mode"] = body.Rebalance.Mode };
            if (body.Rebalance.Period is not null) rebalance["period"] = body.Rebalance.Period;
            if (body.Rebalance.ThresholdPct is not null) rebalance["threshold_pct"] = body.Rebalance.ThresholdPct.Value;
            payload["rebalance"] = rebalance;
        }
        if (body.Dca is not null)
            payload["dca"] = new JsonObject { ["amount"] = (double)body.Dca.Amount, ["freq"] = body.Dca.Freq };
        var result = await compute.Run(Id, "portfolio.backtest", payload, cancellation, seconds: 600);
        return Ok(result ?? throw new ApplicationFailure("worker_unavailable", 503, "回测计算未完成，请稍后重试。"));
    }

    private static void Validate(BacktestBody body)
    {
        if (body.Funds is null || body.Funds.Count is 0 or > 50)
            throw new ArgumentException("funds 须包含 1 到 50 只基金。");
        decimal sum = 0;
        foreach (var fund in body.Funds)
        {
            if (string.IsNullOrWhiteSpace(fund.Code) || fund.Code.Trim().Length > 32)
                throw new ArgumentException("基金代码无效。");
            if (fund.Weight is <= 0 or > 100)
                throw new ArgumentException("单只基金权重须在 0 到 100 之间。");
            sum += fund.Weight;
        }
        if (Math.Abs(sum - 100m) > 0.001m)
            throw new ArgumentException($"基金权重之和必须为 100%（当前 {sum:0.###}%）。");
        var today = DateOnly.FromDateTime(DateTime.UtcNow.AddHours(8));
        if (body.Start >= body.End || body.Start < new DateOnly(1990, 1, 1) || body.End > today)
            throw new ArgumentException("回测起止日期无效（须 start<end 且不晚于今日）。");
        if (body.Rebalance is not null)
        {
            if (body.Rebalance.Mode == "period")
            {
                if (body.Rebalance.Period is not ("1M" or "3M"))
                    throw new ArgumentException("再平衡 period 须为 1M 或 3M。");
            }
            else if (body.Rebalance.Mode == "threshold")
            {
                if (body.Rebalance.ThresholdPct is not > 0 and <= 50)
                    throw new ArgumentException("再平衡 threshold_pct 须在 0 到 50 之间。");
            }
            else throw new ArgumentException("再平衡 mode 须为 period 或 threshold。");
        }
        if (body.Dca is not null)
        {
            if (body.Dca.Amount is <= 0 or > 1_000_000_000m)
                throw new ArgumentException("定投金额须大于 0。");
            if (body.Dca.Freq is not ("weekly" or "monthly"))
                throw new ArgumentException("定投频率须为 weekly 或 monthly。");
        }
    }
}
