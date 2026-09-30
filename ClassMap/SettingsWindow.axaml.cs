using System.Diagnostics;
using System.Reflection;
using Avalonia;
using Avalonia.Controls;
using Avalonia.Input;
using Avalonia.Media;
using Avalonia.Platform.Storage;

namespace ClassMap;

public partial class SettingsWindow : Window
{
    readonly AppState _state = null!;
    readonly MainWindow _main = null!;
    readonly List<ClassGroup> _listed = new();
    string? _classId;
    bool _loading, _namesDirty;
    string? _paletteBrush;

    public SettingsWindow() => InitializeComponent(); // aperçu XAML

    public SettingsWindow(AppState state, MainWindow main, int tab)
    {
        _state = state;
        _main = main;
        InitializeComponent();
        _classId = state.CurrentClass?.Id;
        _paletteBrush = _classId;
        Week.State = state;
        Week.Brush = _paletteBrush;
        DataPathText.Text = state.Store.Dir;
        VersionText.Text = "ClassMap " + (Assembly.GetExecutingAssembly().GetName().Version?.ToString(3) ?? "");
        Wire();
        Load();
        SelectTab(tab);
        state.Changed += OnChanged;
        Closed += (_, _) => state.Changed -= OnChanged;
    }

    public void SelectTab(int tab) => Tabs.SelectedIndex = tab;

    Settings S => _state.Settings;
    ClassGroup? Selected => _state.Find(_classId);

    void OnChanged(Change change)
    {
        if (!_loading) Load();
    }

    void Load()
    {
        _loading = true;
        try
        {
            TextScaleSlider.Value = S.TextScale;
            OpacitySlider.Value = S.Opacity;
            DualBox.IsChecked = S.DualOpacity;
            IdleSlider.Value = S.IdleOpacity;
            IdleSlider.IsEnabled = S.DualOpacity;
            BackSlider.Value = S.BackgroundOpacity;
            TopmostBox.IsChecked = S.Topmost;
            AutoScheduleBox.IsChecked = S.AutoSchedule;

            var current = _state.CurrentClass;
            GridTitle.Text = current == null ? "Grille (aucune classe)" : $"Grille de la classe « {current.Name} »";
            GridRowsBox.IsEnabled = GridColsBox.IsEnabled = current != null;
            GridRowsBox.Value = current?.GridRows ?? 5;
            GridColsBox.Value = current?.GridCols ?? 6;

            if (Selected == null) _classId = current?.Id ?? _state.Data.Classes.FirstOrDefault()?.Id;
            _listed.Clear();
            _listed.AddRange(_state.Data.Classes);
            ClassList.ItemsSource = _listed.Select(c => $"{c.Name}  ({c.Students.Count})").ToList();
            ClassList.SelectedIndex = _listed.FindIndex(c => c.Id == _classId);
            var g = Selected;
            ClassNameBox.IsEnabled = NamesBox.IsEnabled = ApplyNamesButton.IsEnabled = ResetScoresButton.IsEnabled = DeleteClassButton.IsEnabled = g != null;
            if (ClassNameBox.Text != g?.Name && !ClassNameBox.IsFocused) ClassNameBox.Text = g?.Name ?? "";
            if (!_namesDirty) NamesBox.Text = g == null ? "" : string.Join("\n", g.Students.OrderBy(s => s.Order).Select(s => s.Name));

            BuildPalette();
            Week.InvalidateVisual();
            SaveStatusText.Text = _state.Store.Error ?? (_state.Store.LastSaved == default
                ? "Enregistrement : à jour."
                : $"Dernier enregistrement : {_state.Store.LastSaved:HH:mm:ss}");
        }
        finally { _loading = false; }
    }

