using System.Text.Json.Nodes;
using Microsoft.AspNetCore.Mvc;
using QuantAssistant.Api.Services;
using QuantAssistant.Core.Abstractions;
using QuantAssistant.Core.Auth;
using QuantAssistant.Infrastructure.Runtime;

namespace QuantAssistant.Api.Controllers;

[ApiController, Route("api/v1/operations")]
public sealed class OperationalReadinessController(ComputeService compute, NativeBackupEvidence backups, IConfiguration configuration, IAccountDatabase database, NativeOperationsMonitor monitor) : ControllerBase
{
    [HttpGet("readiness")]
    public async Task<IActionResult> Readiness(CancellationToken cancellation)
    {
        var user = User.FindFirst("sub")?.Value ?? throw new AuthFailure("authentication_required");
        var result = (await compute.Run(user, "analysis.readiness", new { }, cancellation, accountSnapshot: true))!.AsObject();
        var checks = result["checks"]!.AsObject(); var archives = backups.Read(); var recent = archives.OfType<JsonObject>().Any(a => a["verified"]!.GetValue<bool>() && a["restore_verified"]!.GetValue<bool>());
        checks["operations_backup_restore"] = new JsonObject { ["passed"] = recent, ["status"] = recent ? "passed" : "blocked", ["blockers"] = recent ? new JsonArray() : new JsonArray("native_backup_restore_evidence_missing"), ["site_backups"] = archives };
        checks["broker_safety_boundary"] = new JsonObject { ["passed"] = true, ["status"] = "passed", ["automatic_trading"] = false, ["blockers"] = new JsonArray() };
        var domain = configuration["QUANT_DOMAIN"]; var endpoint = domain?.Contains("://") == true ? domain : "https://" + domain;
        var secure = Request.IsHttps && !string.IsNullOrWhiteSpace(domain) && Uri.TryCreate(endpoint, UriKind.Absolute, out var uri) && uri.Scheme == "https" && !uri.IsLoopback
            && string.Equals(Request.Host.Host, uri.Host, StringComparison.OrdinalIgnoreCase) && configuration["QUANT_WORKER_SECRET"] is { Length: >= 32 };
        checks["deployment_security"] = new JsonObject { ["passed"] = secure, ["status"] = secure ? "passed" : "blocked", ["https_request_observed"] = Request.IsHttps, ["blockers"] = secure ? new JsonArray() : new JsonArray("https_or_worker_configuration_missing") };
        await database.ReadAsync(sql =>
        {
            checks["model_cost_evidence"] = ModelCostRepository.EvidenceReadinessFor(sql, user);
            var workers = monitor.Workers(sql); var healthy = workers["status"]!.GetValue<string>() == "healthy";
            checks["native_workers"] = new JsonObject { ["passed"] = healthy, ["status"] = healthy ? "passed" : "blocked", ["workers"] = workers, ["blockers"] = healthy ? new JsonArray() : new JsonArray("native_workers_missing") };
            return Task.FromResult(0);
        }, cancellation);
        result["ready_for_hosting"] = new[] { "operations_backup_restore", "broker_safety_boundary", "deployment_security", "native_workers" }.All(key => checks[key]?["passed"]?.GetValue<bool>() == true);
        result["ready_for_strategy"] = new[] { "account_baseline", "broker_fee_evidence", "point_in_time_data", "strategy_acceptance", "drawdown_control", "paper_sessions" }.All(key => checks[key]?["passed"]?.GetValue<bool>() == true);
        result["ready_for_deployment"] = checks.All(check => check.Value?["passed"]?.GetValue<bool>() == true); return Ok(result);
    }
}
