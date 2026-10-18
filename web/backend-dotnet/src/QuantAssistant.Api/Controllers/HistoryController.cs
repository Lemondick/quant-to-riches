using Microsoft.AspNetCore.Mvc;
using QuantAssistant.Core.History;
using QuantAssistant.Api.Services;

namespace QuantAssistant.Api.Controllers;

/// <summary>历史分页导出 + 引导接口（对应旧站点 history_routes.py）。</summary>
[ApiController]
[Route("api/v1")]
public sealed class HistoryController(IHistoryRepository repository,ModelConnectionService models) : ControllerBase
{
    [HttpGet("history/{section}/export")]
    public async Task<IActionResult> Export(string section, [FromQuery] string? symbol = null, [FromQuery] DateOnly? start = null, [FromQuery] DateOnly? end = null, CancellationToken cancellation = default)
    {
        try
        {
            var result = await repository.QueryAsync(CurrentUserId(), section, 1, 500, symbol, start, end, export: true, cancellation);
            return Ok(new { items = result.Items, total = result.Total, csv = result.Csv });
        }
        catch (UnknownHistorySectionError)
        {
            return NotFound(new { error_code = "unknown_history_section", message = "未知历史分区" });
        }
        catch (HistoryExportTooLargeError)
        {
            return StatusCode(StatusCodes.Status413PayloadTooLarge, new { error_code = "history_export_too_large", message = "单次导出最多10万条，请缩小日期范围" });
        }
        catch (ArgumentException error)
        {
            return StatusCode(StatusCodes.Status422UnprocessableEntity, new { error_code = "invalid_request", message = error.Message });
        }
    }

    [HttpGet("history/{section}")]
    public async Task<IActionResult> Records(
        string section,
        [FromQuery] int page = 1,
        [FromQuery] int limit = 50,
        [FromQuery] string? symbol = null,
        [FromQuery] DateOnly? start = null,
        [FromQuery] DateOnly? end = null,
        [FromQuery] bool export = false, CancellationToken cancellation = default)
    {
        if (page < 1 || page > 100_000 || limit < 1 || limit > 500)
        {
            return StatusCode(StatusCodes.Status422UnprocessableEntity, new { error_code = "invalid_request", message = "分页参数越界" });
        }

        try
        {
            var result = await repository.QueryAsync(CurrentUserId(), section, page, limit, symbol, start, end, export, cancellation);
            return Ok(result);
        }
        catch (UnknownHistorySectionError)
        {
            return NotFound(new { error_code = "unknown_history_section", message = "未知历史分区" });
        }
        catch (HistoryExportTooLargeError)
        {
            return StatusCode(StatusCodes.Status413PayloadTooLarge, new { error_code = "history_export_too_large", message = "单次导出最多10万条，请缩小日期范围" });
        }
        catch (ArgumentException error)
        {
            return StatusCode(StatusCodes.Status422UnprocessableEntity, new { error_code = "invalid_request", message = error.Message });
        }
    }

    [HttpGet("onboarding")]
    public IActionResult Onboarding()
    {
        var id=CurrentUserId();var state=repository.Onboarding(id);var configured=models.Read(id)["configured"]!.GetValue<bool>();
        return Ok(state with {Steps=state.Steps.Select(step=>step.Key=="models"?step with {Done=configured}:step).ToArray()});
    }

    private string CurrentUserId()
    {
        // P5 骨架阶段：用户身份由认证中间件注入；此处从 Claims 读取，未登录场景返回占位（后续接入认证）。
        var claim = User.FindFirst("sub") ?? User.FindFirst("user_id");
        return claim?.Value ?? throw new QuantAssistant.Core.Auth.AuthFailure("authentication_required");
    }
}
