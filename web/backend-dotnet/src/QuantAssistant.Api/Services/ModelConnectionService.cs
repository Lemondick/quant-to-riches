using System.Text.Json;
using System.Text.Json.Nodes;
using QuantAssistant.Core.Errors;
using QuantAssistant.Infrastructure.Db;
using QuantAssistant.Infrastructure.Runtime;

namespace QuantAssistant.Api.Services;

public sealed class ModelConnectionService(AccountDatabase database, PrivateSecretStore secrets, PublicModelCatalog catalog)
{
    public sealed record ModelRoles(string News, string Strategy, string Reviewer, string Decision);
    public sealed record SaveModelsBody(ModelRoles Models, long Revision);
    public sealed record ProviderBody(string Endpoint, long Revision, string ApiKey = "");
    public sealed record JevBody(string ApiKey, long Revision);
    private static readonly ModelRoles Defaults = new("gemini-3.1-pro-low", "gpt-6.1-sol", "gpt-6-astra", "jev-latest");
    private static readonly JsonSerializerOptions Json = new() { PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower };

    private static JsonObject Connection(AccountSql sql, string userId, string key) => sql.Scalar("SELECT payload::text FROM user_documents WHERE user_id=$1 AND document_key=$2", userId, key) is string value
        ? JsonNode.Parse(value)!.AsObject() : new() { ["revision"] = 0L, ["endpoint"] = "", ["models"] = new JsonArray(), ["key_reference"] = null };
    private static ModelRoles Roles(AccountSql sql, string userId) => sql.Scalar("SELECT models_json::text FROM advisor_model_settings WHERE user_id=$1 AND id=1", userId) is string value
        ? JsonSerializer.Deserialize<ModelRoles>(value, Json)! : Defaults;
    private static long Revision(AccountSql sql, string userId) => Convert.ToInt64(sql.Scalar("SELECT revision FROM advisor_model_settings WHERE user_id=$1 AND id=1", userId) ?? 0);

    private static string[] ModelIds(JsonObject connection) =>
        connection["models"] is JsonArray models
            ? models
                .Select(node => node is JsonValue value && value.TryGetValue<string>(out var model) ? model : null)
                .Where(model => !string.IsNullOrWhiteSpace(model))
                .Select(model => model!)
                .Distinct(StringComparer.Ordinal)
                .ToArray()
            : [];

    private static void Idle(AccountSql sql, string userId)
    {
        var active = (long)sql.Scalar("""
            SELECT (SELECT count(*) FROM market_advisor_reports WHERE user_id=$1 AND status IN('queued','running'))
            +(SELECT count(*) FROM agent_research_jobs WHERE user_id=$1 AND status IN('queued','running','cancel_requested'))
            +(SELECT count(*) FROM compute_jobs WHERE user_id=$1 AND kind IN('advisor.report','agent.research') AND status IN('queued','running','cancel_requested'))
            """, userId)!;
        if (active > 0) throw new AdvisorBusyError();
    }
    private static void Store(AccountSql sql, string userId, string key, JsonObject value) => sql.Execute("INSERT INTO user_documents(user_id,document_key,revision,payload) VALUES($1,$2,$3,$4::jsonb) ON CONFLICT(user_id,document_key) DO UPDATE SET revision=EXCLUDED.revision,payload=EXCLUDED.payload,updated_at=now()", userId, key, value["revision"]!.GetValue<long>(), value.ToJsonString());

    public JsonObject Read(string userId) => database.Read(sql => View(sql, userId));
    private JsonObject View(AccountSql sql, string userId)
    {
        var provider = Connection(sql, userId, "model-provider");
        var jev = Connection(sql, userId, "model-jev");
        var providerKey = provider["key_reference"]?.GetValue<string>();
        var jevKey = jev["key_reference"]?.GetValue<string>();
        var providerRevision = provider["revision"]!.GetValue<long>();
        var selected = Convert.ToInt64(sql.Scalar("SELECT provider_revision FROM advisor_provider_selection WHERE user_id=$1 AND id=1", userId) ?? -1);
        var active = (long)sql.Scalar("SELECT (SELECT count(*) FROM market_advisor_reports WHERE user_id=$1 AND status IN('queued','running'))+(SELECT count(*) FROM agent_research_jobs WHERE user_id=$1 AND status IN('queued','running','cancel_requested'))", userId)! > 0;
        var roles = Roles(sql, userId);
        var providerModels = ModelIds(provider);
        var jevModels = ModelIds(jev);
        var compatibleModelsValid = providerModels.Length > 0
            && new[] { roles.News, roles.Strategy, roles.Reviewer }.All(model => providerModels.Contains(model, StringComparer.Ordinal));
        var decisionModelValid = jevModels.Length > 0 && jevModels.Contains(roles.Decision, StringComparer.Ordinal);
        var credentialsValid = providerRevision > 0 && selected == providerRevision
            && secrets.Exists(userId, providerKey) && secrets.Exists(userId, jevKey);

        return new()
        {
            ["models"] = JsonSerializer.SerializeToNode(roles, Json),
            ["defaults"] = JsonSerializer.SerializeToNode(Defaults, Json),
            ["revision"] = Revision(sql, userId),
            ["active"] = active,
            // A saved key is not enough: all selected role models must exist in the
            // latest provider catalogs, especially the separately hosted Jev model.
            ["configured"] = credentialsValid && compatibleModelsValid && decisionModelValid,
            ["compatible_models_valid"] = compatibleModelsValid,
            ["decision_model_valid"] = decisionModelValid,
            ["provider"] = new JsonObject
            {
                ["endpoint"] = provider["endpoint"]?.DeepClone(),
                ["models"] = provider["models"]?.DeepClone(),
                ["revision"] = providerRevision,
                ["has_key"] = secrets.Exists(userId, providerKey)
            },
            ["jev"] = new JsonObject
            {
                ["revision"] = jev["revision"]!.DeepClone(),
                ["models"] = jev["models"]?.DeepClone() ?? new JsonArray(),
                ["has_key"] = secrets.Exists(userId, jevKey)
            }
        };
    }

