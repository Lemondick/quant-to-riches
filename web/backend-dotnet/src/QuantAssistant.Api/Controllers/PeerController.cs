using Microsoft.AspNetCore.Mvc;
using QuantAssistant.Core.Portfolio;

namespace QuantAssistant.Api.Controllers;

[ApiController]
public class PeerController(IPeerRepository peerRepo) : ControllerBase
{
    [HttpGet("~/api/v1/peers/groups/{group_id}/rankings")]
    public IActionResult GetGroupRankings(string group_id, [FromQuery] string date)
    {
        if (!DateOnly.TryParse(date, out var dataDate))
            return BadRequest(new { error_code = "invalid_date", message = "Date 格式错误" });

        var rankings = peerRepo.GetRankingsByGroup(group_id, dataDate);
        return Ok(new { data_date = dataDate.ToString("yyyy-MM-dd"), group_id = group_id, results = rankings });
    }

    [HttpGet("~/api/v1/peers/funds/{symbol}/ranking")]
    public IActionResult GetFundRanking(string symbol, [FromQuery] string group_id, [FromQuery] string date)
    {
        if (!DateOnly.TryParse(date, out var dataDate))
            return BadRequest(new { error_code = "invalid_date", message = "Date 格式错误" });

        var ranking = peerRepo.GetFundRanking(symbol, group_id, dataDate);
        if (ranking is null)
            return NotFound(new { error_code = "ranking_not_found", message = "无该基金同业排名数据" });

        return Ok(ranking);
    }
}
