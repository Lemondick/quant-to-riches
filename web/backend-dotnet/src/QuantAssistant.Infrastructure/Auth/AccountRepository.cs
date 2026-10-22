using Npgsql;
using QuantAssistant.Core.Abstractions;
using QuantAssistant.Core.Auth;

namespace QuantAssistant.Infrastructure.Auth;

/// <summary>timestamptz 用 DateTime(Kind=Utc)，与 Npgsql 默认映射一致；显式 UTC 满足计划书 §3「存储时区转换必须显式」。</summary>

/// <summary>
/// PostgreSQL 账号仓储：账号、会话、恢复码、审计。所有 SQL 参数化，防注入；
/// 写操作用事务保证原子性（对应计划书 D1「行锁/唯一约束/冲突处理」）。
/// </summary>
public sealed class AccountRepository(string connectionString, string auditHashAlgo = "SHA256") : IAccountRepository
{
    public async Task RecordUsageBatchAsync(IReadOnlyList<UsageDelta> records, CancellationToken cancellation = default)
    {
        if (records.Count == 0) return;
        await using var connection = new NpgsqlConnection(connectionString);
        await connection.OpenAsync(cancellation);
        await using var command = new NpgsqlCommand("""
            INSERT INTO usage_daily(user_id,day,category,requests,errors,duration_ms,last_activity_at)
            SELECT r.user_id,r.day,r.category,r.requests,r.errors,r.duration_ms,r.last_activity_at
            FROM jsonb_to_recordset($1::jsonb) AS r(user_id text,day text,category text,requests integer,errors integer,duration_ms bigint,last_activity_at timestamptz)
            JOIN users u ON u.id=r.user_id
            ON CONFLICT(user_id,day,category) DO UPDATE SET
                requests=usage_daily.requests+EXCLUDED.requests,errors=usage_daily.errors+EXCLUDED.errors,
                duration_ms=usage_daily.duration_ms+EXCLUDED.duration_ms,
                last_activity_at=greatest(usage_daily.last_activity_at,EXCLUDED.last_activity_at)
            """, connection);
        command.Parameters.AddWithValue(System.Text.Json.JsonSerializer.Serialize(records, new System.Text.Json.JsonSerializerOptions { PropertyNamingPolicy = System.Text.Json.JsonNamingPolicy.SnakeCaseLower }));
        await command.ExecuteNonQueryAsync(cancellation);
    }

    public async Task<AuthSession?> ActiveSessionAsync(string tokenHash, DateTime now, CancellationToken cancellation = default)
    {
        await using var connection = new NpgsqlConnection(connectionString);
        await connection.OpenAsync(cancellation);
        await using var command = new NpgsqlCommand("""
            SELECT u.id,u.username,u.password_hash,u.role,u.status,u.created_at,u.last_login_at,s.csrf,s.expires_at,s.session_id,u.data_revision,p.revision
            FROM sessions s JOIN users u ON u.id=s.user_id CROSS JOIN public_display_revision p
            WHERE s.token_hash=$1 AND s.expires_at>$2 AND u.status='active'
            """, connection);
        command.Parameters.AddWithValue(tokenHash);
        command.Parameters.AddWithValue(now);
        await using var reader = await command.ExecuteReaderAsync(cancellation);
        return await reader.ReadAsync(cancellation) ? new(ReadAccount(reader), reader.GetString(7), reader.GetDateTime(8), reader.GetString(9), $"{reader.GetInt64(10)}:{reader.GetInt64(11)}") : null;
    }

