using System.Text.Json.Nodes;
using Microsoft.AspNetCore.Mvc;
using QuantAssistant.Api.Services;
using QuantAssistant.Infrastructure.Db;
using QuantAssistant.Infrastructure.Portfolio;
using QuantAssistant.Infrastructure.Runtime;

namespace QuantAssistant.Api.Controllers;

[ApiController,Route("api/v1/advisor")]
public sealed class AdvisorController(AdvisorExecutionService service,NativeScheduler scheduler,AccountDatabase database,TradingCalendarRepository calendar):ControllerBase
{
    public sealed record CreateBody(string Kind,bool Force=false);
    private string Id=>User.FindFirst("sub")!.Value;
    [HttpGet("schedule")] public IActionResult Schedule()=>Ok(service.Schedule(Id,scheduler.Running));
    [HttpGet("reports")] public IActionResult Reports(string? kind,[FromQuery(Name="report_date")]DateOnly? date,int page=1,int limit=20)=>Ok(service.List(Id,kind,date,page,limit));
    [HttpGet("reports/{report_id}")] public IActionResult Report(string report_id)=>Ok(service.Get(Id,report_id));
    [HttpPost("reports")] public IActionResult Create(CreateBody body)=>Accepted(service.Create(Id,body.Kind,body.Force));
    [HttpPost("preflight")] public async Task<IActionResult> Preflight(CreateBody body, CancellationToken cancellation) => Ok(await service.Preflight(Id, body.Kind, cancellation));
    [HttpGet("notifications")] public IActionResult Notifications()=>Ok(new JsonObject { ["items"]=calendar.TradingDay(FundFeeRepository.Today())==true ? database.Read(sql=>sql.Query("SELECT id,kind,report_id,status,error_code,created_at,acknowledged FROM advisor_notifications WHERE user_id=$1 AND NOT acknowledged AND (created_at AT TIME ZONE 'Asia/Shanghai')::date=$2 ORDER BY created_at DESC LIMIT 10",Id,FundFeeRepository.Today())) : new JsonArray() });
    [HttpPost("notifications/{notice_id}/acknowledge")] public IActionResult Acknowledge(string notice_id)
    { var changed=database.WriteNonLedger(Id,sql=>sql.Execute("UPDATE advisor_notifications SET acknowledged=true WHERE user_id=$1 AND id=$2",Id,notice_id));return changed==0?NotFound(new{error_code="notice_not_found",message="未找到通知"}):Ok(new{status="acknowledged"}); }
}
