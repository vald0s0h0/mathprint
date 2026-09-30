using Avalonia;
using Avalonia.Controls;
using Avalonia.Input;
using Avalonia.Layout;
using Avalonia.Media;
using Avalonia.Platform;
using Avalonia.Threading;

namespace ClassMap;

public partial class MainWindow : Window
{
    readonly AppState _state;
    readonly DispatcherTimer _geometryTimer = new() { Interval = TimeSpan.FromMilliseconds(700) };
    readonly DispatcherTimer _clock = new() { Interval = TimeSpan.FromSeconds(15) };
    readonly DispatcherTimer _noticeTimer = new() { Interval = TimeSpan.FromSeconds(12) };
    Button _classButton = null!, _undoButton = null!, _redoButton = null!, _pinButton = null!;
    TextBlock _className = null!;
    PathIcon _clockIcon = null!;
    SettingsWindow? _settings;
    bool _hover, _closing;
    string? _notice;

    public MainWindow(AppState state)
    {
        _state = state;
        InitializeComponent();
        Board.State = state;
        BuildToolbar();
        BuildResizeHandles();
        RestoreGeometry();
        ApplySettings();

        state.Changed += OnChanged;
        state.Store.StatusChanged += e => Dispatcher.UIThread.Post(UpdateBanner);

        PointerEntered += (_, _) => { _hover = true; ApplyOpacity(); };
        PointerExited += (_, _) => { _hover = false; ApplyOpacity(); };
        PositionChanged += (_, _) => { _geometryTimer.Stop(); _geometryTimer.Start(); };
        SizeChanged += (_, _) => { _geometryTimer.Stop(); _geometryTimer.Start(); };
        _geometryTimer.Tick += (_, _) => { _geometryTimer.Stop(); CaptureGeometry(); };
        _clock.Tick += (_, _) => state.Tick(DateTime.Now);
        _noticeTimer.Tick += (_, _) => { _noticeTimer.Stop(); _notice = null; UpdateBanner(); };

        Opened += (_, _) =>
        {
            if (ActualTransparencyLevel == WindowTransparencyLevel.None)
                Frame.Background = new SolidColorBrush(Color.Parse("#E6202020")); // transparence indisponible
            state.Tick(DateTime.Now);
            _clock.Start();
            if (state.StartupNotice != null) ShowNotice(state.StartupNotice);
            UpdateBanner();
            RefreshToolbar();
            if (state.Data.Classes.Count == 0) OpenSettings(1);
        };
        Closing += (_, _) =>
        {
            _closing = true;
            CaptureGeometry();
            state.Store.Flush();
        };
    }

    // ---------- Barre d'outils ----------

    void BuildToolbar()
    {
        var grip = new Border
        {
            Background = Brushes.Transparent,
            Padding = new Thickness(5, 0),
            Cursor = new Cursor(StandardCursorType.SizeAll),
            Child = new PathIcon { Data = Icons.Move, Width = 11, Height = 11, Foreground = Brushes.White },
        };
        ToolTip.SetTip(grip, "Déplacer la fenêtre");
        grip.PointerPressed += DragWindow;
        DockPanel.SetDock(grip, Dock.Left);
        ToolbarPanel.Children.Add(grip);

        var right = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 1 };
        DockPanel.SetDock(right, Dock.Right);
        _undoButton = Tool(right, new PathIcon { Data = Icons.Undo }, "Annuler", _state.Undo);
        _redoButton = Tool(right, new PathIcon { Data = Icons.Redo }, "Rétablir", _state.Redo);
        _pinButton = Tool(right, new PathIcon { Data = Icons.Pin }, "Toujours au premier plan", () =>
        {
            _state.Settings.Topmost = !_state.Settings.Topmost;
            _state.SettingsChanged();
        });
        Tool(right, new PathIcon { Data = Icons.Cog }, "Réglages", () => OpenSettings(0));
        Tool(right, new PathIcon { Data = Icons.Minimize }, "Réduire", () => WindowState = WindowState.Minimized);
        Tool(right, new PathIcon { Data = Icons.Eject }, "Enregistrer, éjecter la clé et fermer", Eject);
        ToolbarPanel.Children.Add(right);

