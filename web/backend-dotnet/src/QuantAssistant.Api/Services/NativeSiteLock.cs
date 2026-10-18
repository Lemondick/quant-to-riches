using System.Runtime.InteropServices;

namespace QuantAssistant.Api.Services;

public sealed class NativeSiteLock:IDisposable
{
    private readonly FileStream? handle;
    public bool Held=>handle is not null;
    public NativeSiteLock(IConfiguration configuration)
    {
        if(configuration["QUANT_SITE_ROOT"] is not {Length:>0} root)return;
        NativePaths.RejectReparseAncestors(root);
        var path=Path.Combine(Path.GetFullPath(root),".site.lock");
        if(File.Exists(path)&&(File.GetAttributes(path)&FileAttributes.ReparsePoint)!=0)throw new IOException("Site lock cannot be a symbolic link.");
        handle=new FileStream(path,FileMode.OpenOrCreate,FileAccess.ReadWrite,FileShare.None);
        if(OperatingSystem.IsLinux()&&flock(handle.SafeFileHandle.DangerousGetHandle().ToInt32(),6)!=0)
        {handle.Dispose();throw new IOException("The site is already locked by another service or archive operation.");}
    }
    [DllImport("libc",SetLastError=true)]
    private static extern int flock(int descriptor,int operation);
    public void Dispose()=>handle?.Dispose();
}
