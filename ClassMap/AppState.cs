using System.Text.Json;

namespace ClassMap;

[Flags]
public enum Change { Scores = 1, Layout = 2, Structure = 4, Selection = 8, Settings = 16 }

/// <summary>État de l'application : données, classe affichée, annuler/rétablir, emploi du temps.</summary>
public sealed class AppState
{
    const int MaxUndo = 150;

    sealed record Entry(string Snapshot, string Label, Journaled? J);
    sealed record Journaled(string ClassId, string StudentId, string Action);

    readonly List<Entry> _undo = new(), _redo = new();
    string? _lastScheduled;
    bool _scheduleChecked;

    public AppData Data { get; private set; }
    public Storage Store { get; }
    public string? StartupNotice { get; init; }
    public event Action<Change>? Changed;

    public AppState(AppData data, Storage store)
    {
        Data = data;
        Store = store;
    }

    public Settings Settings => Data.Settings;
    public bool CanUndo => _undo.Count > 0;
    public bool CanRedo => _redo.Count > 0;
    public string? UndoLabel => _undo.Count > 0 ? _undo[^1].Label : null;
    public string? RedoLabel => _redo.Count > 0 ? _redo[^1].Label : null;

    public ClassGroup? CurrentClass => Find(Data.CurrentClassId) ?? Data.Classes.FirstOrDefault();
    public ClassGroup? Find(string? id) => id == null ? null : Data.Classes.Find(c => c.Id == id);

    /// <summary>Toute modification des classes / élèves / emploi du temps passe ici :
    /// instantané pour annuler, enregistrement immédiat, notification.</summary>
    public void Commit(string label, Action mutate, Change change, Student? journalStudent = null, string? journalAction = null)
    {
        var before = Snapshot();
        mutate();
        Journaled? j = null;
        if (journalStudent != null && journalAction != null && CurrentClass is { } g)
        {
            j = new Journaled(g.Id, journalStudent.Id, journalAction);
            Store.Journal(g.Name, journalStudent.Name, journalAction, journalStudent.Score);
        }
        _undo.Add(new Entry(before, label, j));
        if (_undo.Count > MaxUndo) _undo.RemoveAt(0);
        _redo.Clear();
        Save();
        Changed?.Invoke(change);
    }

    public void AddScore(Student s, int delta)
    {
        int next = Math.Max(0, s.Score + delta);
        if (next == s.Score) return;
        Commit($"{(delta > 0 ? "+1" : "−1")} {s.Name}", () => s.Score = next, Change.Scores, s, delta > 0 ? "+1" : "-1");
    }

    public void ResetScore(Student s)
    {
        if (s.Score == 0) return;
        Commit($"Remise à 0 {s.Name}", () => s.Score = 0, Change.Scores, s, "Remise à 0");
    }

    public void ResetClass(ClassGroup g)
    {
        Commit($"Remise à 0 de {g.Name}", () => g.Students.ForEach(s => s.Score = 0), Change.Scores);
        Store.Journal(g.Name, "(toute la classe)", "Remise à 0", 0);
    }

    public void Undo() => Swap(_undo, _redo, "Annulé");
    public void Redo() => Swap(_redo, _undo, "Rétabli");

    void Swap(List<Entry> from, List<Entry> to, string verb)
    {
        if (from.Count == 0) return;
        var e = from[^1];
        from.RemoveAt(from.Count - 1);
        to.Add(e with { Snapshot = Snapshot() });
        var s = JsonSerializer.Deserialize(e.Snapshot, Json.Default.UndoState)!;
        Data.Classes = s.Classes;
        Data.Timetable = s.Timetable;
        if (e.J is { } j && Find(j.ClassId) is { } g && g.Students.Find(x => x.Id == j.StudentId) is { } st)
            Store.Journal(g.Name, st.Name, $"{verb} ({j.Action})", st.Score);
        Save();
        Changed?.Invoke(Change.Structure | Change.Scores | Change.Layout);
    }

    string Snapshot() =>
        JsonSerializer.Serialize(new UndoState { Classes = Data.Classes, Timetable = Data.Timetable }, Json.Default.UndoState);

    public void Save() => Store.Save(Data);

    public void SettingsChanged()
    {
        Save();
        Changed?.Invoke(Change.Settings);
    }

    public void SelectClass(string id)
    {
        if (Data.CurrentClassId == id) return;
        Data.CurrentClassId = id;
        Save();
        Changed?.Invoke(Change.Selection);
    }

    /// <summary>Remplace les données (import d'une sauvegarde). Annulable.</summary>
    public void Import(AppData d)
    {
        Commit("Import d'une sauvegarde", () =>
        {
            Data.Classes = d.Classes;
            Data.Timetable = d.Timetable;
            Data.CurrentClassId = d.CurrentClassId;
        }, Change.Structure | Change.Selection);
    }

    /// <summary>Emploi du temps : bascule sur la classe du créneau quand le créneau change.
    /// Un choix manuel reste affiché jusqu'au créneau suivant.</summary>
    public void Tick(DateTime now)
    {
        if (!Settings.AutoSchedule) { _scheduleChecked = false; return; }
        var id = Timetable.ClassAt(Data.Timetable, now);
        if (_scheduleChecked && id == _lastScheduled) return;
        _scheduleChecked = true;
        _lastScheduled = id;
        if (id != null && Find(id) != null) SelectClass(id);
    }

    public bool IsScheduledNow(ClassGroup g) => Settings.AutoSchedule && Timetable.ClassAt(Data.Timetable, DateTime.Now) == g.Id;
}