    public JsonObject Config(string userId)
    {
        var settings = Read(userId);
        return new()
        {
            ["configured"] = settings["configured"]!.DeepClone(),
            ["models"] = settings["models"]!.DeepClone(),
            ["confidence_threshold"] = 0.8,
            ["daily_limit"] = 4,
            ["timezone"] = "Asia/Shanghai",
            ["schedules"] = new JsonObject { ["morning"] = "09:30", ["afternoon"] = "14:30" },
            ["wechat_enabled"] = false,
            ["automatic_trading"] = false,
            ["instance_id"] = Convert.ToHexString(System.Security.Cryptography.SHA256.HashData(System.Text.Encoding.UTF8.GetBytes(userId))).ToLowerInvariant()[..16]
        };
    }

    public JsonObject SaveModels(string userId, SaveModelsBody body)
    {
        if (body.Revision < 0 || new[] { body.Models.News, body.Models.Strategy, body.Models.Reviewer, body.Models.Decision }.Any(m => !ModelCostRepository.ValidModel(m)))
            throw new ArgumentException("模型选择无效");

        return database.WriteNonLedger(userId, sql =>
        {
            Idle(sql, userId);
            if (Revision(sql, userId) != body.Revision) throw new AdvisorSettingsConflictError();

            var provider = Connection(sql, userId, "model-provider");
            var jev = Connection(sql, userId, "model-jev");
            var available = ModelIds(provider);
            var jevAvailable = ModelIds(jev);

            if (available.Length > 0 && new[] { body.Models.News, body.Models.Strategy, body.Models.Reviewer }.Any(model => !available.Contains(model, StringComparer.Ordinal)))
                throw new ApplicationFailure("model_configuration", 422, "资讯、策略和复核模型必须来自当前兼容 API 返回的可用模型列表。");

            if (jevAvailable.Length > 0 && !jevAvailable.Contains(body.Models.Decision, StringComparer.Ordinal))
                throw new ApplicationFailure("model_configuration", 422, "决策模型必须来自当前 Jev 服务返回的可用模型列表。请重新读取 Jev 模型后再保存。");

            sql.Execute("INSERT INTO advisor_model_settings(user_id,id,models_json,revision,updated_at) VALUES($1,1,$2::json,$3,now()) ON CONFLICT(user_id,id) DO UPDATE SET models_json=EXCLUDED.models_json,revision=EXCLUDED.revision,updated_at=EXCLUDED.updated_at", userId, JsonSerializer.Serialize(body.Models, Json), checked((int)(body.Revision + 1)));
            if (available.Length > 0)
                sql.Execute("INSERT INTO advisor_provider_selection(user_id,id,provider_revision) VALUES($1,1,$2) ON CONFLICT(user_id,id) DO UPDATE SET provider_revision=EXCLUDED.provider_revision", userId, checked((int)provider["revision"]!.GetValue<long>()));
            return View(sql, userId);
        });
    }

