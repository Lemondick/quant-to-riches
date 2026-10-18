namespace QuantAssistant.Core.Abstractions;

/// <summary>
/// 密码哈希契约：Argon2id，参数与旧站点一致（t=2, m=19456 KiB, p=1, hash=32B）。
/// 禁止自行实现密码算法；实现见 Infrastructure 的 Argon2PasswordHasher。
/// </summary>
public interface IPasswordHasher
{
    /// <summary>对明文密码生成 PHC 格式哈希（与旧站点存储格式兼容）。</summary>
    string Hash(string password);

    /// <summary>校验明文密码是否匹配已存哈希；任何非法/不匹配输入返回 false，不抛异常。</summary>
    bool Verify(string hashed, string password);
}