    void Wire()
    {
        GridRowsBox.ValueChanged += (_, _) => SetGrid();
        GridColsBox.ValueChanged += (_, _) => SetGrid();

        void Slide(Slider s, Action<double> set) => s.ValueChanged += (_, e) =>
        {
            if (_loading) return;
            set(Math.Round(e.NewValue, 2));
            _state.SettingsChanged();
        };
        Slide(TextScaleSlider, v => S.TextScale = v);
        Slide(OpacitySlider, v => S.Opacity = v);
        Slide(IdleSlider, v => S.IdleOpacity = v);
        Slide(BackSlider, v => S.BackgroundOpacity = v);

        void Check(CheckBox c, Action<bool> set) => c.IsCheckedChanged += (_, _) =>
        {
            if (_loading) return;
            set(c.IsChecked == true);
            _state.SettingsChanged();
        };
        Check(DualBox, v => S.DualOpacity = v);
        Check(TopmostBox, v => S.Topmost = v);
        Check(AutoScheduleBox, v =>
        {
            S.AutoSchedule = v;
            _state.Tick(DateTime.Now);
        });
        ResetSizeButton.Click += (_, _) => _main.ResetGeometry();

        // Classes
        ClassList.SelectionChanged += async (_, _) =>
        {
            if (_loading || ClassList.SelectedIndex < 0 || ClassList.SelectedIndex >= _listed.Count) return;
            var g = _listed[ClassList.SelectedIndex];
            if (g.Id == _classId) return;
            if (_namesDirty && !await Dialogs.Confirm(this, "La liste d'élèves modifiée n'a pas été enregistrée. L'abandonner ?", "Abandonner"))
            {
                Load();
                return;
            }
            _namesDirty = false;
            _classId = g.Id;
            _paletteBrush = g.Id;
            Week.Brush = g.Id;
            _state.SelectClass(g.Id); // la fenêtre principale suit
            Load();
        };
        ClassNameBox.LostFocus += (_, _) => Rename();
        ClassNameBox.KeyDown += (_, e) => { if (e.Key == Key.Enter) Rename(); };
        NamesBox.TextChanged += (_, _) => { if (!_loading) _namesDirty = true; };
        AddClassButton.Click += async (_, _) => await AddClass();
        DeleteClassButton.Click += async (_, _) => await DeleteClass();
        ApplyNamesButton.Click += async (_, _) => await ApplyNames();
        ResetScoresButton.Click += async (_, _) =>
        {
            if (Selected is { } g && await Dialogs.Confirm(this, $"Remettre à 0 les scores de toute la classe « {g.Name} » ?", "Remettre à 0"))
                _state.ResetClass(g);
        };

        // Données
        OpenFolderButton.Click += (_, _) => Open(_state.Store.Dir);
        OpenJournalButton.Click += (_, _) =>
        {
            _state.Store.Flush();
            if (File.Exists(_state.Store.JournalPath)) Open(_state.Store.JournalPath);
            else _ = Dialogs.Info(this, "Le journal est vide pour l'instant.");
        };
        ExportButton.Click += async (_, _) => await Export();
        ImportButton.Click += async (_, _) => await Import();
        QuitButton.Click += (_, _) => _main.Quit();
    }

    void SetGrid()
    {
        if (_loading || _state.CurrentClass is not { } g) return;
        int rows = (int)(GridRowsBox.Value ?? g.GridRows), cols = (int)(GridColsBox.Value ?? g.GridCols);
        if (rows == g.GridRows && cols == g.GridCols) return;
        _state.Commit("Taille de la grille", () =>
        {
            g.GridRows = rows;
            g.GridCols = cols;
            Placement.Normalize(g);
        }, Change.Layout);
    }

    void Rename()
    {
        if (_loading || Selected is not { } g) return;
        var name = ClassNameBox.Text?.Trim() ?? "";
        if (name.Length == 0 || name == g.Name) { ClassNameBox.Text = g.Name; return; }
        _state.Commit($"Renommer {g.Name}", () => g.Name = name, Change.Structure);
    }

    async Task AddClass()
    {
        var name = await Dialogs.Prompt(this, "Nom de la nouvelle classe (ex. 5eB) :");
        if (string.IsNullOrWhiteSpace(name)) return;
        var g = new ClassGroup { Name = name };
        _state.Commit($"Ajouter {name}", () => _state.Data.Classes.Add(g), Change.Structure);
        _namesDirty = false;
        _classId = g.Id;
        _paletteBrush = g.Id;
        Week.Brush = g.Id;
        _state.SelectClass(g.Id);
        Load();
        NamesBox.Focus();
    }

    async Task DeleteClass()
    {
        if (Selected is not { } g) return;
        if (!await Dialogs.Confirm(this, $"Supprimer la classe « {g.Name} », ses {g.Students.Count} élèves et ses créneaux ?\n(Annulable avec la flèche ↶.)", "Supprimer"))
            return;
        _state.Commit($"Supprimer {g.Name}", () =>
        {
            _state.Data.Classes.Remove(g);
            _state.Data.Timetable.RemoveAll(t => t.ClassId == g.Id);
        }, Change.Structure | Change.Selection);
        _namesDirty = false;
        _classId = null;
        Load();
    }

