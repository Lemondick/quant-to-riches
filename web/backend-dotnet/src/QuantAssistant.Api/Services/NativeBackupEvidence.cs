using System.IO.Compression;
using System.Security.Cryptography;
using System.Text.Json.Nodes;
using System.Globalization;

namespace QuantAssistant.Api.Services;

public sealed class NativeBackupEvidence(IConfiguration configuration)
{
    private readonly object cacheLock = new();
    private string? cachedSignature;
    private DateTimeOffset cachedAt;
    private JsonArray? cached;
    public JsonArray Read()
    {
        try { return ReadCore(); }
        catch (Exception error) when (error is IOException or UnauthorizedAccessException)
        { return [new JsonObject { ["archive"] = "备份目录暂不可读取", ["verified"] = false, ["status"] = "unavailable", ["size_bytes"] = 0 }]; }
    }

    private JsonArray ReadCore()
    {
        var directory=configuration["QUANT_BACKUP_DIR"];
        if(string.IsNullOrWhiteSpace(directory)||!Directory.Exists(directory))return [];
        lock (cacheLock)
        {
            var files = new DirectoryInfo(directory).EnumerateFiles("quant-site-*.zip").OrderByDescending(f => f.LastWriteTimeUtc).Take(10).ToArray();
            var signature = string.Join('|', files.Select(file =>
            {
                var proof = new FileInfo(file.FullName + ".restore.json");
                return $"{file.FullName}:{file.Length}:{file.LastWriteTimeUtc.Ticks}:{file.Attributes}:{(proof.Exists ? proof.Length : -1)}:{(proof.Exists ? proof.LastWriteTimeUtc.Ticks : 0)}";
            }));
            var now = DateTimeOffset.UtcNow;
            if (cached is not null && signature == cachedSignature && now - cachedAt < TimeSpan.FromMinutes(1)) return cached.DeepClone().AsArray();
            cached = Verify(files, now);
            cachedSignature = signature;
            cachedAt = now;
            return cached.DeepClone().AsArray();
        }
    }

    private static JsonArray Verify(FileInfo[] archiveFiles, DateTimeOffset now)
    {
        var items=new JsonArray();
        foreach(var file in archiveFiles)
        {
            var item=new JsonObject{["archive"]=file.Name,["modified_at"]=new DateTimeOffset(file.LastWriteTimeUtc).ToOffset(TimeSpan.FromHours(8)).ToString("O"),["size_bytes"]=file.Length,["verified"]=false,["restore_verified"]=false};
            try
            {
                if((file.Attributes&FileAttributes.ReparsePoint)!=0)throw new InvalidDataException();
                using var archive=ZipFile.OpenRead(file.FullName);if(archive.Entries.Count>100000)throw new InvalidDataException();
                var entries=archive.Entries.ToDictionary(e=>e.FullName,StringComparer.Ordinal);
                using var manifestReader=new StreamReader(entries["manifest.json"].Open());
                if(entries["manifest.json"].Length>20*1024*1024)throw new InvalidDataException();
                var manifest=JsonNode.Parse(manifestReader.ReadToEnd())?.AsObject() ?? throw new InvalidDataException();
                if(manifest["version"]?.GetValue<int>()!=1 || manifest["secrets_included"]?.GetValue<bool>() != false)throw new InvalidDataException();
                if (!DateTimeOffset.TryParseExact(manifest["created_at"]?.GetValue<string>(), "yyyyMMdd'T'HHmmss'.'ffffff'Z'", CultureInfo.InvariantCulture, DateTimeStyles.AssumeUniversal, out var created)) throw new InvalidDataException();
                item["created_at"] = created.ToString("O");
                item["checked_at"] = now.ToString("O");
                var files=manifest["files"]?.AsObject() ?? throw new InvalidDataException();if(entries.Count!=files.Count+2)throw new InvalidDataException();
                item["file_count"] = files.Count;
                foreach(var (name,value) in files.Append(new KeyValuePair<string,JsonNode?>("../database.dump",manifest["database_sha256"])))
                {
                    var member=name=="../database.dump"?"database.dump":"site/"+name;
                    if(member.Contains('\\')||member.Contains(':')||member.Split('/').Any(p=>p is ".." or "")||member.StartsWith('/'))throw new InvalidDataException();
                    using var stream=entries[member].Open();var hash=Convert.ToHexString(SHA256.HashData(stream)).ToLowerInvariant();
                    if(hash!=value?.GetValue<string>())throw new InvalidDataException();
                }
                item["verified"]=true;item["status"]="verified";
                var proofPath=file.FullName+".restore.json";
                if(File.Exists(proofPath))
                {
                    var proofFile = new FileInfo(proofPath);
                    if (proofFile.Length > 16384 || (proofFile.Attributes & FileAttributes.ReparsePoint) != 0) throw new InvalidDataException();
                    var proof=JsonNode.Parse(File.ReadAllText(proofPath))?.AsObject() ?? throw new InvalidDataException();using var input=File.OpenRead(file.FullName);
                    item["restore_verified"]=proof["version"]?.GetValue<int>()==1 && proof["restored"]?.GetValue<bool>()==true && proof["archive_sha256"]?.GetValue<string>()==Convert.ToHexString(SHA256.HashData(input)).ToLowerInvariant();
                    item["restored_at"]=proof["restored_at"]?.DeepClone();
                }
            }
            catch(Exception error) when(error is IOException or InvalidDataException or System.Text.Json.JsonException or KeyNotFoundException or ArgumentException or InvalidOperationException)
            {item["status"]="invalid";item["verified"]=false;item["restore_verified"]=false;}
            items.Add(item);
        }
        return items;
    }
}
