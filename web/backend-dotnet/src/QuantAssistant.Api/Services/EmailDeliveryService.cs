using System.Net;
using System.Net.Mail;
using System.Text.Json.Nodes;
using Microsoft.Extensions.Logging;
using QuantAssistant.Api.Configuration;
using QuantAssistant.Core.Abstractions;
using QuantAssistant.Infrastructure.Db;

namespace QuantAssistant.Api.Services;

/// <summary>
/// batch3 流 A：邮件推送统一入口。
/// 统一入口 SendAsync(userId, template, payload)（batch3 跨流契约）；
/// SMTP 配置走 site_settings（键 QUANT_SMTP_HOST/PORT/USER/PASS/FROM、QUANT_SMTP_NOTIFY_TO），
/// 未配时回退同名环境变量（QuantOptions）；PASS 仅内存使用，永不落日志、不出现在响应中。
/// 发送失败指数退避重试最多 3 次（失败后等待 2s/4s）；每次发送（成功/失败/跳过）都写 email_send_log。
/// 定时报告完成与 critical 告警经 Tick 的 CheckDispatch 接入（站内触达保留，微信通道暂缓）。
/// </summary>
public sealed class EmailDeliveryService(AccountDatabase database, QuantOptions options, ILogger<EmailDeliveryService> logger)
{
private sealed record SmtpConfig(string Host, int Port, string? User, string? Pass, string From, string To)
{
public bool Configured => Host.Length > 0 && From.Length > 0 && To.Length > 0;
}

private SmtpConfig ReadConfig(IAccountSql sql)
{
string? Setting(string key)
{
var raw = sql.Scalar("SELECT value FROM site_settings WHERE key=$1", key) as string;
return string.IsNullOrWhiteSpace(raw)? null: raw.Trim();
}
var host = Setting("QUANT_SMTP_HOST")?? options.SmtpHost?? "";
var port = int.TryParse(Setting("QUANT_SMTP_PORT"), out var sitePort) && sitePort is >= 1 and <= 65535
? sitePort: options.SmtpPort?? 25;
var user = Setting("QUANT_SMTP_USER")?? options.SmtpUser;
var pass = Setting("QUANT_SMTP_PASS")?? options.SmtpPass;
var from = Setting("QUANT_SMTP_FROM")?? options.SmtpFrom?? "";
var to = Setting("QUANT_SMTP_NOTIFY_TO")?? options.SmtpNotifyTo?? from;
return new SmtpConfig(host.Trim(), port, user, pass, from.Trim(), to.Trim());
}

/// <summary>管理员可读的脱敏配置（不含密码，只给 has_password）。</summary>
public JsonObject MaskedSettings() => database.Read(sql =>
{
var config = ReadConfig(sql);
return new JsonObject
{
["host"] = config.Host, ["port"] = config.Port, ["user"] = config.User, ["from"] = config.From,
["notify_to"] = config.To, ["has_password"] =!string.IsNullOrEmpty(config.Pass),
["configured"] = config.Configured,
};
});

/// <summary>模板渲染（纯文本）。未知模板抛 ArgumentException（统一 422）。</summary>
private static (string Subject, string Body) Render(string template, JsonObject payload) => template switch
{
"report_completed" =>
($"{(payload["kind"]?.GetValue<string>() == "morning"? "早盘": "午盘")}定时报告已生成（{payload["report_date"]?.GetValue<string>()}）",
$"定时报告已生成，请登录站点查看完整内容。\n报告类型：{payload["kind"]?.GetValue<string>()}\n报告日期：{payload["report_date"]?.GetValue<string>()}\n报告状态：{payload["status"]?.GetValue<string>()}\n报告ID：{payload["report_id"]?.GetValue<string>()}\n\n（本邮件由系统自动发送，请勿回复。）"),
"critical_alert" =>
($"严重告警：{payload["alert_key"]?.GetValue<string>()}",
$"系统出现严重告警，请登录站点运维页处理。\n告警键：{payload["alert_key"]?.GetValue<string>()}\n告警内容：{payload["message"]?.GetValue<string>()}\n详情：{payload["details"]?.ToJsonString()}\n\n（本邮件由系统自动发送，请勿回复。）"),
"test" =>
("邮件通道测试",
"这是一封测试邮件，说明 SMTP 配置可用。\n\n（本邮件由系统自动发送，请勿回复。）"),
_ => throw new ArgumentException("邮件模板无效。"),
};

private async Task LogAsync(string userId, string to, string subject, string template, string status, int attempts,
string? lastError, CancellationToken cancellation)
{
await database.WriteNonLedgerAsync(userId, async sql =>
{
await sql.ExecuteAsync(
"INSERT INTO email_send_log(user_id,to_addr,subject,template,status,attempts,last_error) VALUES($1,$2,$3,$4,$5,$6,$7)",
userId, to, subject, template, status, attempts, lastError);
return 0;
}, cancellation);
}

private static async Task SendOnceAsync(SmtpConfig config, string subject, string body, CancellationToken cancellation)
{
using var client = new SmtpClient(config.Host, config.Port)
{
EnableSsl = config.Port is 465 or 587,
Timeout = 15000,
DeliveryMethod = SmtpDeliveryMethod.Network,
};
if (!string.IsNullOrEmpty(config.User))
client.Credentials = new NetworkCredential(config.User, config.Pass?? "");
using var message = new MailMessage(config.From, config.To, subject, body)
{
BodyEncoding = System.Text.Encoding.UTF8,
SubjectEncoding = System.Text.Encoding.UTF8,
IsBodyHtml = false,
};
await client.SendMailAsync(message, cancellation);
}

/// <summary>
/// 统一发送入口。SMTP 未配置时记 skipped（不抛错，Tick 不中断）；
/// 发送失败指数退避重试最多 3 次（2s/4s），仍失败记 failed 并返回结果（不抛错，调用方决定）。
/// </summary>
public async Task<JsonObject> SendAsync(string userId, string template, JsonObject payload, CancellationToken cancellation = default)
{
var (subject, body) = Render(template, payload);
var config = database.Read(ReadConfig);
if (!config.Configured)
{
await LogAsync(userId, config.To, subject, template, "skipped", 0, "smtp_not_configured", cancellation);
logger.LogWarning("SMTP 未配置，邮件 {Template} 跳过发送（user {UserId}）。", template, userId);
return new JsonObject { ["status"] = "skipped", ["attempts"] = 0, ["reason"] = "smtp_not_configured"};
}
string? lastError = null;
for (var attempt = 1; attempt <= 3; attempt++)
{
try
{
await SendOnceAsync(config, subject, body, cancellation);
await LogAsync(userId, config.To, subject, template, "sent", attempt, null, cancellation);
return new JsonObject { ["status"] = "sent", ["attempts"] = attempt};
}
catch (Exception error) when (error is not (OperationCanceledException or ArgumentException))
{
// 注意：异常信息不含密码（SmtpClient 异常不携带凭据），此处只记类型名。
lastError = error.GetType().Name;
logger.LogWarning("邮件发送第 {Attempt}/3 次失败（{Template}）：{ErrorType}", attempt, template, error.GetType().Name);
if (attempt < 3) await Task.Delay(TimeSpan.FromSeconds(1 << attempt), cancellation); // 2s, 4s 指数退避
}
}
await LogAsync(userId, config.To, subject, template, "failed", 3, lastError, cancellation);
return new JsonObject { ["status"] = "failed", ["attempts"] = 3, ["reason"] = lastError};
}

/// <summary>
/// Scheduler Tick 接入：定时报告完成与 critical 告警的邮件分发（站内触达保留）。
/// 失败只记日志不中断 Tick；critical 告警每键 24 小时内只发一次（防刷屏）。
/// </summary>
public void CheckDispatch()
{
try
{
DispatchReportCompletions();
DispatchCriticalAlerts();
}
catch (Exception error) when (error is not OutOfMemoryException)
{
logger.LogWarning("邮件分发失败：{ErrorType}", error.GetType().Name);
}
}

private void DispatchReportCompletions()
{
var rows = database.WriteSystem(sql =>
{
var cursor = sql.Scalar("SELECT value FROM site_settings WHERE key='email.dispatch.report_cursor'") as string;
if (cursor is null)
{
// 首次运行：游标初始化为当前时间，避免把历史通知一次性补发。
sql.Execute("INSERT INTO site_settings(key,value) VALUES('email.dispatch.report_cursor',now()::text) ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value");
return new JsonArray();
}
// Freeze the initial scan floor, including legacy timestamp/composite cursors.
// Transaction timestamps do not follow commit order: advancing a high-water
// cursor can permanently skip a completion that becomes visible later.
// Per-notification receipts handle pagination and late commits without replay.
var saved = cursor.StartsWith('{') ? JsonNode.Parse(cursor)!.AsObject() : null;
var created = saved?["created_at"]?.GetValue<string>() ?? cursor;
var user = saved?["user_id"]?.GetValue<string>() ?? "";
var id = saved?["id"]?.GetValue<string>() ?? "";
return sql.Query("""
SELECT n.user_id, n.id, n.kind, n.report_id, n.status, r.report_date::text AS report_date, n.created_at::text AS created_at
FROM advisor_notifications n JOIN market_advisor_reports r ON r.user_id=n.user_id AND r.report_id=n.report_id
JOIN users u ON u.id=n.user_id AND u.status='active'
WHERE (n.created_at,n.user_id,n.id) > ($1::timestamptz,$2::text,$3::text)
AND n.kind IN ('morning','afternoon') AND n.status='completed'
AND NOT EXISTS (SELECT 1 FROM site_settings sent
  WHERE sent.key='email.dispatch.report_sent:' || n.user_id || ':' || n.id)
ORDER BY n.created_at,n.user_id,n.id LIMIT 50
""", created, user, id);
});
foreach (var row in rows.OfType<JsonObject>())
{
var userId = row["user_id"]!.GetValue<string>();
try
{
var result = SendAsync(userId, "report_completed", new JsonObject
{
["kind"] = row["kind"]?.GetValue<string>(), ["report_id"] = row["report_id"]?.GetValue<string>(),
["status"] = row["status"]?.GetValue<string>(), ["report_date"] = row["report_date"]?.GetValue<string>(),
}).GetAwaiter().GetResult();
if (result["status"]?.GetValue<string>() is not ("sent" or "skipped")) break;
}
catch (Exception error) when (error is not OutOfMemoryException)
{
logger.LogWarning("报告完成邮件发送异常（user {UserId}）：{ErrorType}", userId, error.GetType().Name);
break;
}
database.WriteSystem(sql => sql.Execute(
"INSERT INTO site_settings(key,value) VALUES($1,now()::text) ON CONFLICT(key) DO NOTHING",
"email.dispatch.report_sent:" + userId + ":" + row["id"]!.GetValue<string>()));
}
}

private void DispatchCriticalAlerts()
{
var rows = database.Read(sql => sql.Query("""
SELECT user_id, alert_key, message, details_json::text AS details
FROM operational_alerts
WHERE severity='critical' AND status='open'
AND (last_notified_at IS NULL OR last_notified_at < now() - interval '24 hours')
ORDER BY last_seen_at DESC LIMIT 100
"""));
foreach (var row in rows.OfType<JsonObject>())
{
var userId = row["user_id"]!.GetValue<string>();
var alertKey = row["alert_key"]!.GetValue<string>();
try
{
var result = SendAsync(userId, "critical_alert", new JsonObject
{
["alert_key"] = alertKey, ["message"] = row["message"]?.GetValue<string>(),
["details"] = row["details"]?.GetValue<string>(),
}).GetAwaiter().GetResult();
// 发送成功或因未配置跳过都推进 last_notified_at（skipped 下次仍会尝试：24h 冷却后）。
if (result["status"]?.GetValue<string>() is "sent" or "skipped")
database.WriteNonLedger(userId, sql => sql.Execute(
"UPDATE operational_alerts SET last_notified_at=now() WHERE user_id=$1 AND alert_key=$2 AND status='open'",
userId, alertKey));
}
catch (Exception error) when (error is not OutOfMemoryException)
{
logger.LogWarning("critical 告警邮件发送异常（{AlertKey}）：{ErrorType}", alertKey, error.GetType().Name);
}
}
}
}
