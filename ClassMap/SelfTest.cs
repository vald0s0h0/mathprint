namespace ClassMap;

/// <summary>Vérification rapide du binaire publié (CI) : JSON, écriture atomique, reprise après corruption,
/// emploi du temps, placements. « ClassMap.exe --selftest » → code 0 si tout va bien.</summary>
public static class SelfTest
{
    public static int Run()
    {
        var dir = Path.Combine(Path.GetTempPath(), "classmap-selftest-" + Guid.NewGuid().ToString("N")[..8]);
        try
        {
            var data = new AppData();
            var g = new ClassGroup { Name = "5eB" };
            foreach (var n in Placement.ParseNames("Lucas\nJean-Baptiste\tMartin\n\n  Zoé  \n"))
                g.Students.Add(new Student { Name = n });
            data.Classes.Add(g);
            Check(g.Students.Count == 3 && g.Students[1].Name == "Jean-Baptiste Martin", "saisie par lot");
            Check(Placement.Normalize(g) && g.Students.All(s => s.Row >= 0 && s.Order >= 0), "placements");
            Check(Placement.TwoLines("Jean-Baptiste") == "Jean-\nBaptiste", "prénom composé");
            g.Students[0].Score = 4;
            Timetable.Paint(data.Timetable, 0, 8 * 60, 9 * 60, g.Id);
            Timetable.Paint(data.Timetable, 0, 9 * 60, 10 * 60, g.Id);
            Check(data.Timetable.Count == 1 && data.Timetable[0].End == 600, "fusion des créneaux");
            Check(Timetable.ClassAt(data.Timetable, new DateTime(2024, 1, 1, 9, 30, 0)) == g.Id, "créneau du lundi");

            using (var store = new Storage(dir))
            {
                store.Save(data);
                store.Journal("5eB", "Lucas", "+1", 4);
                Check(store.Flush(), "écriture");
                store.Save(data);
                Check(store.Flush() && File.Exists(store.DataPath + ".bak"), "copie .bak");
                Check(store.Load(out _).Classes[0].Students[0].Score == 4, "relecture");
                File.WriteAllText(store.DataPath, "{ tronqué");
                var back = store.Load(out var notice);
                Check(back.Classes.Count == 1 && notice != null, "reprise après corruption");
                Check(File.ReadAllText(store.JournalPath).Contains("Lucas;+1;4"), "journal");
            }
            Console.WriteLine("selftest OK");
            return 0;
        }
        catch (Exception e)
        {
            Console.Error.WriteLine("selftest ÉCHEC : " + e.Message);
            return 1;
        }
        finally
        {
            try { Directory.Delete(dir, true); } catch { }
        }
    }

    static void Check(bool ok, string what)
    {
        if (!ok) throw new Exception(what);
    }
}