    public async Task RecordUsageAsync(string userId, string category, int status, long durationMs, DateTime now, CancellationToken cancellation = default)
    {
        await using var connection = new NpgsqlConnection(connectionString);
        await connection.OpenAsync(cancellation);
        await using var command = new NpgsqlCommand("""
            INSERT INTO usage_daily(user_id,day,category,requests,errors,duration_ms,last_activity_at)
            VALUES($1,$2,$3,1,$4,$5,$6) ON CONFLICT(user_id,day,category) DO UPDATE SET
            requests=usage_daily.requests+1,errors=usage_daily.errors+EXCLUDED.errors,
            duration_ms=usage_daily.duration_ms+EXCLUDED.duration_ms,last_activity_at=EXCLUDED.last_activity_at
            """, connection);
        foreach (var value in new object[] { userId, DateOnly.FromDateTime(now.AddHours(8)).ToString("yyyy-MM-dd", System.Globalization.CultureInfo.InvariantCulture), category, status >= 400 ? 1 : 0, durationMs, now })
            command.Parameters.AddWithValue(value);
        await command.ExecuteNonQueryAsync(cancellation);
    }

    public int CountUsers()
    {
        using var conn = Open();
        using var cmd = conn.CreateCommand();
        cmd.CommandText = "SELECT count(*) FROM users";
        return Convert.ToInt32(cmd.ExecuteScalar());
    }

    public int CountAdmins()
    {
        using var conn = Open();
        using var cmd = conn.CreateCommand();
        cmd.CommandText = "SELECT count(*) FROM users WHERE role = 'admin'";
        return Convert.ToInt32(cmd.ExecuteScalar());
    }

    public bool RegistrationEnabled()
    {
        using var conn = Open();
        using var cmd = conn.CreateCommand();
        cmd.CommandText = "SELECT value FROM site_settings WHERE key = 'registration_enabled'";
        return (string?)cmd.ExecuteScalar() == "1";
    }

    public void SetRegistrationEnabled(bool enabled, string actorId, DateTime now)
    {
        using var conn = Open();
        using var tx = conn.BeginTransaction();
        LockRegistration(conn, tx);
        using (var cmd = conn.CreateCommand())
        {
            cmd.Transaction = tx;
            cmd.CommandText = "UPDATE site_settings SET value = $1 WHERE key = 'registration_enabled'";
            cmd.Parameters.AddWithValue(enabled ? "1" : "0");
            cmd.ExecuteNonQuery();
        }

        InsertAudit(conn, tx, actorId, "registration_toggle", null, enabled ? "enabled" : "disabled", now);
        tx.Commit();
    }

    /// <summary>创建用户；用户名冲突抛 AccountConflict（映射 409）。</summary>
    public void CreateUser(string id, string username, string passwordHash, AccountRole role, DateTime now)
    {
        using var conn = Open();
        using var tx = conn.BeginTransaction();
        LockRegistration(conn, tx);
        using (var check = conn.CreateCommand())
        {
            check.Transaction = tx;
            check.CommandText = "SELECT count(*) FROM users";
            var count = Convert.ToInt32(check.ExecuteScalar());
            if (role == AccountRole.Admin && count != 0) throw new AuthFailure("already_initialized");
            if (count >= AccountRules.AccountLimit)
                throw new AuthFailure("registration_full");
            if (role == AccountRole.User)
            {
                check.CommandText = "SELECT count(*) FROM users WHERE role='admin'";
                if (Convert.ToInt32(check.ExecuteScalar()) == 0) throw new AuthFailure("registration_closed");
                check.CommandText = "SELECT value FROM site_settings WHERE key='registration_enabled'";
                if ((string?)check.ExecuteScalar() != "1") throw new AuthFailure("registration_paused");
            }
        }
        using var cmd = conn.CreateCommand();
        cmd.Transaction = tx;
        cmd.CommandText =
            "INSERT INTO users (id, username, password_hash, role, status, created_at) " +
            "VALUES ($1, $2, $3, $4, 'active', $5)";
        cmd.Parameters.AddWithValue(id);
        cmd.Parameters.AddWithValue(username);
        cmd.Parameters.AddWithValue(passwordHash);
        cmd.Parameters.AddWithValue(ToRoleText(role));
        cmd.Parameters.AddWithValue(now);
        try
        {
            cmd.ExecuteNonQuery();
            tx.Commit();
        }
        catch (PostgresException ex) when (ex.SqlState == PostgresErrorCodes.UniqueViolation)
        {
            throw new AccountConflict(username);
        }
    }

