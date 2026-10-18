using Microsoft.AspNetCore.Mvc;
using QuantAssistant.Core.Portfolio;

namespace QuantAssistant.Api.Controllers;

[ApiController]
public class DcaController(IDcaRepository dcaRepo) : ControllerBase
{
    private string CurrentUserId()
    {
        return User.FindFirst("sub")?.Value ?? throw new QuantAssistant.Core.Auth.AuthFailure("authentication_required");
    }

    [HttpPost("~/api/v1/dca/plans")]
    public IActionResult CreatePlan([FromBody] CreateDcaPlanBody body)
    {
        var plan = new DcaPlan
        {
            Id = Guid.NewGuid().ToString("N"),
            UserId = CurrentUserId(),
            Symbol = body.Symbol,
            BenchmarkSymbol = body.BenchmarkSymbol,
            BaseAmount = body.BaseAmount,
            MaDays = body.MaDays,
            MinMultiplier = body.MinMultiplier,
            MaxMultiplier = body.MaxMultiplier,
            Status = "active"
        };
        
        dcaRepo.CreatePlan(plan);
        return Created($"/api/v1/dca/plans/{plan.Id}", plan);
    }

    [HttpGet("~/api/v1/dca/plans")]
    public IActionResult ListPlans()
    {
        var plans = dcaRepo.ListActivePlans(CurrentUserId());
        return Ok(plans);
    }
    
    [HttpPost("~/api/v1/dca/plans/{plan_id}/calculate")]
    public IActionResult CalculateCurrentAmount(string plan_id, [FromBody] CalculateDcaBody body)
    {
        // 实际场景应由后端自己拉取最新 MA 价格
        // 这里提供计算接口供前端展示"预览定投金额"
        var plans = dcaRepo.ListActivePlans(CurrentUserId());
        var plan = plans.FirstOrDefault(p => p.Id == plan_id);
        if (plan is null)
            return NotFound(new { error_code = "plan_not_found", message = "计划不存在或已结束" });
            
        var multiplier = DcaRules.CalculateMultiplier(
            body.CurrentPrice, 
            body.MovingAveragePrice, 
            plan.MinMultiplier, 
            plan.MaxMultiplier, 
            body.DeviationThreshold);
            
        var finalAmount = Math.Round(plan.BaseAmount * multiplier, 2);
        
        return Ok(new 
        { 
            plan_id = plan.Id,
            base_amount = plan.BaseAmount,
            multiplier = Math.Round(multiplier, 4),
            final_amount = finalAmount,
            current_price = body.CurrentPrice,
            ma_price = body.MovingAveragePrice
        });
    }
}

public class CreateDcaPlanBody
{
    public string Symbol { get; set; } = "";
    public string BenchmarkSymbol { get; set; } = "";
    public decimal BaseAmount { get; set; }
    public int MaDays { get; set; } = 250;
    public decimal MinMultiplier { get; set; } = 0.5m;
    public decimal MaxMultiplier { get; set; } = 2.0m;
}

public class CalculateDcaBody
{
    public decimal CurrentPrice { get; set; }
    public decimal MovingAveragePrice { get; set; }
    public decimal DeviationThreshold { get; set; } = 0.20m;
}
