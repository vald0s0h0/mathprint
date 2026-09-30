using System.Text.Json;
using System.Text.Json.Serialization;

namespace ClassMap;

/// <summary>Tout ce qui est persisté sur la clé, dans un seul fichier JSON.</summary>
public sealed class AppData
{
    public int Version { get; set; } = 1;
    public Settings Settings { get; set; } = new();
    public List<ClassGroup> Classes { get; set; } = new();
    public List<Slot> Timetable { get; set; } = new();
    public string? CurrentClassId { get; set; }
}

public sealed class Settings
{
    public bool Topmost { get; set; } = true;
    public double Opacity { get; set; } = 0.95;
    public bool DualOpacity { get; set; }
    public double IdleOpacity { get; set; } = 0.45;
    public double BackgroundOpacity { get; set; }
    public double TextScale { get; set; } = 1;
    public bool AutoSchedule { get; set; } = true;
    public WindowRect? Window { get; set; }
}

/// <summary>Position de la fenêtre en fractions de la zone de travail de l'écran :
/// retrouvée à l'identique sur un autre PC, quelle que soit la résolution.</summary>
public sealed class WindowRect
{
    public int Screen { get; set; }
    public double X { get; set; }
    public double Y { get; set; }
    public double W { get; set; }
    public double H { get; set; }
}

public sealed class ClassGroup
{
    public string Id { get; set; } = Ids.New();
    public string Name { get; set; } = "";
    public int GridRows { get; set; } = 5;
    public int GridCols { get; set; } = 6;
    public List<Student> Students { get; set; } = new();
}

public sealed class Student
{
    public string Id { get; set; } = Ids.New();
    public string Name { get; set; } = "";
    public int Score { get; set; }
    // Grille : case (table) et rang dans la case.
    public int Row { get; set; } = -1;
    public int Col { get; set; } = -1;
    public int Seat { get; set; }
    // Ordre de la liste des noms (réglages).
    public int Order { get; set; } = -1;
}

/// <summary>Créneau de l'emploi du temps. Day : 0 = lundi. Start/End en minutes depuis minuit.</summary>
public sealed class Slot
{
    public int Day { get; set; }
    public int Start { get; set; }
    public int End { get; set; }
    public string ClassId { get; set; } = "";
}

public sealed class UndoState
{
    public List<ClassGroup> Classes { get; set; } = new();
    public List<Slot> Timetable { get; set; } = new();
}

[JsonSourceGenerationOptions(WriteIndented = true, UseStringEnumConverter = true,
    DefaultIgnoreCondition = JsonIgnoreCondition.WhenWritingNull)]
[JsonSerializable(typeof(AppData))]
[JsonSerializable(typeof(UndoState))]
internal sealed partial class Json : JsonSerializerContext
{
    public static byte[] Serialize(AppData d) => JsonSerializer.SerializeToUtf8Bytes(d, Default.AppData);

    /// <summary>Lève une exception si le contenu n'est pas un fichier ClassMap valide.</summary>
    public static AppData Parse(ReadOnlySpan<byte> bytes) =>
        JsonSerializer.Deserialize(bytes, Default.AppData) ?? throw new JsonException("Fichier vide");
}

public static class Ids
{
    public static string New() => Guid.NewGuid().ToString("N")[..12];
}

public static class Timetable
{
    public const int DayStart = 7 * 60, DayEnd = 19 * 60, Step = 15;
    public static readonly string[] Days = { "Lun", "Mar", "Mer", "Jeu", "Ven", "Sam" };

    public static int DayIndex(DayOfWeek d) => ((int)d + 6) % 7;

    public static string? ClassAt(List<Slot> slots, DateTime t)
    {
        int day = DayIndex(t.DayOfWeek), minute = t.Hour * 60 + t.Minute;
        foreach (var s in slots)
            if (s.Day == day && s.Start <= minute && minute < s.End) return s.ClassId;
        return null;
    }

