using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using Microsoft.AspNetCore.Mvc;
using QuantAssistant.Core.Auth;
using QuantAssistant.Infrastructure.Jobs;
using QuantAssistant.Api.Services;
using QuantAssistant.Infrastructure.Runtime;
using QuantAssistant.Core.Errors;
using System.Text.Json.Nodes;

namespace QuantAssistant.Api.Controllers;

[ApiController]
[Route("internal/v1/worker")]
public sealed class WorkerController(ComputeJobRepository jobs, IConfiguration configuration, ModelConnectionService connections, ModelCostRepository costs, PublicSnapshotRepository publicData, TradingCalendarRepository calendars) : ControllerBase
{
    public sealed record ClaimBody(string WorkerId, string[] Capabilities);
    public sealed record LeaseBody(string UserId, string JobId, int Attempt, string OwnerToken);
    public sealed record FinishBody(string UserId, string JobId, int Attempt, string OwnerToken, JsonElement Result, string? ErrorCode, JsonObject? Timings = null);
    public sealed record ReserveBody(string UserId,string JobId,int Attempt,string OwnerToken,string Model,int? MaxInputTokens=null,int? MaxOutputTokens=null,string Role="unknown",string Provider="unknown");
    public sealed record CostFinishBody(string UserId,string JobId,int Attempt,string OwnerToken,string CallId,long? InputTokens=null,long? OutputTokens=null,string Stage="response",string? UnknownReason=null);

    private void AuthenticateWorker()
    {
        var expected = configuration["QUANT_WORKER_SECRET"];
        if (string.IsNullOrEmpty(expected) || expected.Length < 32) throw new AuthFailure("authentication_required");
        var supplied = Request.Headers["X-Quant-Worker-Secret"].ToString();
        if (!CryptographicOperations.FixedTimeEquals(SHA256.HashData(Encoding.UTF8.GetBytes(expected)), SHA256.HashData(Encoding.UTF8.GetBytes(supplied))))
            throw new AuthFailure("authentication_required");
    }

    [HttpPost("claim")]
    public async Task<IActionResult> Claim([FromBody] ClaimBody body, CancellationToken cancellation)
    {
        AuthenticateWorker();
        using var signal = ComputeJobNotifications.Local.SubscribeQueue();
        var job = await jobs.ClaimAsync(body.WorkerId, body.Capabilities, cancellation);
        if (job is null)
        {
            await signal.WaitAsync(TimeSpan.FromSeconds(5), cancellation);
            job = await jobs.ClaimAsync(body.WorkerId, body.Capabilities, cancellation);
        }
        var input = job is null ? null : JsonNode.Parse(job.InputJson)!.AsObject();
        if (job is not null && ComputeJobRepository.IndependentPublicKinds.Contains(job.Kind))
            input!["_calendars"] = (await calendars.SnapshotAsync(DateTime.UtcNow.AddHours(8).Year, cancellation)).ToJson();
        if (job?.Kind == "fund.valuations")
        {
            var symbols = input!["symbols"]!.AsArray().Select(value => value!.GetValue<string>()).ToArray();
            input["local_nav"] = await publicData.OfficialNavAsync(symbols, cancellation);
        }
        if(job is not null && job.Kind is "advisor.report" or "agent.research")
        {
            try { input!["credentials"]=connections.WorkerCredentials(job.UserId); }
            catch(ApplicationFailure)
            {
                await jobs.FinishAsync(job.UserId,job.JobId,job.Attempt,job.OwnerToken,null,"model_configuration",cancellation);
                return NoContent();
            }
        }
        return job is null ? NoContent() : Ok(new { version = 1, user_id = job.UserId, job_id = job.JobId, kind = job.Kind,
            input, attempt = job.Attempt, owner_token = job.OwnerToken, deadline = job.Deadline, trace_id = job.TraceId });
    }

    [HttpPost("heartbeat")]
    public async Task<IActionResult> Heartbeat([FromBody] LeaseBody body, CancellationToken cancellation)
    {
        AuthenticateWorker();
        return await jobs.HeartbeatAsync(body.UserId, body.JobId, body.Attempt, body.OwnerToken, cancellation) ? Ok(new { active = true }) : Conflict(new { active = false });
    }

    [HttpPost("finish")]
    public async Task<IActionResult> Finish([FromBody] FinishBody body, CancellationToken cancellation)
    {
        AuthenticateWorker();
        if (body.Result.ValueKind == JsonValueKind.Undefined || body.ErrorCode is not null && body.Result.ValueKind != JsonValueKind.Null) throw new ArgumentException("计算结果无效");
        if (body.Timings is not null && (body.Timings.Count > 8 || body.Timings.Any(pair => pair.Key is not ("worker" or "startup" or "provider" or "external_io") || pair.Value is not JsonValue value || !value.TryGetValue<double>(out var duration) || !double.IsFinite(duration) || duration < 0 || duration > 86_400_000))) throw new ArgumentException("计算耗时无效");
        return await jobs.FinishAsync(body.UserId, body.JobId, body.Attempt, body.OwnerToken, body.ErrorCode is null ? body.Result.GetRawText() : null, body.ErrorCode, cancellation, body.Timings)
            ? Ok(new { accepted = true }) : Conflict(new { accepted = false });
    }

    [HttpPost("cost-reserve")]
    public IActionResult Reserve(ReserveBody body)
    {
        AuthenticateWorker();
        return Ok(new { call_id=costs.ReserveOwned(body.UserId,body.JobId,body.Attempt,body.OwnerToken,body.Model,body.MaxInputTokens,body.MaxOutputTokens,body.Role,body.Provider) });
    }

    [HttpPost("cost-finish")]
    public IActionResult FinishCost(CostFinishBody body)
    {
        AuthenticateWorker();costs.FinishOwned(body.UserId,body.JobId,body.Attempt,body.OwnerToken,body.CallId,body.InputTokens,body.OutputTokens,body.Stage,body.UnknownReason);
        return Ok(new { accepted=true });
    }
}
