using System.Text;

namespace ClassMap;

/// <summary>
/// Persistance sur la clé, à côté du .exe (dossier « donnees »). Rien n'est écrit dans Windows.
/// - écriture atomique : fichier .tmp vidé sur le disque (FlushFileBuffers) puis remplacement,
///   l'ancienne version devient .bak ;
/// - écritures faites sur un fil dédié, regroupées (seule la dernière version compte) ;
/// - si la clé est retirée ou protégée, les données restent en mémoire et l'écriture est retentée ;
/// - une copie par jour dans « sauvegardes » (14 derniers jours).
/// </summary>
public sealed class Storage : IDisposable
{
    const string DataFile = "classmap.json", JournalFile = "journal.csv", BackupDir = "sauvegardes";
    const int KeepBackups = 14;

    public string Dir { get; }
    public string DataPath => Path.Combine(Dir, DataFile);
    public string JournalPath => Path.Combine(Dir, JournalFile);

    /// <summary>Appelé (depuis le fil d'écriture) quand l'état d'enregistrement change : null = OK.</summary>
    public event Action<string?>? StatusChanged;
    public string? Error { get; private set; }
    public DateTime LastSaved { get; private set; }

    readonly object _lock = new(), _writeLock = new();
    readonly List<string> _journal = new();
    readonly AutoResetEvent _signal = new(false);
    readonly Thread _thread;
    byte[]? _pending;
    volatile bool _stop;
    string? _backupDay;

    public Storage(string dir)
    {
        Dir = Path.GetFullPath(dir);
        _thread = new Thread(Loop) { IsBackground = true, Name = "ClassMap-save" };
        _thread.Start();
    }

    public AppData Load(out string? notice)
    {
        notice = null;
        var candidates = new List<string> { DataPath, DataPath + ".tmp", DataPath + ".bak" };
        try
        {
            var bdir = Path.Combine(Dir, BackupDir);
            if (Directory.Exists(bdir))
                candidates.AddRange(Directory.GetFiles(bdir, "classmap-*.json").OrderDescending());
        }
        catch { }

        bool mainBroken = false;
        foreach (var path in candidates)
        {
            if (!File.Exists(path)) continue;
            AppData data;
            try { data = Sanitize(Json.Parse(File.ReadAllBytes(path))); }
            catch
            {
                if (path == DataPath) mainBroken = true;
                continue;
            }
            if (path != DataPath)
            {
                KeepBroken(mainBroken);
                notice = $"Fichier principal illisible : données récupérées depuis {Path.GetFileName(path)}.";
                Save(data); // réécrit un fichier principal sain
            }
            return data;
        }
        if (mainBroken)
        {
            KeepBroken(true);
            notice = "Le fichier de données était illisible et aucune sauvegarde n'a pu être relue. Il a été mis de côté.";
        }
        return Sanitize(new AppData());
    }

    /// <summary>Le fichier abîmé est gardé de côté : il ne sera jamais écrasé.</summary>
    void KeepBroken(bool broken)
    {
        if (!broken) return;
        try { File.Copy(DataPath, Path.Combine(Dir, $"classmap.corrompu-{DateTime.Now:yyyyMMdd-HHmmss}.json")); } catch { }
    }

    public static AppData Sanitize(AppData d)
    {
        d.Settings ??= new Settings();
        d.Classes ??= new();
        d.Timetable ??= new();
        d.Classes.RemoveAll(c => c == null);
        foreach (var c in d.Classes)
        {
            c.Id ??= Ids.New();
            c.Name ??= "";
            c.Students ??= new();
            c.Students.RemoveAll(s => s == null);
            foreach (var s in c.Students) { s.Id ??= Ids.New(); s.Name ??= ""; s.Score = Math.Max(0, s.Score); }
            Placement.Normalize(c);
        }
        d.Timetable.RemoveAll(t => t == null || t.End <= t.Start || d.Classes.All(c => c.Id != t.ClassId));
        var st = d.Settings;
        st.Opacity = Math.Clamp(st.Opacity, 0.15, 1);
        st.IdleOpacity = Math.Clamp(st.IdleOpacity, 0.1, 1);
        st.BackgroundOpacity = Math.Clamp(st.BackgroundOpacity, 0, 0.9);
        st.TextScale = Math.Clamp(st.TextScale, 0.5, 2);
        return d;
    }

    /// <summary>Programme l'enregistrement (non bloquant). La sérialisation se fait ici, sur le fil
    /// appelant, pour figer un état cohérent.</summary>
    public void Save(AppData d)
    {
        var bytes = Json.Serialize(d);
        lock (_lock) _pending = bytes;
        _signal.Set();
    }

