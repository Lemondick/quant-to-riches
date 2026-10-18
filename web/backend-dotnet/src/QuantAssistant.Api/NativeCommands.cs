using QuantAssistant.Core.Auth;
using QuantAssistant.Infrastructure.Auth;
using QuantAssistant.Infrastructure.Security;

namespace QuantAssistant.Api;

internal static class NativeCommands
{
    public static bool Execute(string[] args, string connectionString)
    {
        if (args is not ["--init-admin", var username]) return false;
        var password = Environment.GetEnvironmentVariable("QUANT_INIT_ADMIN_PASSWORD");
        if (string.IsNullOrEmpty(password))
        {
            Console.Write("Administrator password: ");
            if (Console.IsInputRedirected) password = Console.ReadLine();
            else
            {
                var value = new System.Text.StringBuilder();
                while (true)
                {
                    var key = Console.ReadKey(true);
                    if (key.Key == ConsoleKey.Enter) break;
                    if (key.Key == ConsoleKey.Backspace) { if (value.Length > 0) value.Length--; }
                    else if (!char.IsControl(key.KeyChar)) value.Append(key.KeyChar);
                }
                password = value.ToString();
                Console.WriteLine();
            }
        }
        try
        {
            new AuthService(new AccountRepository(connectionString), new Argon2PasswordHasher(), TimeProvider.System).InitializeAdmin(username, password ?? "");
            Console.WriteLine("admin_initialized");
        }
        catch (AuthFailure error)
        {
            Console.Error.WriteLine(error.Code);
            Environment.ExitCode = 1;
        }
        return true;
    }
}