    public async Task<JsonObject> Discover(string userId, ProviderBody body, CancellationToken cancellation)
    {
        if (body.Revision < 0) throw new ArgumentException("连接版本无效");
        var endpoint = PublicModelCatalog.Endpoint(body.Endpoint);
        var current = database.Read(sql => Connection(sql, userId, "model-provider"));
        if (current["revision"]!.GetValue<long>() != body.Revision) throw new AdvisorSettingsConflictError();
        var key = body.ApiKey.Trim();
        if (key.Length == 0)
        {
            if (current["endpoint"]!.GetValue<string>() != endpoint || current["key_reference"] is null)
                throw new ApplicationFailure("model_discovery_failed", 422, "更换API地址时请提供对应API Key。");
            key = secrets.Read(userId, current["key_reference"]!.GetValue<string>());
        }
        if (key.Length is < 1 or > 4096 || key.Any(char.IsWhiteSpace)) throw new ArgumentException("API Key无效");
        // No DB transaction or lock is held during the bounded network request.
        var models = await catalog.Discover(endpoint, key, cancellation);
        return database.WriteNonLedger(userId, sql =>
        {
            Idle(sql, userId);
            if (Connection(sql, userId, "model-provider")["revision"]!.GetValue<long>() != body.Revision) throw new AdvisorSettingsConflictError();
            var reference = secrets.Save(userId, key);
            Store(sql, userId, "model-provider", new() { ["revision"] = body.Revision + 1, ["endpoint"] = endpoint, ["models"] = JsonSerializer.SerializeToNode(models), ["key_reference"] = reference });
            sql.Execute("INSERT INTO advisor_model_settings(user_id,id,models_json,revision,updated_at) VALUES($1,1,$2::json,$3,now()) ON CONFLICT(user_id,id) DO UPDATE SET revision=EXCLUDED.revision,updated_at=EXCLUDED.updated_at", userId, JsonSerializer.Serialize(Roles(sql, userId), Json), checked((int)(Revision(sql, userId) + 1)));
            return View(sql, userId);
        });
    }

    public JsonObject SaveJev(string userId, JevBody body)
    {
        var key = body.ApiKey.Trim();
        if (body.Revision < 0 || key.Length is < 1 or > 4096 || key.Any(char.IsWhiteSpace)) throw new ArgumentException("Jev连接参数无效");
        return database.WriteNonLedger(userId, sql =>
        {
            Idle(sql, userId);
            if (Connection(sql, userId, "model-jev")["revision"]!.GetValue<long>() != body.Revision) throw new AdvisorSettingsConflictError();
            Store(sql, userId, "model-jev", new() { ["revision"] = body.Revision + 1, ["key_reference"] = secrets.Save(userId, key) });
            return View(sql, userId);
        });
    }

    public async Task<JsonObject> DiscoverJev(string userId, JevBody body, CancellationToken cancellation)
    {
        if (body.Revision < 0) throw new ArgumentException("连接版本无效");
        var current = database.Read(sql =>
        {
            Idle(sql, userId);
            return Connection(sql, userId, "model-jev");
        });
        if (current["revision"]!.GetValue<long>() != body.Revision) throw new AdvisorSettingsConflictError();
        var key = body.ApiKey.Trim();
        if (key.Length == 0)
        {
            if (current["key_reference"] is null) throw new ApplicationFailure("model_discovery_failed", 422, "请先填写此账号的Jev API Key。");
            key = secrets.Read(userId, current["key_reference"]!.GetValue<string>());
        }
        if (key.Length is < 1 or > 4096 || key.Any(char.IsWhiteSpace)) throw new ArgumentException("Jev API Key无效");
        var models = await catalog.Discover("https://api.typesafe.ai/v1", key, cancellation);
        return database.WriteNonLedger(userId, sql =>
        {
            Idle(sql, userId);
            if (Connection(sql, userId, "model-jev")["revision"]!.GetValue<long>() != body.Revision) throw new AdvisorSettingsConflictError();
            var reference = current["key_reference"]?.GetValue<string>();
            if (body.ApiKey.Trim().Length > 0) reference = secrets.Save(userId, key);
            Store(sql, userId, "model-jev", new() { ["revision"] = body.Revision + 1, ["models"] = JsonSerializer.SerializeToNode(models), ["key_reference"] = reference });
            sql.Execute("INSERT INTO advisor_model_settings(user_id,id,models_json,revision,updated_at) VALUES($1,1,$2::json,$3,now()) ON CONFLICT(user_id,id) DO UPDATE SET revision=EXCLUDED.revision,updated_at=EXCLUDED.updated_at", userId, JsonSerializer.Serialize(Roles(sql, userId), Json), checked((int)(Revision(sql, userId) + 1)));
            return View(sql, userId);
        });
    }

    public JsonObject WorkerCredentials(string userId) => database.Read(sql =>
    {
        var provider = Connection(sql, userId, "model-provider");
        var jev = Connection(sql, userId, "model-jev");
        var view = View(sql, userId);
        if (!view["configured"]!.GetValue<bool>())
            throw new ApplicationFailure("model_configuration_failed", 422, "请先读取两类服务的模型列表，并选择当前列表中可用的模型。");
        return new JsonObject
        {
            ["endpoint"] = provider["endpoint"]!.DeepClone(),
            ["models"] = view["models"]!.DeepClone(),
            ["api_key"] = secrets.Read(userId, provider["key_reference"]!.GetValue<string>()),
            ["jev_api_key"] = secrets.Read(userId, jev["key_reference"]!.GetValue<string>())
        };
    });
}
