using System.Collections.Concurrent;
using System.Text.Json.Nodes;
using Microsoft.Extensions.Caching.Memory;

namespace QuantAssistant.Api.Services;

/// <summary>Coalesce simultaneous reads; committed account/public writes change the authenticated version.</summary>
public sealed class AccountDisplayCache(IMemoryCache cache)
{
    private readonly ConcurrentDictionary<string, Lazy<Task<JsonObject>>> pending = new();
    public async Task<JsonObject> Read(string user, string kind, string? version, Func<CancellationToken, Task<JsonObject>> read, CancellationToken cancellation)
    {
        if (version is null) return await read(cancellation);
        var key = $"account-display:{user}:{kind}:{version}";
        if (cache.TryGetValue<JsonObject>(key, out var saved)) return saved!.DeepClone().AsObject();
        var operation = pending.GetOrAdd(key, requestKey => new Lazy<Task<JsonObject>>(async () =>
        {
            using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(10));
            try
            {
                var result = await read(timeout.Token);
                cache.Set(key, result.DeepClone().AsObject(), TimeSpan.FromMilliseconds(500));
                return result;
            }
            finally { pending.TryRemove(key, out _); }
        }));
        return (await operation.Value.WaitAsync(cancellation)).DeepClone().AsObject();
    }
}
