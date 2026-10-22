using Microsoft.Extensions.Logging.Abstractions;
using Npgsql;
using QuantAssistant.Api.Configuration;
using QuantAssistant.Api.Services;
using QuantAssistant.Core.Abstractions;
using QuantAssistant.Core.Auth;
using QuantAssistant.Infrastructure.Auth;
using QuantAssistant.Infrastructure.Db;
using QuantAssistant.Infrastructure.Security;
using Xunit;

namespace QuantAssistant.Api.Tests;

public sealed class SecondReviewRegressionTests
{
    private sealed class ConcurrentHasher(Barrier barrier) : IPasswordHasher
    {
        private readonly Argon2PasswordHasher inner = new();
        public string Hash(string password) => inner.Hash(password);
        public bool Verify(string hashed, string password)
        {
            var valid = inner.Verify(hashed, password);
            if (valid && !barrier.SignalAndWait(TimeSpan.FromSeconds(15)))
                throw new TimeoutException("Both recovery attempts must reach verification.");
            return valid;
        }
    }

    [PgFact]
    public async Task RecoveryCode_TwoPendingChallengesCanConsumeItOnlyOnce()
    {
        using var user = new PgTestUser();
        var repository = new AccountRepository(user.Connection);
        repository.SetTotpSecret(user.Id, Totp.GenerateSecret());
        repository.SetTotpEnabled(user.Id, true);
        const string recovery = "ABCD-EFGH";
        repository.ReplaceRecoveryCodes(user.Id, [new Argon2PasswordHasher().Hash(recovery)], DateTime.UtcNow);
        using var barrier = new Barrier(2);
        var auth = new AuthService(repository, new ConcurrentHasher(barrier), TimeProvider.System);
        var first = auth.BeginTotpChallenge(user.Id);
        var second = auth.BeginTotpChallenge(user.Id);
        var results = await Task.WhenAll(
            Task.Run(() => auth.VerifyTotpChallenge(first, recovery)),
            Task.Run(() => auth.VerifyTotpChallenge(second, recovery)));
        Assert.Single(results, account => account is not null);
        Assert.Empty(repository.ListUnusedRecoveryCodes(user.Id));
    }

    [PgFact]
    public void ReportDispatch_VisitsNotificationsThatCommitBehindTheLastSentTimestamp()
    {
        using var user = new PgTestUser();
        using var other = new PgTestUser();
        var cursor = user.Query("SELECT value FROM site_settings WHERE key='email.dispatch.report_cursor'") as string;
        using var late = new NpgsqlConnection(user.Connection);
        late.Open();
        using var transaction = late.BeginTransaction();
        try
        {
            user.Execute("INSERT INTO site_settings(key,value) VALUES('email.dispatch.report_cursor',$1) ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value", DateTime.UtcNow.AddDays(-1).ToString("O"));
            user.Execute("INSERT INTO market_advisor_reports(user_id,report_id,report_date,kind,status,created_at,updated_at) VALUES($1,'late',current_date,'morning','completed',now(),now())", user.Id);
            other.Execute("INSERT INTO market_advisor_reports(user_id,report_id,report_date,kind,status,created_at,updated_at) VALUES($1,'early',current_date,'morning','completed',now(),now())", other.Id);
            using (var command = new NpgsqlCommand("INSERT INTO advisor_notifications(user_id,id,kind,report_id,status,created_at) VALUES($1,'late','morning','late','completed',now())", late, transaction))
            {
                command.Parameters.AddWithValue(user.Id);
                command.ExecuteNonQuery();
            }
            other.Execute("INSERT INTO advisor_notifications(user_id,id,kind,report_id,status,created_at) VALUES($1,'early','morning','early','completed',clock_timestamp())", other.Id);
            var service = new EmailDeliveryService(new AccountDatabase(user.Connection), new QuantOptions(), NullLogger<EmailDeliveryService>.Instance);
            service.CheckDispatch();
            Assert.Equal(1L, other.Query("SELECT count(*) FROM email_send_log WHERE user_id=$1", other.Id));
            transaction.Commit();
            service.CheckDispatch();
            Assert.Equal(1L, user.Query("SELECT count(*) FROM email_send_log WHERE user_id=$1", user.Id));
            service.CheckDispatch();
            Assert.Equal(1L, user.Query("SELECT count(*) FROM email_send_log WHERE user_id=$1", user.Id));
            Assert.Equal(1L, other.Query("SELECT count(*) FROM email_send_log WHERE user_id=$1", other.Id));
        }
        finally
        {
            transaction.Dispose();
            foreach (var owner in new[] { user, other })
            {
                owner.Execute("DELETE FROM advisor_notifications WHERE user_id=$1", owner.Id);
                owner.Execute("DELETE FROM market_advisor_reports WHERE user_id=$1", owner.Id);
                owner.Execute("DELETE FROM email_send_log WHERE user_id=$1", owner.Id);
                owner.Execute("DELETE FROM site_settings WHERE key LIKE $1", "email.dispatch.report_sent:" + owner.Id + ":%");
            }
            if (cursor is null) user.Execute("DELETE FROM site_settings WHERE key='email.dispatch.report_cursor'");
            else user.Execute("UPDATE site_settings SET value=$1 WHERE key='email.dispatch.report_cursor'", cursor);
        }
    }
}