    public Account? FindByUsername(string username)
    {
        using var conn = Open();
        using var cmd = conn.CreateCommand();
        cmd.CommandText =
            "SELECT id, username, password_hash, role, status,created_at,last_login_at FROM users WHERE username = $1";
        cmd.Parameters.AddWithValue(username);
        using var reader = cmd.ExecuteReader();
        return reader.Read() ? ReadAccount(reader) : null;
    }

    public Account? FindById(string userId)
    {
        using var conn = Open();
        using var cmd = conn.CreateCommand();
        cmd.CommandText =
            "SELECT id, username, password_hash, role, status,created_at,last_login_at FROM users WHERE id = $1";
        cmd.Parameters.AddWithValue(userId);
        using var reader = cmd.ExecuteReader();
        return reader.Read() ? ReadAccount(reader) : null;
    }

    private static Account ReadAccount(NpgsqlDataReader reader) =>
        new(
            reader.GetString(0),
            reader.GetString(1),
            reader.GetString(2),
            ParseRole(reader.GetString(3)),
            reader.GetString(4) == "active" ? AccountStatus.Active : AccountStatus.Disabled,
            reader.GetDateTime(5),reader.IsDBNull(6)?null:reader.GetDateTime(6));

    private static string ToRoleText(AccountRole role) => role switch
    {
        AccountRole.Admin => "admin",
        AccountRole.ReadOnly => "readonly",
        _ => "user",
    };

    private static AccountRole ParseRole(string value) => value switch
    {
        "admin" => AccountRole.Admin,
        "readonly" => AccountRole.ReadOnly,
        _ => AccountRole.User,
    };

    /// <summary>F6：管理者调整用户角色（仅 user ↔ readonly；admin 身份不受此路径变更）。</summary>
    public bool SetUserRole(string actorId, string userId, AccountRole role, DateTime now)
    {
        if (role == AccountRole.Admin) throw new ArgumentException("不能通过此接口授予管理者身份。");
        using var conn = Open();
        using var tx = conn.BeginTransaction();
        using (var check = conn.CreateCommand())
        {
            check.Transaction = tx;
            check.CommandText = "SELECT role FROM users WHERE id = $1 FOR UPDATE";
            check.Parameters.AddWithValue(userId);
            var current = check.ExecuteScalar() as string;
            if (current is null)
            {
                tx.Rollback();
                throw new AccountNotFound(userId);
            }
            if (current == "admin" || actorId == userId)
            {
                tx.Rollback();
                throw new AccountConflict("admin_protected");
            }
        }
        using (var update = conn.CreateCommand())
        {
            update.Transaction = tx;
            update.CommandText = "UPDATE users SET role = $1 WHERE id = $2";
            update.Parameters.AddWithValue(ToRoleText(role));
            update.Parameters.AddWithValue(userId);
            update.ExecuteNonQuery();
        }
        InsertAudit(conn, tx, actorId, "user_role_change", userId, ToRoleText(role), now);
        tx.Commit();
        return true;
    }

