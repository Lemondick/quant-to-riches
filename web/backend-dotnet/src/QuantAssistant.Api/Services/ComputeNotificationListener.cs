using System.Text.Json;
using Npgsql;
using QuantAssistant.Infrastructure.Jobs;

namespace QuantAssistant.Api.Services;

public sealed class ComputeNotificationListener(string connectionString, ILogger<ComputeNotificationListener> logger) : BackgroundService
{
    private readonly TaskCompletionSource ready = new(TaskCreationOptions.RunContinuationsAsynchronously);
    public Task Ready => ready.Task;
    protected override async Task ExecuteAsync(CancellationToken stoppingToken)
    {
        while (!stoppingToken.IsCancellationRequested)
        {
            try
            {
                var options = new NpgsqlConnectionStringBuilder(connectionString) { Pooling = false, KeepAlive = 30 };
                await using var connection = new NpgsqlConnection(options.ConnectionString);
                await connection.OpenAsync(stoppingToken);
                connection.Notification += (_, notification) =>
                {
                    if (notification.Channel == "quant_compute_queued") ComputeJobNotifications.Local.PublishQueue();
                    else
                    {
                        using var payload = JsonDocument.Parse(notification.Payload);
                        ComputeJobNotifications.Local.Publish(payload.RootElement.GetProperty("user_id").GetString()!, payload.RootElement.GetProperty("job_id").GetString()!);
                    }
                };
                await using var listen = new NpgsqlCommand("LISTEN quant_compute_completed; LISTEN quant_compute_queued", connection);
                await listen.ExecuteNonQueryAsync(stoppingToken);
                ready.TrySetResult();
                ComputeJobNotifications.Local.Reconnected();
                while (!stoppingToken.IsCancellationRequested) await connection.WaitAsync(stoppingToken);
            }
            catch (OperationCanceledException) when (stoppingToken.IsCancellationRequested) { break; }
            catch (Exception error) when (error is NpgsqlException or IOException or JsonException)
            {
                logger.LogWarning("Compute notification listener reconnecting ({ErrorType})", error.GetType().Name);
                await Task.Delay(TimeSpan.FromSeconds(5), stoppingToken);
            }
        }
    }
}
