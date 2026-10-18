using System.Text.Json.Nodes;
using System.Security.Cryptography;
using Npgsql;
using QuantAssistant.Api.Services;
using QuantAssistant.Infrastructure.Db;

namespace QuantAssistant.Api;

internal static class NativeSecretCommands
{
    public static bool Execute(string[] args,IServiceProvider services,string connectionString)
    {
        if(args is not ["--import-model-secrets",var source])return false;
        var root=Path.GetFullPath(source);
        var secrets=services.GetRequiredService<PrivateSecretStore>();
        using var connection=new NpgsqlConnection(connectionString);connection.Open();
        using var transaction=connection.BeginTransaction();var sql=new AccountSql(connection,transaction);
        sql.Execute("SELECT pg_advisory_xact_lock(hashtextextended('site:migration',0))");
        if((long)sql.Scalar("SELECT count(*) FROM migration_batches")! !=1)throw new InvalidOperationException("Import the frozen site before its model credentials.");
        if(!Directory.Exists(root)||(File.GetAttributes(root)&FileAttributes.ReparsePoint)!=0)throw new ArgumentException("Frozen source is invalid.");
        NativePaths.RejectReparseAncestors(root);
        var manifest=sql.Query("SELECT manifest_json FROM migration_batches")[0]!["manifest_json"]!["files"]!.AsObject();
        foreach(var (relative,expected) in manifest)
        {
            var path=Path.GetFullPath(Path.Combine(root,relative.Replace('/',Path.DirectorySeparatorChar)));
            var comparison=OperatingSystem.IsWindows()?StringComparison.OrdinalIgnoreCase:StringComparison.Ordinal;
            if(!path.StartsWith(root.TrimEnd(Path.DirectorySeparatorChar)+Path.DirectorySeparatorChar,comparison)||!File.Exists(path))throw new IOException("Frozen source differs from its imported manifest.");
            NativePaths.RejectReparseAncestors(Path.GetDirectoryName(path)!);
            if((File.GetAttributes(path)&FileAttributes.ReparsePoint)!=0)throw new IOException("Frozen source contains a symbolic link.");
            using var stream=File.OpenRead(path);
            if(Convert.ToHexString(SHA256.HashData(stream)).ToLowerInvariant()!=expected!.GetValue<string>())throw new IOException("Frozen source differs from its imported manifest.");
        }
        var users=sql.Query("SELECT id,role FROM users ORDER BY id");var count=0;
        foreach(var user in users.OfType<JsonObject>())
        {
            var id=user["id"]!.GetValue<string>();
            var directory=user["role"]!.GetValue<string>()=="admin"?root:Path.Combine(root,"users",id);
            NativePaths.RejectReparseAncestors(directory);
            foreach(var (filename,key,fallback) in new[]{("advisor-provider.json","model-provider","advisor-byok.key"),("advisor-jev.json","model-jev","advisor-jev.key")})
            {
                var configPath=Path.Combine(directory,filename);
                if(!File.Exists(configPath))continue;
                if((File.GetAttributes(configPath)&FileAttributes.ReparsePoint)!=0)throw new IOException("Credential configuration cannot be a symbolic link.");
                var legacy=JsonNode.Parse(File.ReadAllText(configPath))!.AsObject();
                var stored=sql.Query("SELECT payload FROM user_documents WHERE user_id=$1 AND document_key=$2",id,key).FirstOrDefault()?["payload"]?.AsObject()??throw new InvalidOperationException("Model connection was not imported.");
                if(stored["revision"]!.GetValue<long>()!=legacy["revision"]!.GetValue<long>()||key=="model-provider"&&stored["endpoint"]?.GetValue<string>()!=legacy["endpoint"]?.GetValue<string>())throw new InvalidOperationException("Frozen model configuration differs from the imported site.");
                if(stored["key_reference"] is not null)
                {if(!secrets.Exists(id,stored["key_reference"]!.GetValue<string>()))throw new IOException("Imported credential is missing; restore private keys first.");continue;}
                var name=legacy["credential_file"]?.GetValue<string>()??fallback;
                if(Path.GetFileName(name)!=name||name.IndexOfAny(['/', '\\'])>=0||!name.EndsWith(".key",StringComparison.Ordinal))throw new ArgumentException("Legacy credential path is invalid.");
                var path=Path.Combine(directory,name);
                if(!File.Exists(path)||(File.GetAttributes(path)&FileAttributes.ReparsePoint)!=0||new FileInfo(path).Length>8192)throw new IOException("Legacy credential is unavailable.");
                stored["key_reference"]=secrets.Save(id,File.ReadAllText(path).Trim());
                sql.Execute("UPDATE user_documents SET payload=$3::jsonb,updated_at=now() WHERE user_id=$1 AND document_key=$2",id,key,stored.ToJsonString());count++;
            }
        }
        transaction.Commit();Console.WriteLine($"model_credentials_imported:{count}");return true;
    }
}
