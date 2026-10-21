using System.Security.Claims;
using System.Security.Cryptography;
using System.Text;
using Microsoft.AspNetCore.Mvc.Controllers;
using QuantAssistant.Core.Auth;

namespace QuantAssistant.Api.Middleware;

/// <summary>
/// 全局认证中间件（修复漏洞）。
/// 读取 quant_session Cookie，验证并组装 ClaimsPrincipal。
/// </summary>
public sealed class SessionAuthenticationMiddleware(RequestDelegate next, IConfiguration configuration)
{
    private static readonly HashSet<string> PublicPaths = new(StringComparer.OrdinalIgnoreCase)
    {
        "/api/v1/auth/register", "/api/v1/auth/login", "/api/v1/auth/status", "/api/v1/auth/recovery/redeem",
    };

    public async Task InvokeAsync(HttpContext context, AuthService auth)
    {
        if (context.GetEndpoint()?.Metadata.GetMetadata<ControllerActionDescriptor>() is null
            || !context.Request.Path.StartsWithSegments("/api/v1"))
        {
            await next(context);
            return;
        }

        var write = !HttpMethods.IsGet(context.Request.Method) && !HttpMethods.IsHead(context.Request.Method)
            && !HttpMethods.IsOptions(context.Request.Method);
        if (write && !AllowedOrigin(context.Request)) throw new AuthFailure("origin_forbidden");

        if (!PublicPaths.Contains(context.Request.Path.Value?.TrimEnd('/') ?? ""))
        {
            var token = context.Request.Cookies[AuthErrors.CookieName];
            AuthSession? session;
            using (QuantAssistant.Infrastructure.Db.PerformanceTrace.Measure("authentication"))
                session = token is null ? null : await auth.SessionDataAsync(token, context.RequestAborted);
            if (session is null) throw new AuthFailure("authentication_required");
            if (context.Request.Headers.TryGetValue("X-Session-Scope", out var scope) && scope.ToString() != session.SessionId)
                throw new AuthFailure("authentication_required");
            var account = session.Account;
            context.Items[typeof(AuthSession)] = session;
            if (context.Request.Path.StartsWithSegments("/api/v1/admin") && account.Role != AccountRole.Admin)
                throw new AuthFailure("admin_required");
            // F6：只读角色禁止一切写操作（先于 CSRF 校验）。
            if (write && account.Role == AccountRole.ReadOnly)
                throw new AuthFailure("readonly_forbidden");
            if (write)
            {
                var expected = session.Csrf;
                var supplied = context.Request.Headers["X-CSRF-Token"].ToString();
                if (expected is null || !CryptographicOperations.FixedTimeEquals(Encoding.UTF8.GetBytes(supplied), Encoding.UTF8.GetBytes(expected)))
                    throw new AuthFailure("csrf_invalid");
            }

            context.User = new ClaimsPrincipal(new ClaimsIdentity(new[]
            {
                new Claim("sub", account.Id),
                new Claim(ClaimTypes.NameIdentifier, account.Id),
                new Claim("username", account.Username),
                new Claim("role", account.Role switch { AccountRole.Admin => "admin", AccountRole.ReadOnly => "readonly", _ => "user" }),
            }, "quant_session"));
        }

        await next(context);
    }

    private bool AllowedOrigin(HttpRequest request)
    {
        // E22：缺失 Origin 头不再直接放行。退化检查 Referer；二者皆无或与本站不一致 → 403。
        // （api.md 早已约定“写请求需要允许的 Origin”，此处使代码与文档一致。）
        var header = request.Headers["Origin"].FirstOrDefault() ?? request.Headers["Referer"].FirstOrDefault();
        if (string.IsNullOrWhiteSpace(header)) return false;
        if (!Uri.TryCreate(header, UriKind.Absolute, out var origin) || origin.UserInfo.Length > 0) return false;
        // Origin 头按旧有严格口径校验（无路径/查询/片段）；Referer 带路径，只比对 authority。
        if (request.Headers.ContainsKey("Origin") && (origin.AbsolutePath != "/" || origin.Query.Length > 0 || origin.Fragment.Length > 0))
            return false;
        var domain = configuration["QUANT_DOMAIN"];
        var allowed = string.IsNullOrWhiteSpace(domain) ? $"{request.Scheme}://{request.Host}"
            : domain.Contains("://", StringComparison.Ordinal) ? domain : "https://" + domain;
        return Uri.TryCreate(allowed, UriKind.Absolute, out var site)
            && string.Equals(origin.GetLeftPart(UriPartial.Authority), site.GetLeftPart(UriPartial.Authority), StringComparison.OrdinalIgnoreCase);
    }
}