    public void CreateSession(string tokenHash, string sessionId, string userId, string csrf, string device, DateTime now, DateTime expires)
    {
        using var conn = Open();
        using var tx = conn.BeginTransaction();
        using var cmd = conn.CreateCommand();
        cmd.Transaction = tx;
        // Keep login history and the session atomic, and serialize against disabling the account.
        cmd.CommandText = "UPDATE users SET last_login_at=$2 WHERE id=$1 AND status='active'";
        cmd.Parameters.AddWithValue(userId);
        cmd.Parameters.AddWithValue(now);
        if (cmd.ExecuteNonQuery() != 1) throw new AuthFailure("invalid_credentials");
        cmd.Parameters.Clear();
        cmd.CommandText =
            "INSERT INTO sessions (token_hash, session_id, user_id, csrf, device, created_at, expires_at) " +
            "SELECT $1, $2, id, $4, $5, $6, $7 FROM users WHERE id=$3 AND status='active'";
        cmd.Parameters.AddWithValue(tokenHash);
        cmd.Parameters.AddWithValue(sessionId);
        cmd.Parameters.AddWithValue(userId);
        cmd.Parameters.AddWithValue(csrf);
        cmd.Parameters.AddWithValue(device);
        cmd.Parameters.AddWithValue(now);
        cmd.Parameters.AddWithValue(expires);
        if (cmd.ExecuteNonQuery() != 1) throw new AuthFailure("invalid_credentials");
        cmd.Parameters.Clear();
        cmd.CommandText = "INSERT INTO account_activity(user_id,action,created_at) VALUES($1,'login',$2)";
        cmd.Parameters.AddWithValue(userId);
        cmd.Parameters.AddWithValue(now);
        cmd.ExecuteNonQuery();
        tx.Commit();
    }

    public (string UserId, string Csrf, DateTime Expires)? FindSession(string tokenHash)
    {
        using var conn = Open();
        using var cmd = conn.CreateCommand();
        cmd.CommandText = "SELECT user_id, csrf, expires_at FROM sessions WHERE token_hash = $1";
        cmd.Parameters.AddWithValue(tokenHash);
        using var reader = cmd.ExecuteReader();
        if (!reader.Read())
        {
            return null;
        }

        return (reader.GetString(0), reader.GetString(1), reader.GetFieldValue<DateTime>(2));
    }

    public void RevokeSession(string tokenHash)
    {
        using var conn = Open();
        using var cmd = conn.CreateCommand();
        cmd.CommandText = "DELETE FROM sessions WHERE token_hash = $1";
        cmd.Parameters.AddWithValue(tokenHash);
        cmd.ExecuteNonQuery();
    }

    public void RevokeAllSessions(string userId)
    {
        using var conn = Open();
        using var cmd = conn.CreateCommand();
        cmd.CommandText = "DELETE FROM sessions WHERE user_id = $1";
        cmd.Parameters.AddWithValue(userId);
        cmd.ExecuteNonQuery();
    }

    public IReadOnlyList<AccountSession> ListSessions(string userId, DateTime now)
    {
        using var conn = Open();
        using var cmd = conn.CreateCommand();
        cmd.CommandText = "SELECT session_id,device,created_at,expires_at,token_hash FROM sessions WHERE user_id=$1 AND expires_at>$2 ORDER BY created_at DESC";
        cmd.Parameters.AddWithValue(userId);
        cmd.Parameters.AddWithValue(now);
        using var reader = cmd.ExecuteReader();
        var rows = new List<AccountSession>();
        while (reader.Read()) rows.Add(new(reader.GetString(0), reader.GetString(1), reader.GetDateTime(2), reader.GetDateTime(3), reader.GetString(4)));
        return rows;
    }

    public bool RevokeOwnedSession(string userId, string sessionId)
    {
        using var conn = Open();
        using var cmd = conn.CreateCommand();
        cmd.CommandText = "DELETE FROM sessions WHERE user_id=$1 AND session_id=$2";
        cmd.Parameters.AddWithValue(userId);
        cmd.Parameters.AddWithValue(sessionId);
        return cmd.ExecuteNonQuery() == 1;
    }

    public void RevokeOtherSessions(string userId, string currentTokenHash)
    {
        using var conn = Open();
        using var cmd = conn.CreateCommand();
        cmd.CommandText = "DELETE FROM sessions WHERE user_id=$1 AND token_hash<>$2";
        cmd.Parameters.AddWithValue(userId);
        cmd.Parameters.AddWithValue(currentTokenHash);
        cmd.ExecuteNonQuery();
    }