    /// <summary>Affecte [start, end[ du jour à une classe (null = efface), puis fusionne les créneaux contigus.</summary>
    public static void Paint(List<Slot> slots, int day, int start, int end, string? classId)
    {
        var result = new List<Slot>(slots.Count + 2);
        foreach (var s in slots)
        {
            if (s.Day != day || s.End <= start || s.Start >= end) { result.Add(s); continue; }
            if (s.Start < start) result.Add(new Slot { Day = day, Start = s.Start, End = start, ClassId = s.ClassId });
            if (s.End > end) result.Add(new Slot { Day = day, Start = end, End = s.End, ClassId = s.ClassId });
        }
        if (classId != null) result.Add(new Slot { Day = day, Start = start, End = end, ClassId = classId });
        result.Sort((a, b) => a.Day != b.Day ? a.Day.CompareTo(b.Day) : a.Start.CompareTo(b.Start));
        slots.Clear();
        foreach (var s in result)
        {
            var last = slots.Count > 0 ? slots[^1] : null;
            if (last != null && last.Day == s.Day && last.End == s.Start && last.ClassId == s.ClassId) last.End = s.End;
            else slots.Add(s);
        }
    }
}

public static class Placement
{
    /// <summary>Rend les placements cohérents (élèves nouveaux ou hors d'une grille réduite).
    /// Renvoie true si quelque chose a changé.</summary>
    public static bool Normalize(ClassGroup g)
    {
        bool changed = false;
        g.GridRows = Math.Clamp(g.GridRows, 1, 15);
        g.GridCols = Math.Clamp(g.GridCols, 1, 15);
        int rows = g.GridRows, cols = g.GridCols;

        // Ordre des noms : 0..n-1 dans l'ordre existant, nouveaux à la fin.
        var ordered = g.Students.Select((s, i) => (s, i))
            .OrderBy(t => t.s.Order < 0 ? int.MaxValue : t.s.Order).ThenBy(t => t.i).ToList();
        for (int i = 0; i < ordered.Count; i++)
            if (ordered[i].s.Order != i) { ordered[i].s.Order = i; changed = true; }

        // Grille : élèves sans case (ou hors d'une grille réduite) -> case libre, sinon la moins remplie.
        var count = new int[rows, cols];
        foreach (var s in g.Students)
            if (InGrid(s, rows, cols)) count[s.Row, s.Col]++;
        foreach (var (s, _) in ordered)
        {
            if (InGrid(s, rows, cols)) continue;
            int br = 0, bc = 0;
            for (int r = 0; r < rows; r++)
                for (int c = 0; c < cols; c++)
                    if (count[r, c] < count[br, bc]) { br = r; bc = c; }
            s.Row = br; s.Col = bc; s.Seat = count[br, bc]++;
            changed = true;
        }

        return changed;
    }

    static bool InGrid(Student s, int rows, int cols) => s.Row >= 0 && s.Row < rows && s.Col >= 0 && s.Col < cols;

    /// <summary>« Jean-Baptiste » -> « Jean- / Baptiste », « Marie Lou » -> « Marie / Lou ».</summary>
    public static string TwoLines(string name)
    {
        int best = -1;
        for (int i = 1; i < name.Length - 1; i++)
            if ((name[i] == ' ' || name[i] == '-') && (best < 0 || Math.Abs(i - name.Length / 2.0) < Math.Abs(best - name.Length / 2.0)))
                best = i;
        if (best < 0) return name;
        return name[best] == '-'
            ? name[..(best + 1)] + "\n" + name[(best + 1)..].TrimStart()
            : name[..best].TrimEnd() + "\n" + name[(best + 1)..].TrimStart();
    }

    /// <summary>Colle depuis un tableur/Pronote : une ligne = un élève, tabulations -> espaces.</summary>
    public static List<string> ParseNames(string? text) =>
        (text ?? "").Replace("\r", "").Split('\n')
            .Select(l => string.Join(' ', l.Replace('\t', ' ').Split(' ', StringSplitOptions.RemoveEmptyEntries)))
            .Where(l => l.Length > 0).ToList();
}
