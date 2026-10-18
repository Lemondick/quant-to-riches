namespace QuantAssistant.Api.Middleware;

/// <summary>请求 ID 扩展：从 HttpContext.Items 读取当前请求 ID。</summary>
public static class RequestIdExtensions
{
    private const string ItemKey = "request_id";

    public static string GetRequestId(this HttpContext context)
    {
        if (context.Items.TryGetValue(ItemKey, out var value) && value is string id)
        {
            return id;
        }

        // 兜底：中间件未挂载时生成一个（正常情况下 RequestIdMiddleware 已设置）。
        id = Guid.NewGuid().ToString("N");
        context.Items[ItemKey] = id;
        return id;
    }

    public static void SetRequestId(this HttpContext context, string id) => context.Items[ItemKey] = id;
}
