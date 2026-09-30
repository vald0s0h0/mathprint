using System.Text;
using Avalonia;

namespace ClassMap;

public static class Program
{
    public static AppState? State { get; private set; }

    [STAThread]
    public static int Main(string[] args)
    {
        if (args.Contains("--selftest")) return SelfTest.Run();

        // Données à côté du .exe (sur la clé). --data <dossier> pour les essais.
        int i = Array.IndexOf(args, "--data");
        string dir = i >= 0 && i + 1 < args.Length ? args[i + 1] : Path.Combine(AppContext.BaseDirectory, "donnees");

        // Une seule instance par dossier de données (deux instances s'écraseraient leurs fichiers).
        using var mutex = new Mutex(true, "Local\\ClassMap-" + Fnv(Path.GetFullPath(dir).ToUpperInvariant()), out bool first);
        if (!first) return 0;

        using var store = new Storage(dir);
        State = new AppState(store.Load(out var notice), store) { StartupNotice = notice };
        BuildAvaloniaApp().StartWithClassicDesktopLifetime(args);
        store.Flush();
        return 0;
    }

    public static AppBuilder BuildAvaloniaApp() => AppBuilder.Configure<App>().UsePlatformDetect();

    static string Fnv(string s)
    {
        ulong h = 14695981039346656037;
        foreach (var b in Encoding.UTF8.GetBytes(s)) { h ^= b; h *= 1099511628211; }
        return h.ToString("x16");
    }
}