        _className = new TextBlock
        {
            FontSize = 11, FontWeight = FontWeight.SemiBold, Foreground = Brushes.White,
            TextTrimming = TextTrimming.CharacterEllipsis, VerticalAlignment = VerticalAlignment.Center,
        };
        _clockIcon = new PathIcon { Data = Icons.Clock, Width = 9, Height = 9, Margin = new Thickness(0, 0, 4, 0), IsVisible = false };
        var label = new StackPanel { Orientation = Orientation.Horizontal, Children = { _clockIcon, _className, new TextBlock { Text = " ▾", FontSize = 9, Foreground = Brushes.White, VerticalAlignment = VerticalAlignment.Center } } };
        _classButton = new Button { Content = label, HorizontalAlignment = HorizontalAlignment.Left };
        _classButton.Classes.Add("tb");
        _classButton.Click += (_, _) => ShowClassMenu();
        ToolTip.SetTip(_classButton, "Choisir la classe");
        DockPanel.SetDock(_classButton, Dock.Left);
        ToolbarPanel.Children.Add(_classButton);

        // Le reste de la barre sert aussi de poignée de déplacement.
        var filler = new Border { Background = Brushes.Transparent };
        filler.PointerPressed += DragWindow;
        ToolbarPanel.Children.Add(filler);