    public bool ChangePassword(string userId, string currentHash, string replacementHash, DateTime now)
    {
        using var conn = Open();
        using var tx = conn.BeginTransaction();
        using var cmd = conn.CreateCommand();
        cmd.Transaction = tx;
        cmd.CommandText = "UPDATE users SET password_hash=$3 WHERE id=$1 AND password_hash=$2 AND status='active'";
        cmd.Parameters.AddWithValue(userId);
        cmd.Parameters.AddWithValue(currentHash);
        cmd.Parameters.AddWithValue(replacementHash);
        if (cmd.ExecuteNonQuery() != 1) return false;
        cmd.Parameters.Clear();
        cmd.CommandText = "DELETE FROM sessions WHERE user_id=$1";
        cmd.Parameters.AddWithValue(userId);
        cmd.ExecuteNonQuery();
        cmd.Parameters.Clear();
        cmd.CommandText = "INSERT INTO account_activity(user_id,action,created_at) VALUES($1,'password_changed',$2)";
        cmd.Parameters.AddWithValue(userId);
        cmd.Parameters.AddWithValue(now);
        cmd.ExecuteNonQuery();
        tx.Commit();
        return true;
    }

    public void RecordUsage(string userId, string category, int status, long durationMs, DateTime now)
    {
        using var conn = Open();
        using var cmd = conn.CreateCommand();
        cmd.CommandText = """
            INSERT INTO usage_daily(user_id,day,category,requests,errors,duration_ms,last_activity_at)
            VALUES($1,$2,$3,1,$4,$5,$6) ON CONFLICT(user_id,day,category) DO UPDATE SET
            requests=usage_daily.requests+1,errors=usage_daily.errors+EXCLUDED.errors,
            duration_ms=usage_daily.duration_ms+EXCLUDED.duration_ms,last_activity_at=EXCLUDED.last_activity_at
            """;
        cmd.Parameters.AddWithValue(userId);
        cmd.Parameters.AddWithValue(DateOnly.FromDateTime(now.AddHours(8)).ToString("yyyy-MM-dd", System.Globalization.CultureInfo.InvariantCulture));
        cmd.Parameters.AddWithValue(category);
        cmd.Parameters.AddWithValue(status >= 400 ? 1 : 0);
        cmd.Parameters.AddWithValue(durationMs);
        cmd.Parameters.AddWithValue(now);
        cmd.ExecuteNonQuery();
    }

    public void IssueRecoveryToken(string tokenHash, string userId, string issuedBy, DateTime now, DateTime expires)
    {
        using var conn = Open();
        using var cmd = conn.CreateCommand();
        cmd.CommandText =
            "INSERT INTO recovery_tokens (token_hash, user_id, issued_by, created_at, expires_at) " +
            "VALUES ($1, $2, $3, $4, $5) " +
            "ON CONFLICT (user_id) DO UPDATE SET token_hash = EXCLUDED.token_hash, " +
            "issued_by = EXCLUDED.issued_by, created_at = EXCLUDED.created_at, " +
            "expires_at = EXCLUDED.expires_at, consumed_at = NULL";
        cmd.Parameters.AddWithValue(tokenHash);
        cmd.Parameters.AddWithValue(userId);
        cmd.Parameters.AddWithValue(issuedBy);
        cmd.Parameters.AddWithValue(now);
        cmd.Parameters.AddWithValue(expires);
        cmd.ExecuteNonQuery();
    }

