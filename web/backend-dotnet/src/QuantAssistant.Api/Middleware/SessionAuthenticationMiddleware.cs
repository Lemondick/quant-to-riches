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
                new Claim("role", account.Role == AccountRole.Admin ? "admin" : "user"),
            }, "quant_session"));
        }

        await next(context);
    }

    private bool AllowedOrigin(HttpRequest request)
    {
        if (!request.Headers.TryGetValue("Origin", out var header)) return true;
        if (!Uri.TryCreate(header.ToString(), UriKind.Absolute, out var origin) || origin.AbsolutePath != "/"
            || origin.Query.Length > 0 || origin.Fragment.Length > 0 || origin.UserInfo.Length > 0) return false;
        var domain = configuration["QUANT_DOMAIN"];
        var allowed = string.IsNullOrWhiteSpace(domain) ? $"{request.Scheme}://{request.Host}"
            : domain.Contains("://", StringComparison.Ordinal) ? domain : "https://" + domain;
        return Uri.TryCreate(allowed, UriKind.Absolute, out var site)
            && string.Equals(origin.GetLeftPart(UriPartial.Authority), site.GetLeftPart(UriPartial.Authority), StringComparison.OrdinalIgnoreCase);
    }
}
