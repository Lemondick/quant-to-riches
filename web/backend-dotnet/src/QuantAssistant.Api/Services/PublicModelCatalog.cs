using System.Net;
using System.Net.Http.Headers;
using System.Net.Sockets;
using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;
using QuantAssistant.Core.Errors;
using QuantAssistant.Infrastructure.Runtime;

namespace QuantAssistant.Api.Services;

/// <summary>HTTPS model discovery with DNS validation on the actual socket and no redirects.</summary>
public sealed class PublicModelCatalog
{
    // IANA special-purpose registry: the parent is non-global unless a more
    // specific public allocation applies. Transition mechanisms are excluded.
    private static readonly IPNetwork IetfAssignments = IPNetwork.Parse("2001::/23");
    private static readonly IPNetwork[] IetfGlobal = [
        IPNetwork.Parse("2001:1::1/128"), IPNetwork.Parse("2001:1::2/128"), IPNetwork.Parse("2001:1::3/128"),
        IPNetwork.Parse("2001:3::/32"), IPNetwork.Parse("2001:4:112::/48"),
        IPNetwork.Parse("2001:20::/28"), IPNetwork.Parse("2001:30::/28")
    ];
    private static readonly IPNetwork DocumentationV6 = IPNetwork.Parse("3fff::/20");
    private readonly Func<string, CancellationToken, Task<IPAddress[]>> resolve;
    private readonly Func<IPAddress, HttpMessageHandler> transport;
    public PublicModelCatalog() : this(Dns.GetHostAddressesAsync, CreateTransport) { }
    internal PublicModelCatalog(Func<string, CancellationToken, Task<IPAddress[]>> resolve, Func<IPAddress, HttpMessageHandler> transport)
    {
        this.resolve = resolve;
        this.transport = transport;
    }
    private static HttpMessageHandler CreateTransport(IPAddress address)
    {
        var handler = new SocketsHttpHandler { AllowAutoRedirect = false, UseProxy = false, ConnectTimeout = TimeSpan.FromSeconds(5) };
        handler.ConnectCallback = async (context, token) =>
        {
            var socket = new Socket(address.AddressFamily, SocketType.Stream, ProtocolType.Tcp);
            try { await socket.ConnectAsync(new IPEndPoint(address, context.DnsEndPoint.Port), token); return new NetworkStream(socket, ownsSocket: true); }
            catch { socket.Dispose(); throw; }
        };
        return handler;
    }
    public static string Endpoint(string value)
    {
        if (string.IsNullOrWhiteSpace(value) || value.Any(char.IsControl))
            throw Failure("请输入公开HTTPS兼容API地址，不包含密钥或查询参数。");
        var original = value.Trim();
        var endpoint = original.TrimEnd('/');
        if (endpoint.Length is < 8 or > 500 || !Uri.TryCreate(endpoint, UriKind.Absolute, out var uri) || uri.Scheme != "https"
            || uri.Port != 443 || uri.UserInfo.Length > 0 || uri.Query.Length > 0 || uri.Fragment.Length > 0
            || endpoint.IndexOfAny(['?', '#', '%', '\\', '@']) >= 0 || endpoint.Any(char.IsWhiteSpace))
            throw Failure("请输入公开HTTPS兼容API地址，不包含密钥或查询参数。");
        // Inspect the original path before Uri normalizes dot segments.
        var pathStart = original.IndexOf('/', original.IndexOf("://", StringComparison.Ordinal) + 3);
        var path = pathStart < 0 ? "" : original[pathStart..];
        if (path.Contains("//", StringComparison.Ordinal) || path.TrimEnd('/').Split('/').Skip(1).Any(
            part => part is "." or ".." || !Regex.IsMatch(part, @"\A[A-Za-z0-9._~-]+\z")))
            throw Failure("请输入不含转义或相对路径的兼容API地址。");
        return endpoint;
    }
    private static ApplicationFailure Failure(string message) => new("model_discovery_failed", 422, message);
    private static bool Public(IPAddress address)
    {
        if (address.IsIPv4MappedToIPv6) address = address.MapToIPv4();
        var bytes = address.GetAddressBytes();
        if (address.AddressFamily == AddressFamily.InterNetworkV6)
        {
            if (IetfAssignments.Contains(address)) return IetfGlobal.Any(network => network.Contains(address));
            return (bytes[0] & 0xe0) == 0x20
                && !(bytes[0] == 0x20 && bytes[1] == 0x02) // 6to4 embeds IPv4 destinations
                && !DocumentationV6.Contains(address)
                && !(bytes[0] == 0x20 && bytes[1] == 0x01 && bytes[2] == 0x0d && bytes[3] == 0xb8);
        }
        return !(bytes[0] is 0 or 10 or 127 || bytes[0] >= 224 || bytes[0] == 169 && bytes[1] == 254
            || bytes[0] == 172 && bytes[1] is >= 16 and <= 31 || bytes[0] == 192 && bytes[1] == 168
            || bytes[0] == 100 && bytes[1] is >= 64 and <= 127 || bytes[0] == 192 && bytes[1] == 0
            || bytes[0] == 192 && bytes[1] == 88 && bytes[2] == 99 // retired IPv4 transition relays
            || bytes[0] == 198 && bytes[1] is 18 or 19 || bytes[0] == 198 && bytes[1] == 51 && bytes[2] == 100
            || bytes[0] == 203 && bytes[1] == 0 && bytes[2] == 113);
    }
    public async Task<string[]> Discover(string endpoint, string key, CancellationToken cancellation)
    {
        endpoint = Endpoint(endpoint);
        using var deadline = CancellationTokenSource.CreateLinkedTokenSource(cancellation);
        deadline.CancelAfter(TimeSpan.FromSeconds(20));
        var timedOut = false;
        try
        {
            var addresses = await resolve(new Uri(endpoint).Host, deadline.Token);
            if (addresses.Length == 0 || addresses.Any(a => !Public(a))) throw Failure("API地址不可用或指向非公开网络。");
            // Catalog GETs are safe to repeat across pinned public addresses. One
            // unreachable TLS endpoint must not hide models served by another.
            foreach (var address in addresses.Distinct())
            {
                using var attempt = CancellationTokenSource.CreateLinkedTokenSource(deadline.Token);
                attempt.CancelAfter(TimeSpan.FromSeconds(5));
                using var handler = transport(address);
                using var client = new HttpClient(handler) { Timeout = Timeout.InfiniteTimeSpan };
                using var request = new HttpRequestMessage(HttpMethod.Get, endpoint + "/models");
                request.Headers.Authorization = new AuthenticationHeaderValue("Bearer", key);
                try
                {
                    using var response = await client.SendAsync(request, HttpCompletionOption.ResponseHeadersRead, attempt.Token);
                    if (!response.IsSuccessStatusCode) throw Failure(response.StatusCode is HttpStatusCode.Unauthorized or HttpStatusCode.Forbidden ? "API Key或模型列表权限无效。" : "模型列表暂不可用，请稍后重试。");
                    await using var stream = await response.Content.ReadAsStreamAsync(attempt.Token);
                    using var buffer = new MemoryStream(); var chunk = new byte[8192]; int count;
                    while ((count = await stream.ReadAsync(chunk, attempt.Token)) > 0) { buffer.Write(chunk, 0, count); if (buffer.Length > 500000) throw Failure("模型列表响应异常。"); }
                    var text = Encoding.UTF8.GetString(buffer.ToArray());
                    if (text.Contains(key, StringComparison.Ordinal)) throw Failure("模型列表响应异常。");
                    using var document = JsonDocument.Parse(text);
                    var jev = endpoint == "https://api.typesafe.ai/v1";
                    var modelField = jev ? "name" : "id";
                    if (document.RootElement.ValueKind != JsonValueKind.Object || !document.RootElement.TryGetProperty(jev ? "models" : "data", out var data) || data.ValueKind != JsonValueKind.Array || data.GetArrayLength() > 2000) throw Failure("服务未返回兼容模型列表。");
                    var models = data.EnumerateArray().Where(r => r.ValueKind == JsonValueKind.Object && r.TryGetProperty(modelField, out var id) && id.ValueKind == JsonValueKind.String)
                        .Select(r => r.GetProperty(modelField).GetString()!).Where(ModelCostRepository.ValidModel).Distinct().Order(StringComparer.Ordinal).ToArray();
                    return models.Length > 0 ? models : throw Failure("没有可选择的模型，请核对Key权限。");
                }
                catch (HttpRequestException) { }
                catch (IOException) { }
                catch (OperationCanceledException) when (!cancellation.IsCancellationRequested)
                {
                    timedOut = true;
                    if (deadline.IsCancellationRequested) break;
                }
            }
            throw Failure(timedOut ? "读取模型列表超时，没有更改服务配置。" : "读取模型列表连接失败，没有更改服务配置。");
        }
        catch (SocketException) { throw Failure("读取模型列表连接失败，没有更改服务配置。"); }
        catch (OperationCanceledException) when (!cancellation.IsCancellationRequested) { throw Failure("读取模型列表超时，没有更改服务配置。"); }
        catch (JsonException) { throw Failure("服务未返回兼容模型列表。"); }
    }
}
