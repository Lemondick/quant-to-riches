namespace QuantAssistant.Api.Services;

internal static class NativePaths
{
    public static void RejectReparseAncestors(string path)
    {
        for(DirectoryInfo? directory=new(Path.GetFullPath(path));directory is not null;directory=directory.Parent)
            if(directory.Exists && (directory.Attributes&FileAttributes.ReparsePoint)!=0)
                throw new IOException("Native data and credential paths cannot use symbolic links.");
    }
}
