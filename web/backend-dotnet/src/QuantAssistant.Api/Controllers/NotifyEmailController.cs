using System.Text.Json.Nodes;
using Microsoft.AspNetCore.Mvc;
using QuantAssistant.Api.Contracts;
using QuantAssistant.Api.Middleware;
using QuantAssistant.Api.Services;
using QuantAssistant.Core.Auth;
using QuantAssistant.Infrastructure.Auth;
using QuantAssistant.Infrastructure.Db;

namespace QuantAssistant.Api.Controllers;

/// <summary>
/// batch3 流 A：邮件推送管理接口（管理员）。
/// GET/PUT api/v1/notify/email/settings：SMTP 配置（存 site_settings，键 QUANT_SMTP_*；密码不回显）；
/// GET api/v1/notify/email/log：发送记录查询。管理操作记 admin_audit。
/// </summary>
[ApiController]
[Route("api/v1/notify/email")]
public sealed class NotifyEmailController(
    AuthService auth, EmailDeliveryService email, AccountDatabase database, AdminAuditRepository audits) : ControllerBase
{
    private Account? Admin => Request.Cookies.TryGetValue(AuthErrors.CookieName, out var token) ? auth.RequireAdmin(token) : null;

    private IActionResult? RejectNonAdmin()
    {
        if (Admin is not null) return null;
        return StatusCode(AuthErrors.Status("admin_required"),
            new ErrorResponse("admin_required", AuthErrors.Message("admin_required"), HttpContext.GetRequestId()));
    }

    public sealed class SmtpSettingsBody
    {
        public string? Host { get; set; }
        public int? Port { get; set; }
        public string? User { get; set; }
        public string? From { get; set; }
        public string? NotifyTo { get; set; }
        public string? Password { get; set; }
        public bool ClearPassword { get; set; }
    }

    private static bool ValidAddr(string? value) =>
        value is { Length: > 3 and <= 320 } && value.Contains('@') && value.Contains('.') && !value.Any(char.IsWhiteSpace);

    [HttpGet("settings")]
    public IActionResult GetSettings()
    {
        if (RejectNonAdmin() is { } rejected) return rejected;
        return Ok(email.MaskedSettings());
    }

    [HttpPut("settings")]
    public async Task<IActionResult> SaveSettings([FromBody] SmtpSettingsBody body, CancellationToken cancellation = default)
    {
        var admin = Admin;
        if (admin is null) return RejectNonAdmin()!;
        if (body.Host is not { Length: > 0 and <= 256 } || body.Host.Any(char.IsWhiteSpace))
            throw new ArgumentException("SMTP 服务器地址无效。");
        if (body.Port is < 1 or > 65535) throw new ArgumentException("SMTP 端口须在 1 到 65535 之间。");
        if (body.From is not null && !ValidAddr(body.From)) throw new ArgumentException("发件人地址无效。");
        if (body.NotifyTo is not null && !ValidAddr(body.NotifyTo)) throw new ArgumentException("收件人地址无效。");
        if (body is { User: { Length: > 256 } } or { Password: { Length: > 512 } })
            throw new ArgumentException("SMTP 用户名或密码过长。");
        var before = email.MaskedSettings();
        await database.WriteNonLedgerAsync(admin.Id, async sql =>
        {
            async Task Upsert(string key, string? value)
            {
                if (value is null) await sql.ExecuteAsync("DELETE FROM site_settings WHERE key=$1", key);
                else await sql.ExecuteAsync("INSERT INTO site_settings(key,value) VALUES($1,$2) ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value", key, value);
            }
            await Upsert("QUANT_SMTP_HOST", body.Host.Trim());
            await Upsert("QUANT_SMTP_PORT", body.Port?.ToString());
            await Upsert("QUANT_SMTP_USER", string.IsNullOrWhiteSpace(body.User) ? null : body.User.Trim());
            await Upsert("QUANT_SMTP_FROM", string.IsNullOrWhiteSpace(body.From) ? null : body.From.Trim());
            await Upsert("QUANT_SMTP_NOTIFY_TO", string.IsNullOrWhiteSpace(body.NotifyTo) ? null : body.NotifyTo.Trim());
            // 密码：空=保持，ClearPassword=true=清除；永不回显、永不落日志。
            if (body.ClearPassword) await sql.ExecuteAsync("DELETE FROM site_settings WHERE key='QUANT_SMTP_PASS'");
            else if (!string.IsNullOrEmpty(body.Password))
                await sql.ExecuteAsync("INSERT INTO site_settings(key,value) VALUES('QUANT_SMTP_PASS',$1) ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value", body.Password);
            return 0;
        }, cancellation);
        // 管理操作记 admin_audit（哈希链；detail 只记掩码后配置）。
        var after = email.MaskedSettings();
        database.WriteNonLedger(admin.Id, sql =>
        {
            audits.Write(sql, admin.Id, "email_smtp_settings_updated", null,
                new JsonObject { ["before"] = before.DeepClone(), ["after"] = after.DeepClone() }.ToJsonString(), DateTime.UtcNow);
            return 0;
        });
        return Ok(after);
    }

    /// <summary>发送测试邮件（走同一 SendAsync 入口与重试/日志路径）。</summary>
    [HttpPost("settings/test")]
    public async Task<IActionResult> TestSend(CancellationToken cancellation = default)
    {
        var admin = Admin;
        if (admin is null) return RejectNonAdmin()!;
        var result = await email.SendAsync(admin.Id, "test", new JsonObject(), cancellation);
        return Ok(result);
    }

    [HttpGet("log")]
    public async Task<IActionResult> Log([FromQuery] int page = 1, [FromQuery] int limit = 20,
        CancellationToken cancellation = default)
    {
        if (RejectNonAdmin() is { } rejected) return rejected;
        if (page is < 1 or > 100000 || limit is < 1 or > 100) throw new ArgumentException("分页参数无效。");
        var rows = await database.QueryAsync("""
            SELECT id,user_id,to_addr,subject,template,status,attempts,last_error,created_at
            FROM email_send_log ORDER BY id DESC LIMIT $1 OFFSET $2
            """, [limit, (page - 1) * limit], cancellation);
        var total = Convert.ToInt64(database.Read(sql => sql.Scalar("SELECT count(*) FROM email_send_log")));
        return Ok(new JsonObject { ["items"] = rows, ["total"] = total, ["page"] = page, ["limit"] = limit });
    }
}