    /// <summary>
    /// 原子兑付恢复码：单条 UPDATE ... WHERE 校验未消费+未过期+用户名匹配，
    /// 命中行数为 1 则成功（更新密码+标记消费+撤销全部会话在同一事务）。
    /// 并发下第二次兑付 WHERE 不命中 → 返回 false（对应计划书 §7「并发提交只一次业务效果」）。
    /// </summary>
    public bool RedeemRecovery(string username, string tokenHash, string newPasswordHash, DateTime now)
    {
        using var conn = Open();
        using var tx = conn.BeginTransaction();

        using (var cmd = conn.CreateCommand())
        {
            cmd.Transaction = tx;
            cmd.CommandText =
                "UPDATE recovery_tokens r SET consumed_at = $4 " +
                "FROM users u " +
                "WHERE r.token_hash = $1 AND r.user_id = u.id AND u.username = $2 " +
                "AND r.consumed_at IS NULL AND r.expires_at > $3 " +
                "RETURNING r.user_id";
            cmd.Parameters.AddWithValue(tokenHash);
            cmd.Parameters.AddWithValue(username);
            cmd.Parameters.AddWithValue(now);
            cmd.Parameters.AddWithValue(now);
            var userId = cmd.ExecuteScalar() as string;
            if (userId is null)
            {
                tx.Rollback();
                return false;
            }

            using (var updatePw = conn.CreateCommand())
            {
                updatePw.Transaction = tx;
                updatePw.CommandText = "UPDATE users SET password_hash = $1 WHERE id = $2";
                updatePw.Parameters.AddWithValue(newPasswordHash);
                updatePw.Parameters.AddWithValue(userId);
                updatePw.ExecuteNonQuery();
            }

            using (var revoke = conn.CreateCommand())
            {
                revoke.Transaction = tx;
                revoke.CommandText = "DELETE FROM sessions WHERE user_id = $1";
                revoke.Parameters.AddWithValue(userId);
                revoke.ExecuteNonQuery();
            }
        }

        tx.Commit();
        return true;
    }

    public IReadOnlyList<Account> ListUsers(string? search)
    {
        using var conn = Open();
        using var cmd = conn.CreateCommand();
        cmd.CommandText = string.IsNullOrWhiteSpace(search)
            ? "SELECT id, username, password_hash, role, status, created_at, last_login_at FROM users ORDER BY username"
            : "SELECT id, username, password_hash, role, status, created_at, last_login_at FROM users WHERE username ILIKE $1 ORDER BY username";
        if (!string.IsNullOrWhiteSpace(search))
        {
            cmd.Parameters.AddWithValue($"{search}%");
        }

        var result = new List<Account>();
        using var reader = cmd.ExecuteReader();
        while (reader.Read())
        {
            result.Add(ReadAccount(reader));
        }

        return result;
    }

    /// <summary>
    /// 停用/恢复用户：停用即撤销其全部会话，且不允许停用唯一管理员（admin_protected）。
    /// 状态变更 + 会话撤销 + 审计在同一事务原子完成（计划书 §7「并发提交只一次业务效果」）。
    /// </summary>
    public bool SetUserStatus(string actorId, string userId, AccountStatus status, DateTime now)
    {
        using var conn = Open();
        using var tx = conn.BeginTransaction();
        using (var check = conn.CreateCommand())
        {
            check.Transaction = tx;
            check.CommandText = "SELECT role FROM users WHERE id = $1 FOR UPDATE";
            check.Parameters.AddWithValue(userId);
            var role = check.ExecuteScalar() as string;
            if (role is null)
            {
                tx.Rollback();
                throw new AccountNotFound(userId);
            }

            if (role == "admin" && status == AccountStatus.Disabled)
            {
                tx.Rollback();
                throw new AccountConflict("admin_protected");
            }
        }

        using (var update = conn.CreateCommand())
        {
            update.Transaction = tx;
            update.CommandText = "UPDATE users SET status = $1 WHERE id = $2";
            update.Parameters.AddWithValue(status == AccountStatus.Active ? "active" : "disabled");
            update.Parameters.AddWithValue(userId);
            update.ExecuteNonQuery();
        }

        if (status == AccountStatus.Disabled)
        {
            using var revoke = conn.CreateCommand();
            revoke.Transaction = tx;
            revoke.CommandText = "DELETE FROM sessions WHERE user_id = $1";
            revoke.Parameters.AddWithValue(userId);
            revoke.ExecuteNonQuery();
        }

        InsertAudit(conn, tx, actorId, "status", userId, status == AccountStatus.Active ? "active" : "disabled", now);
        tx.Commit();
        return true;
    }

