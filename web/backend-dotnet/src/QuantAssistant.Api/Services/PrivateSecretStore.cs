using System.Diagnostics;
using System.Security.Principal;
using System.Text.RegularExpressions;
using Microsoft.AspNetCore.DataProtection;
using QuantAssistant.Core.Errors;

namespace QuantAssistant.Api.Services;

/// <summary>Immutable encrypted credential files, outside the site and its ordinary backups.</summary>
public sealed partial class PrivateSecretStore(IConfiguration configuration, IDataProtectionProvider protection)
{
    private string? cachedRoot;
    [GeneratedRegex("^[a-f0-9]{32}$")]
    private static partial Regex Identity();
    private string Root()
    {
        if (cachedRoot is not null) return cachedRoot;
        cachedRoot = PrepareRoot(configuration);
        return cachedRoot;
    }
    public static string PrepareRoot(IConfiguration configuration)
    {
        var configured = configuration["QUANT_SECRET_ROOT"];
        if (string.IsNullOrWhiteSpace(configured)) throw new ApplicationFailure("model_configuration_failed", 422, "请先在服务器配置独立密钥目录。");
        var root = Path.GetFullPath(configured);
        foreach(var name in new[]{"QUANT_SITE_ROOT","QUANT_WEB_ROOT"})
        {
            if(configuration[name] is not {Length:>0} site) continue;
            var data = Path.GetFullPath(site).TrimEnd(Path.DirectorySeparatorChar);
            if (root.Equals(data, StringComparison.OrdinalIgnoreCase) || root.StartsWith(data + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase))
                throw new ApplicationFailure("model_configuration_failed", 422, "密钥目录须位于站点数据和网页目录之外。");
        }
        NativePaths.RejectReparseAncestors(root);
        Directory.CreateDirectory(root);
        NativePaths.RejectReparseAncestors(root);
        if ((File.GetAttributes(root) & FileAttributes.ReparsePoint) != 0) throw new ApplicationFailure("model_configuration_failed", 422, "密钥目录不能使用符号链接。");
        if (OperatingSystem.IsWindows())
        {
            using var identity = WindowsIdentity.GetCurrent();
            var info = new ProcessStartInfo("icacls.exe") { CreateNoWindow = true, UseShellExecute = false, RedirectStandardOutput = true, RedirectStandardError = true };
            foreach (var argument in new[] { root, "/inheritance:r", "/grant:r", "*" + identity.User!.Value + ":(OI)(CI)F", "*S-1-5-18:(OI)(CI)F" }) info.ArgumentList.Add(argument);
            using var process = Process.Start(info)!;
            if (!process.WaitForExit(10000) || process.ExitCode != 0) throw new ApplicationFailure("model_configuration_failed", 422, "无法设置密钥目录权限。");
        }
        else File.SetUnixFileMode(root, UnixFileMode.UserRead | UnixFileMode.UserWrite | UnixFileMode.UserExecute);
        return root;
    }
    public bool Exists(string userId, string? reference) => reference is not null && Identity().IsMatch(userId) && Identity().IsMatch(reference)
        && configuration["QUANT_SECRET_ROOT"] is { Length: > 0 } configured && File.Exists(Path.Combine(Path.GetFullPath(configured), userId + "-" + reference + ".secret"));
    public string Save(string userId, string value)
    {
        if (!Identity().IsMatch(userId) || value.Length is < 1 or > 4096 || value.Any(char.IsWhiteSpace)) throw new ArgumentException("密钥无效");
        var id = Guid.NewGuid().ToString("N"); var path = Path.Combine(Root(), userId + "-" + id + ".secret");
        var bytes = System.Text.Encoding.UTF8.GetBytes(protection.CreateProtector("QuantAssistant.model-key.v1", userId).Protect(value));
        using (var stream = new FileStream(path, FileMode.CreateNew, FileAccess.Write, FileShare.None)) { stream.Write(bytes); stream.Flush(true); }
        if (!OperatingSystem.IsWindows()) File.SetUnixFileMode(path, UnixFileMode.UserRead | UnixFileMode.UserWrite);
        return id;
    }
    public string Read(string userId, string reference)
    {
        if (!Identity().IsMatch(userId) || !Identity().IsMatch(reference)) throw new ArgumentException("密钥引用无效");
        var path = Path.Combine(Root(), userId + "-" + reference + ".secret");
        if (!File.Exists(path) || (File.GetAttributes(path) & FileAttributes.ReparsePoint) != 0) throw new ApplicationFailure("model_configuration_failed", 422, "模型密钥不可用，请重新配置连接。");
        try { return protection.CreateProtector("QuantAssistant.model-key.v1", userId).Unprotect(File.ReadAllText(path)); }
        catch (System.Security.Cryptography.CryptographicException) { throw new ApplicationFailure("model_configuration_failed", 422, "无法解密模型密钥，请恢复密钥备份或重新配置。"); }
    }
}