    public void Journal(string className, string student, string action, int? score)
    {
        var line = string.Join(';', DateTime.Now.ToString("yyyy-MM-dd"), DateTime.Now.ToString("HH:mm:ss"),
            Csv(className), Csv(student), Csv(action), score?.ToString() ?? "");
        lock (_lock)
        {
            _journal.Add(line);
            if (_journal.Count > 5000) _journal.RemoveAt(0);
        }
        _signal.Set();
    }

    static string Csv(string s) => s.IndexOfAny(new[] { ';', '"', '\n' }) >= 0 ? "\"" + s.Replace("\"", "\"\"") + "\"" : s;

    /// <summary>Écrit tout ce qui est en attente, tout de suite. Renvoie false si la clé est inaccessible.</summary>
    public bool Flush()
    {
        WritePending();
        return Error == null;
    }

    void Loop()
    {
        while (!_stop)
        {
            _signal.WaitOne(Error != null ? 3000 : Timeout.Infinite);
            if (!_stop) WritePending();
        }
    }

    void WritePending()
    {
        lock (_writeLock)
        {
            byte[]? data;
            string[] lines;
            lock (_lock)
            {
                data = _pending;
                _pending = null;
                lines = _journal.ToArray();
                _journal.Clear();
            }
            if (data == null && lines.Length == 0) return;

            string? error = null;
            try
            {
                Directory.CreateDirectory(Dir);
                if (data != null)
                {
                    DailyBackup();
                    AtomicWrite(DataPath, data);
                    LastSaved = DateTime.Now;
                }
            }
            catch (Exception e)
            {
                error = e is UnauthorizedAccessException || (e.HResult & 0xFFFF) == 19 // ERROR_WRITE_PROTECT
                    ? "Clé protégée en écriture : modifications non enregistrées."
                    : "Clé inaccessible : modifications non enregistrées (nouvel essai automatique).";
                lock (_lock) _pending ??= data; // on ne remplace pas une version plus récente
            }
            if (lines.Length > 0)
            {
                try { AppendJournal(lines); }
                catch { lock (_lock) _journal.InsertRange(0, lines); } // journal ouvert dans Excel, etc.
            }
            if (error != Error)
            {
                Error = error;
                StatusChanged?.Invoke(error);
            }
        }
    }

    static void AtomicWrite(string path, byte[] data)
    {
        var tmp = path + ".tmp";
        using (var fs = new FileStream(tmp, FileMode.Create, FileAccess.Write, FileShare.None, 4096, FileOptions.WriteThrough))
        {
            fs.Write(data);
            fs.Flush(true);
        }
        if (!File.Exists(path)) { File.Move(tmp, path); return; }
        try
        {
            File.Replace(tmp, path, path + ".bak", ignoreMetadataErrors: true);
        }
        catch (IOException) when (File.Exists(tmp))
        {
            // Certains systèmes de fichiers refusent ReplaceFile : repli copie + renommage.
            File.Copy(path, path + ".bak", true);
            File.Move(tmp, path, true);
        }
    }

    void DailyBackup()
    {
        var today = DateTime.Now.ToString("yyyy-MM-dd");
        if (_backupDay == today) return;
        if (File.Exists(DataPath))
        {
            var dir = Path.Combine(Dir, BackupDir);
            Directory.CreateDirectory(dir);
            var target = Path.Combine(dir, $"classmap-{today}.json");
            if (!File.Exists(target)) File.Copy(DataPath, target);
            foreach (var old in Directory.GetFiles(dir, "classmap-*.json").OrderDescending().Skip(KeepBackups))
                File.Delete(old);
        }
        _backupDay = today;
    }

    void AppendJournal(string[] lines)
    {
        bool isNew = !File.Exists(JournalPath);
        using var fs = new FileStream(JournalPath, FileMode.Append, FileAccess.Write, FileShare.Read, 4096, FileOptions.WriteThrough);
        var sb = new StringBuilder();
        if (isNew) sb.Append('﻿').Append("Date;Heure;Classe;Élève;Action;Score\r\n"); // BOM : accents corrects dans Excel
        foreach (var l in lines) sb.Append(l).Append("\r\n");
        fs.Write(Encoding.UTF8.GetBytes(sb.ToString()));
        fs.Flush(true);
    }

    public void Dispose()
    {
        _stop = true;
        _signal.Set();
        _thread.Join(2000);
        WritePending();
    }
}