    public void AdminRevokeSessions(string actorId, string userId, DateTime now)
    {
        using var conn = Open();
        using var tx = conn.BeginTransaction();
        using (var revoke = conn.CreateCommand())
        {
            revoke.Transaction = tx;
            revoke.CommandText = "DELETE FROM sessions WHERE user_id = $1";
            revoke.Parameters.AddWithValue(userId);
            revoke.ExecuteNonQuery();
        }

        InsertAudit(conn, tx, actorId, "revoke_sessions", userId, "forced_logout", now);
        tx.Commit();
    }

    public (IReadOnlyList<AuditEntry> Items, int Total) ListAudit(int page, int limit)
    {
        using var conn = Open();
        using var count = conn.CreateCommand();
        count.CommandText = "SELECT count(*) FROM admin_audit";
        var total = Convert.ToInt32(count.ExecuteScalar());

        using var cmd = conn.CreateCommand();
        cmd.CommandText = "SELECT actor_id, action, target_id, detail, created_at FROM admin_audit ORDER BY created_at DESC OFFSET $1 LIMIT $2";
        cmd.Parameters.AddWithValue((page - 1) * limit);
        cmd.Parameters.AddWithValue(limit);

        var items = new List<AuditEntry>();
        using var reader = cmd.ExecuteReader();
        while (reader.Read())
        {
            items.Add(new AuditEntry(reader.GetString(0), reader.GetString(1), reader.IsDBNull(2) ? null : reader.GetString(2), reader.GetString(3), reader.GetFieldValue<DateTime>(4)));
        }

        return (items, total);
    }

    // ===== batch2-11：双重验证（TOTP + 恢复码 + 登录挑战）=====

    public (string? Secret, bool Enabled) GetTotp(string userId)
    {
        using var conn = Open();
        using var cmd = conn.CreateCommand();
        cmd.CommandText = "SELECT totp_secret, totp_enabled FROM users WHERE id=$1";
        cmd.Parameters.AddWithValue(userId);
        using var reader = cmd.ExecuteReader();
        return reader.Read() ? (reader.IsDBNull(0) ? null : reader.GetString(0), reader.GetBoolean(1)) : (null, false);
    }

    public void SetTotpSecret(string userId, string secret)
    {
        using var conn = Open();
        using var cmd = conn.CreateCommand();
        cmd.CommandText = "UPDATE users SET totp_secret=$2 WHERE id=$1";
        cmd.Parameters.AddWithValue(userId);
        cmd.Parameters.AddWithValue(secret);
        cmd.ExecuteNonQuery();
    }

    public void SetTotpEnabled(string userId, bool enabled)
    {
        using var conn = Open();
        using var cmd = conn.CreateCommand();
        cmd.CommandText = "UPDATE users SET totp_enabled=$2 WHERE id=$1";
        cmd.Parameters.AddWithValue(userId);
        cmd.Parameters.AddWithValue(enabled);
        cmd.ExecuteNonQuery();
    }

    public void ClearTotp(string userId)
    {
        using var conn = Open();
        using var tx = conn.BeginTransaction();
        using var cmd = conn.CreateCommand();
        cmd.Transaction = tx;
        cmd.CommandText = "UPDATE users SET totp_secret=NULL, totp_enabled=FALSE WHERE id=$1";
        cmd.Parameters.AddWithValue(userId);
        cmd.ExecuteNonQuery();
        cmd.Parameters.Clear();
        cmd.CommandText = "DELETE FROM user_totp_recovery WHERE user_id=$1";
        cmd.Parameters.AddWithValue(userId);
        cmd.ExecuteNonQuery();
        cmd.Parameters.Clear();
        cmd.CommandText = "DELETE FROM totp_login_challenges WHERE user_id=$1";
        cmd.Parameters.AddWithValue(userId);
        cmd.ExecuteNonQuery();
        tx.Commit();
    }