        Toolbar.PointerEntered += (_, _) => Toolbar.Opacity = 1;
        Toolbar.PointerExited += (_, _) => Toolbar.Opacity = 0.5;
    }

    static Button Tool(Panel parent, PathIcon icon, string tip, Action action)
    {
        var b = new Button { Content = icon };
        b.Classes.Add("tb");
        ToolTip.SetTip(b, tip);
        b.Click += (_, _) => action();
        parent.Children.Add(b);
        return b;
    }

    void DragWindow(object? sender, PointerPressedEventArgs e)
    {
        if (e.GetCurrentPoint(this).Properties.IsLeftButtonPressed) BeginMoveDrag(e);
    }

    void ShowClassMenu()
    {
        var menu = new MenuFlyout();
        var current = _state.CurrentClass;
        foreach (var g in _state.Data.Classes)
        {
            var id = g.Id;
            var item = new MenuItem
            {
                Header = g.Name + (_state.IsScheduledNow(g) ? "   (emploi du temps)" : ""),
                Icon = g == current ? new TextBlock { Text = "✓" } : null,
            };
            item.Click += (_, _) => _state.SelectClass(id);
            menu.Items.Add(item);
        }
        if (_state.Data.Classes.Count > 0) menu.Items.Add(new Separator());
        var manage = new MenuItem { Header = "Gérer les classes…" };
        manage.Click += (_, _) => OpenSettings(1);
        menu.Items.Add(manage);
        menu.ShowAt(_classButton);
    }

    void RefreshToolbar()
    {
        var g = _state.CurrentClass;
        _className.Text = g?.Name ?? "Aucune classe";
        _clockIcon.IsVisible = g != null && _state.IsScheduledNow(g);
        _undoButton.IsEnabled = _state.CanUndo;
        _redoButton.IsEnabled = _state.CanRedo;
        ToolTip.SetTip(_undoButton, _state.UndoLabel is { } u ? $"Annuler : {u}  (Ctrl+Z)" : "Annuler");
        ToolTip.SetTip(_redoButton, _state.RedoLabel is { } r ? $"Rétablir : {r}  (Ctrl+Y)" : "Rétablir");
        _pinButton.Classes.Set("off", !_state.Settings.Topmost);
    }

    // ---------- Redimensionnement par les bords ----------

    void BuildResizeHandles()
    {
        const double e = 5, c = 12;
        Handle(WindowEdge.West, HorizontalAlignment.Left, VerticalAlignment.Stretch, e, double.NaN, StandardCursorType.LeftSide);
        Handle(WindowEdge.East, HorizontalAlignment.Right, VerticalAlignment.Stretch, e, double.NaN, StandardCursorType.RightSide);
        Handle(WindowEdge.North, HorizontalAlignment.Stretch, VerticalAlignment.Top, double.NaN, e, StandardCursorType.TopSide);
        Handle(WindowEdge.South, HorizontalAlignment.Stretch, VerticalAlignment.Bottom, double.NaN, e, StandardCursorType.BottomSide);
        Handle(WindowEdge.NorthWest, HorizontalAlignment.Left, VerticalAlignment.Top, c, c, StandardCursorType.TopLeftCorner);
        Handle(WindowEdge.NorthEast, HorizontalAlignment.Right, VerticalAlignment.Top, c, c, StandardCursorType.TopRightCorner);
        Handle(WindowEdge.SouthWest, HorizontalAlignment.Left, VerticalAlignment.Bottom, c, c, StandardCursorType.BottomLeftCorner);
        Handle(WindowEdge.SouthEast, HorizontalAlignment.Right, VerticalAlignment.Bottom, c, c, StandardCursorType.BottomRightCorner);
    }

    void Handle(WindowEdge edge, HorizontalAlignment h, VerticalAlignment v, double w, double ht, StandardCursorType cursor)
    {
        var b = new Border
        {
            Background = new SolidColorBrush(Color.FromArgb(1, 0, 0, 0)),
            HorizontalAlignment = h, VerticalAlignment = v, Width = w, Height = ht,
            Cursor = new Cursor(cursor),
        };
        b.PointerPressed += (_, e) =>
        {
            if (!e.GetCurrentPoint(this).Properties.IsLeftButtonPressed) return;
            e.Handled = true;
            if (OperatingSystem.IsWindows()) { BeginResizeDrag(edge, e); return; }
            // macOS/Linux : pas de redimensionnement natif sans décorations, on le fait à la main.
            _resize = new ResizeDrag(edge, b.PointToScreen(e.GetPosition(b)), Position, Bounds.Size,
                (this.PointToScreen(new Point(100, 0)).X - this.PointToScreen(new Point(0, 0)).X) / 100.0);
            e.Pointer.Capture(b);
        };
        b.PointerMoved += (_, e) =>
        {
            if (_resize is { } r) ResizeTo(r, b.PointToScreen(e.GetPosition(b)));
        };
        b.PointerReleased += (_, _) => _resize = null;
        b.PointerCaptureLost += (_, _) => _resize = null;
        Handles.Children.Add(b);
    }

    sealed record ResizeDrag(WindowEdge Edge, PixelPoint Start, PixelPoint Position, Size Size, double Unit);
    ResizeDrag? _resize;

    /// <summary>Unit = unités écran par pixel logique (mesuré, car il diffère entre Windows et macOS).</summary>
    void ResizeTo(ResizeDrag r, PixelPoint p)
    {
        double dx = (p.X - r.Start.X) / r.Unit, dy = (p.Y - r.Start.Y) / r.Unit;
        double w = r.Size.Width, h = r.Size.Height;
        int x = r.Position.X, y = r.Position.Y;
        var e = r.Edge;
        if (e is WindowEdge.East or WindowEdge.NorthEast or WindowEdge.SouthEast) w = Math.Max(MinWidth, r.Size.Width + dx);
        if (e is WindowEdge.South or WindowEdge.SouthEast or WindowEdge.SouthWest) h = Math.Max(MinHeight, r.Size.Height + dy);
        if (e is WindowEdge.West or WindowEdge.NorthWest or WindowEdge.SouthWest)
        {
            w = Math.Max(MinWidth, r.Size.Width - dx);
            x = r.Position.X + (int)Math.Round((r.Size.Width - w) * r.Unit);
        }
        if (e is WindowEdge.North or WindowEdge.NorthWest or WindowEdge.NorthEast)
        {
            h = Math.Max(MinHeight, r.Size.Height - dy);
            y = r.Position.Y + (int)Math.Round((r.Size.Height - h) * r.Unit);
        }
        if (x != Position.X || y != Position.Y) Position = new PixelPoint(x, y);
        Width = w;
        Height = h;
    }

    // ---------- Position / taille, mémorisées en fractions de l'écran ----------

    Screen? ScreenAt(int index)
    {
        var all = Screens.All;
        return index >= 0 && index < all.Count ? all[index] : Screens.Primary ?? all.FirstOrDefault();
    }

    void RestoreGeometry()
    {
        var wr = _state.Settings.Window;
        if (ScreenAt(wr?.Screen ?? -1) is not { } screen) return;
        var wa = screen.WorkingArea;
        double fw = 1 / 3.0, fh = 1 / 3.0, fx = 1 - fw, fy = 0; // défaut : 1/9 de l'écran, en haut à droite
        if (wr != null)
        {
            fw = Math.Clamp(wr.W, 0.05, 1);
            fh = Math.Clamp(wr.H, 0.05, 1);
            fx = Math.Clamp(wr.X, 0, 1 - fw);
            fy = Math.Clamp(wr.Y, 0, 1 - fh);
        }
        Width = Math.Max(MinWidth, fw * wa.Width / screen.Scaling);
        Height = Math.Max(MinHeight, fh * wa.Height / screen.Scaling);
        Position = new PixelPoint(wa.X + (int)(fx * wa.Width), wa.Y + (int)(fy * wa.Height));
    }

    void CaptureGeometry()
    {
        if (WindowState != WindowState.Normal || Bounds.Width < 1) return;
        var center = new PixelPoint(Position.X + (int)(Bounds.Width * RenderScaling / 2), Position.Y + (int)(Bounds.Height * RenderScaling / 2));
        var screen = Screens.ScreenFromPoint(center) ?? Screens.Primary;
        if (screen == null) return;
        var wa = screen.WorkingArea;
        var rect = new WindowRect
        {
            Screen = Math.Max(0, Screens.All.ToList().IndexOf(screen)),
            X = (Position.X - wa.X) / (double)wa.Width,
            Y = (Position.Y - wa.Y) / (double)wa.Height,
            W = Bounds.Width * RenderScaling / wa.Width,
            H = Bounds.Height * RenderScaling / wa.Height,
        };
        var old = _state.Settings.Window;
        if (old != null && Math.Abs(old.X - rect.X) + Math.Abs(old.Y - rect.Y) + Math.Abs(old.W - rect.W) + Math.Abs(old.H - rect.H) < 1e-4
            && old.Screen == rect.Screen) return;
        _state.Settings.Window = rect;
        _state.Save();
    }

    public void ResetGeometry()
    {
        _state.Settings.Window = null;
        RestoreGeometry();
        _state.Save();
    }

    // ---------- Réglages, opacité, bandeau ----------

    void OnChanged(Change change)
    {
        Board.Refresh();
        if (change.HasFlag(Change.Settings)) ApplySettings();
        RefreshToolbar();
    }

    void ApplySettings()
    {
        var s = _state.Settings;
        Topmost = s.Topmost;
        if (ActualTransparencyLevel != WindowTransparencyLevel.None || !IsVisible)
            Frame.Background = new SolidColorBrush(Color.FromArgb((byte)Math.Max(1, s.BackgroundOpacity * 255), 24, 24, 24));
        ApplyOpacity();
        Board.Refresh();
    }

    void ApplyOpacity()
    {
        var s = _state.Settings;
        Opacity = s.DualOpacity && !_hover ? s.IdleOpacity : s.Opacity;
    }

    void ShowNotice(string text)
    {
        _notice = text;
        UpdateBanner();
        _noticeTimer.Stop();
        _noticeTimer.Start();
    }

    void UpdateBanner()
    {
        var text = _state.Store.Error ?? _notice;
        Banner.IsVisible = text != null;
        BannerText.Text = text;
        Banner.Background = new SolidColorBrush(Color.Parse(_state.Store.Error != null ? "#E6B3261E" : "#E6305080"));
    }

    void OpenSettings(int tab)
    {
        if (_settings != null)
        {
            _settings.SelectTab(tab);
            _settings.Activate();
            return;
        }
        _settings = new SettingsWindow(_state, this, tab) { Topmost = Topmost };
        _settings.Closed += (_, _) => _settings = null;
        _settings.Show(this);
    }

    protected override void OnKeyDown(KeyEventArgs e)
    {
        base.OnKeyDown(e);
        if (!e.KeyModifiers.HasFlag(KeyModifiers.Control)) return;
        if (e.Key == Key.Z && e.KeyModifiers.HasFlag(KeyModifiers.Shift) || e.Key == Key.Y) _state.Redo();
        else if (e.Key == Key.Z) _state.Undo();
        else return;
        e.Handled = true;
    }

    // ---------- Quitter / éjecter ----------

    public void Quit()
    {
        if (!_closing) Close();
    }

    async void Eject()
    {
        Board.CancelInteraction();
        CaptureGeometry();
        _state.Save();
        if (!_state.Store.Flush() && !await Dialogs.Confirm(this,
                "La clé est inaccessible : les dernières modifications ne sont pas enregistrées.\n\nFermer quand même ?", "Fermer"))
            return;

        if (OperatingSystem.IsWindows())
        {
            var plan = UsbEject.Prepare(_state.Store.Dir);
            switch (plan.Kind)
            {
                case UsbEject.Kind.Ready:
                    if (!UsbEject.LaunchHelper(plan)) UsbEject.OpenSafeRemoval();
                    break;
                case UsbEject.Kind.Failed:
                    if (await Dialogs.Confirm(this,
                            $"Éjection impossible : {plan.Error}\n\nVos données sont enregistrées. Ouvrir « Retirer le périphérique en toute sécurité » ?",
                            "Oui", "Non"))
                        UsbEject.OpenSafeRemoval();
                    break;
                default:
                    await Dialogs.Info(this, $"ClassMap ne fonctionne pas depuis une clé USB ({plan.Drive}) : les données sont enregistrées, rien à éjecter.");
                    break;
            }
        }
        Quit();
    }
}
