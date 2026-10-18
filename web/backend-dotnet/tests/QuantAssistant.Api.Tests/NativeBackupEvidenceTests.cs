using System.IO.Compression;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json.Nodes;
using Microsoft.Extensions.Configuration;
using QuantAssistant.Api.Services;
using Xunit;

namespace QuantAssistant.Api.Tests;

public sealed class NativeBackupEvidenceTests
{
    [Fact]
    public void ArchiveAgeUsesManifestTime_AndChangedArchivesCannotReuseVerifiedEvidence()
    {
        var directory = Path.Combine(Path.GetTempPath(), "quant-backup-evidence-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(directory);
        var path = Path.Combine(directory, "quant-site-synthetic.zip");
        try
        {
            using (var zip = ZipFile.Open(path, ZipArchiveMode.Create))
            {
                var dump = Encoding.UTF8.GetBytes("synthetic dump for inventory validation only");
                using (var stream = zip.CreateEntry("database.dump").Open()) stream.Write(dump);
                using var writer = new StreamWriter(zip.CreateEntry("manifest.json").Open());
                writer.Write(new JsonObject { ["version"] = 1, ["secrets_included"] = false, ["files"] = new JsonObject(),
                    ["created_at"] = "20000101T010203.000004Z", ["database_sha256"] = Convert.ToHexString(SHA256.HashData(dump)).ToLowerInvariant() }.ToJsonString());
            }
            File.SetLastWriteTimeUtc(path, DateTime.UtcNow);
            var configuration = new ConfigurationBuilder().AddInMemoryCollection(new Dictionary<string, string?> { ["QUANT_BACKUP_DIR"] = directory }).Build();
            var reader = new NativeBackupEvidence(configuration);
            var first = reader.Read()[0]!;
            Assert.True(first["verified"]!.GetValue<bool>());
            Assert.Equal(2000, DateTimeOffset.Parse(first["created_at"]!.GetValue<string>()).Year);
            first["verified"] = false;
            Assert.True(reader.Read()[0]!["verified"]!.GetValue<bool>(), "returned evidence must not mutate the cache");
            File.WriteAllText(path + ".restore.json", "null");
            Assert.False(reader.Read()[0]!["verified"]!.GetValue<bool>());
            File.WriteAllText(path, "corrupted synthetic archive");
            Assert.False(reader.Read()[0]!["verified"]!.GetValue<bool>());
        }
        finally { Directory.Delete(directory, true); }
    }
}