    public void ReplaceRecoveryCodes(string userId, IReadOnlyList<string> codeHashes, DateTime now)
    {
        using var conn = Open();
        using var tx = conn.BeginTransaction();
        using var cmd = conn.CreateCommand();
        cmd.Transaction = tx;
        cmd.CommandText = "DELETE FROM user_totp_recovery WHERE user_id=$1";
        cmd.Parameters.AddWithValue(userId);
        cmd.ExecuteNonQuery();
        cmd.Parameters.Clear();
        cmd.CommandText = "INSERT INTO user_totp_recovery(user_id,code_hash,created_at) VALUES($1,$2,$3)";
        foreach (var hash in codeHashes)
        {
            cmd.Parameters.AddWithValue(userId);
            cmd.Parameters.AddWithValue(hash);
            cmd.Parameters.AddWithValue(now);
            cmd.ExecuteNonQuery();
            cmd.Parameters.Clear();
        }
        tx.Commit();
    }

    public IReadOnlyList<(long Id, string CodeHash)> ListUnusedRecoveryCodes(string userId)
    {
        using var conn = Open();
        using var cmd = conn.CreateCommand();
        cmd.CommandText = "SELECT id,code_hash FROM user_totp_recovery WHERE user_id=$1 AND used_at IS NULL ORDER BY id";
        cmd.Parameters.AddWithValue(userId);
        using var reader = cmd.ExecuteReader();
        var list = new List<(long, string)>();
        while (reader.Read()) list.Add((reader.GetInt64(0), reader.GetString(1)));
        return list;
    }

    public void MarkRecoveryCodeUsed(long id)
    {
        using var conn = Open();
        using var cmd = conn.CreateCommand();
        cmd.CommandText = "UPDATE user_totp_recovery SET used_at=now() WHERE id=$1 AND used_at IS NULL";
        cmd.Parameters.AddWithValue(id);
        cmd.ExecuteNonQuery();
    }

    public void CreateTotpChallenge(string tokenHash, string userId, DateTime now, DateTime expires)
    {
        using var conn = Open();
        using var cmd = conn.CreateCommand();
        cmd.CommandText = "WITH expired AS (DELETE FROM totp_login_challenges WHERE expires_at<=$2) INSERT INTO totp_login_challenges(token_hash,user_id,created_at,expires_at) VALUES($1,$3,$2,$4)";
        cmd.Parameters.AddWithValue(tokenHash);
        cmd.Parameters.AddWithValue(now);
        cmd.Parameters.AddWithValue(userId);
        cmd.Parameters.AddWithValue(expires);
        cmd.ExecuteNonQuery();
    }

    public string? ConsumeTotpChallenge(string tokenHash, DateTime now)
    {
        using var conn = Open();
        using var cmd = conn.CreateCommand();
        cmd.CommandText = "DELETE FROM totp_login_challenges WHERE token_hash=$1 AND expires_at>$2 RETURNING user_id";
        cmd.Parameters.AddWithValue(tokenHash);
        cmd.Parameters.AddWithValue(now);
        return cmd.ExecuteScalar() as string;
    }

    private NpgsqlConnection Open()
    {
        var conn = new NpgsqlConnection(connectionString);
        try { conn.Open(); }
        catch { conn.Dispose(); throw; }
        return conn;
    }

    private static void LockRegistration(NpgsqlConnection conn, NpgsqlTransaction tx)
    {
        using var gate = conn.CreateCommand();
        gate.Transaction = tx;
        gate.CommandText = "SELECT pg_advisory_xact_lock(hashtextextended('auth:registration', 0))";
        gate.ExecuteNonQuery();
    }

    /// <summary>batch2-12：admin_audit 统一走哈希链写入（WriteRaw 按列存在自适应；detail 保持原有 {value} 包裹形状）。</summary>
    private void InsertAudit(NpgsqlConnection conn, NpgsqlTransaction tx, string actorId, string action, string? targetId, string detail, DateTime now)
    {
        AdminAuditRepository.WriteRaw(conn, tx, auditHashAlgo, actorId, action, targetId,
            System.Text.Json.JsonSerializer.Serialize(new { value = detail }), now);
    }
}