    async Task ApplyNames()
    {
        if (Selected is not { } g) return;
        var names = Placement.ParseNames(NamesBox.Text);
        var pool = g.Students.ToList();
        var result = new List<Student>();
        foreach (var n in names)
        {
            var match = pool.Find(s => string.Equals(s.Name, n, StringComparison.OrdinalIgnoreCase));
            if (match != null) { pool.Remove(match); result.Add(match); }
            else result.Add(new Student { Name = n });
        }
        if (pool.Count > 0)
        {
            var list = string.Join(", ", pool.Take(8).Select(s => s.Name)) + (pool.Count > 8 ? "…" : "");
            if (!await Dialogs.Confirm(this, $"{pool.Count} élève(s) retiré(s) de la classe : {list}.\nLeurs scores et placements seront perdus (annulable avec ↶).", "Continuer"))
                return;
        }
        _state.Commit($"Liste des élèves de {g.Name}", () =>
        {
            for (int i = 0; i < result.Count; i++) result[i].Order = i;
            g.Students = result;
            Placement.Normalize(g);
        }, Change.Structure);
        _namesDirty = false;
        Load();
    }

    void BuildPalette()
    {
        Palette.Children.Clear();
        foreach (var g in _state.Data.Classes)
            Palette.Children.Add(Chip(g.Name, WeekGrid.ColorOf(_state, g.Id), g.Id));
        Palette.Children.Add(Chip("Gomme", new SolidColorBrush(Color.Parse("#40FF5A5A")), null));
    }

    Border Chip(string text, IBrush color, string? id)
    {
        bool selected = _paletteBrush == id;
        var b = new Border
        {
            Child = new TextBlock { Text = text, Foreground = Brushes.Black, FontWeight = selected ? FontWeight.SemiBold : FontWeight.Normal },
            Background = color,
            CornerRadius = new CornerRadius(4),
            Margin = new Thickness(0, 0, 6, 6),
            Padding = new Thickness(10, 3),
            BorderBrush = Brushes.Black,
            BorderThickness = new Thickness(selected ? 2 : 0),
            Cursor = new Cursor(StandardCursorType.Hand),
        };
        b.PointerPressed += (_, _) =>
        {
            _paletteBrush = id;
            Week.Brush = id;
            BuildPalette();
        };
        return b;
    }

    static void Open(string path)
    {
        try { using var _ = Process.Start(new ProcessStartInfo(path) { UseShellExecute = true }); } catch { }
    }

    async Task Export()
    {
        var file = await StorageProvider.SaveFilePickerAsync(new FilePickerSaveOptions
        {
            Title = "Exporter une copie des données ClassMap",
            SuggestedFileName = $"classmap-{DateTime.Now:yyyy-MM-dd}.json",
            DefaultExtension = "json",
            FileTypeChoices = new[] { new FilePickerFileType("Données ClassMap") { Patterns = new[] { "*.json" } } },
        });
        if (file == null) return;
        try
        {
            await using var stream = await file.OpenWriteAsync();
            await stream.WriteAsync(Json.Serialize(_state.Data));
            await Dialogs.Info(this, "Copie exportée.");
        }
        catch (Exception e) { await Dialogs.Info(this, "Export impossible : " + e.Message); }
    }

    async Task Import()
    {
        var files = await StorageProvider.OpenFilePickerAsync(new FilePickerOpenOptions
        {
            Title = "Importer une sauvegarde ClassMap",
            AllowMultiple = false,
            FileTypeFilter = new[] { new FilePickerFileType("Données ClassMap") { Patterns = new[] { "*.json" } } },
        });
        if (files.Count == 0) return;
        AppData data;
        try
        {
            await using var stream = await files[0].OpenReadAsync();
            using var ms = new MemoryStream();
            await stream.CopyToAsync(ms);
            data = Storage.Sanitize(Json.Parse(ms.ToArray()));
        }
        catch { await Dialogs.Info(this, "Ce fichier n'est pas une sauvegarde ClassMap lisible."); return; }
        if (!await Dialogs.Confirm(this, $"Remplacer les classes et l'emploi du temps actuels par ceux du fichier ({data.Classes.Count} classes) ?\nLes réglages d'affichage sont conservés. Annulable avec ↶.", "Remplacer"))
            return;
        _state.Import(data);
        _namesDirty = false;
        _classId = null;
        Load();
    }
}
