using System.Globalization;
using Avalonia;
using Avalonia.Controls;
using Avalonia.Input;
using Avalonia.Media;
using Avalonia.Media.Immutable;
using Avalonia.Threading;

namespace ClassMap;

/// <summary>
/// La classe : un seul contrôle dessiné à la main (pas un contrôle par élève), donc aucun lag.
/// Clic = +1 · clic droit ou appui long = menu (−1, remise à 0) · glisser = déplacer.
/// </summary>
public sealed class ClassBoard : Control
{
    const double DragThreshold = 6;

    static readonly IImmutableSolidColorBrush[] Fills =
    {
        new ImmutableSolidColorBrush(Color.Parse("#F2F4F7")), // 0
        new ImmutableSolidColorBrush(Color.Parse("#FFE66D")), // 1 jaune
        new ImmutableSolidColorBrush(Color.Parse("#FFC247")), // 2
        new ImmutableSolidColorBrush(Color.Parse("#FF8C42")), // 3 orange
        new ImmutableSolidColorBrush(Color.Parse("#E5383B")), // 4+ rouge
    };
    static readonly IImmutableSolidColorBrush Dark = new ImmutableSolidColorBrush(Color.Parse("#1E1E1E"));
    static readonly IImmutableSolidColorBrush Light = new ImmutableSolidColorBrush(Colors.White);
    static readonly IImmutableSolidColorBrush Bubble = new ImmutableSolidColorBrush(Color.Parse("#E6303030"));
    static readonly IImmutableSolidColorBrush Pill = new ImmutableSolidColorBrush(Color.Parse("#2E000000"));
    static readonly IImmutableSolidColorBrush DropFill = new ImmutableSolidColorBrush(Color.Parse("#334DA3FF"));
    static readonly IImmutableSolidColorBrush HintBrush = new ImmutableSolidColorBrush(Color.Parse("#E6FFFFFF"));
    static readonly IImmutableSolidColorBrush HintBack = new ImmutableSolidColorBrush(Color.Parse("#99202020"));
    static readonly ImmutablePen BadgePen = new(new ImmutableSolidColorBrush(Color.Parse("#66000000")), 0.8);
    static readonly ImmutablePen GridPen = new(new ImmutableSolidColorBrush(Color.Parse("#66A0A0A0")), 1);
    static readonly ImmutablePen DropPen = new(new ImmutableSolidColorBrush(Color.Parse("#FF4DA3FF")), 3);
    static readonly Typeface Face = new(FontFamily.Default, FontStyle.Normal, FontWeight.SemiBold);
    static readonly Typeface Bold = new(FontFamily.Default, FontStyle.Normal, FontWeight.Bold);

    readonly record struct Item(Student S, Rect R);

    readonly List<Item> _items = new();
    readonly Dictionary<string, (string Key, FormattedText Text)> _names = new();
    readonly DispatcherTimer _longPress = new() { Interval = TimeSpan.FromMilliseconds(550) };
    Size _laidOut;
    bool _dirty = true;
    double _cellW, _cellH;

    Student? _press;
    Rect _pressRect;
    Point _pressPt, _dragPt;
    bool _dragging, _longFired;
    IPointer? _pointer;

    public AppState? State { get; set; }

    public ClassBoard()
    {
        ClipToBounds = true;
        _longPress.Tick += (_, _) => OnLongPress();
    }

    ClassGroup? Group => State?.CurrentClass;

    public void Refresh()
    {
        if (_press != null && Group?.Students.Contains(_press) != true) CancelInteraction();
        _dirty = true;
        InvalidateVisual();
    }

    public void CancelInteraction()
    {
        _longPress.Stop();
        _pointer?.Capture(null);
        _pointer = null;
        _press = null;
        _dragging = false;
        InvalidateVisual();
    }

    // ---------- Mise en page : grille (une case = une table) ----------

    void Layout()
    {
        _items.Clear();
        var g = Group;
        double w = Bounds.Width, h = Bounds.Height;
        if (g == null || g.Students.Count == 0 || w < 20 || h < 20) return;

        _cellW = w / g.GridCols;
        _cellH = h / g.GridRows;
        double m = Math.Max(1.5, Math.Min(_cellW, _cellH) * 0.06);
        foreach (var cell in g.Students.GroupBy(s => (s.Row, s.Col)))
        {
            var list = cell.OrderBy(s => s.Seat).ToList();
            int k = list.Count, kc = k <= 3 ? k : (k + 1) / 2, kr = k <= 3 ? 1 : 2;
            var box = new Rect(cell.Key.Col * _cellW, cell.Key.Row * _cellH, _cellW, _cellH).Deflate(m);
            double sw = box.Width / kc, sh = box.Height / kr;
            for (int i = 0; i < k; i++)
            {
                var r = new Rect(box.X + i % kc * sw, box.Y + i / kc * sh, sw, sh);
                if (k > 1) r = r.Deflate(m * 0.35);
                _items.Add(new Item(list[i], Squat(r)));
            }
        }
    }

    /// <summary>Évite les badges plus hauts que larges dans les cases étroites.</summary>
    static Rect Squat(Rect r)
    {
        double maxH = r.Width * 0.75;
        return r.Height <= maxH ? r : new Rect(r.X, r.Center.Y - maxH / 2, r.Width, maxH);
    }

    // ---------- Dessin ----------

    public override void Render(DrawingContext dc)
    {
        if (_dirty || _laidOut != Bounds.Size)
        {
            Layout();
            _dirty = false;
            _laidOut = Bounds.Size;
        }
        dc.FillRectangle(Brushes.Transparent, new Rect(Bounds.Size)); // toute la surface capte la souris

        var g = Group;
        if (g == null || g.Students.Count == 0)
        {
            DrawHint(dc, g == null
                ? "Aucune classe.\nCréez vos classes dans les réglages ⚙"
                : $"{g.Name} : aucun élève.\nCollez la liste dans les réglages ⚙");
            return;
        }

        {
            for (int c = 0; c <= g.GridCols; c++)
            {
                double x = Math.Round(Math.Min(c * _cellW, Bounds.Width - 1)) + 0.5;
                dc.DrawLine(GridPen, new Point(x, 0), new Point(x, Bounds.Height));
            }
            for (int r = 0; r <= g.GridRows; r++)
            {
                double y = Math.Round(Math.Min(r * _cellH, Bounds.Height - 1)) + 0.5;
                dc.DrawLine(GridPen, new Point(0, y), new Point(Bounds.Width, y));
            }
        }

        Item? dragged = null;
        foreach (var it in _items)
        {
            if (_dragging && it.S == _press) { dragged = it; continue; }
            DrawBadge(dc, it.S, it.R);
        }
        if (dragged is { } d)
        {
            DrawDropHint(dc);
            using (dc.PushOpacity(0.85))
                DrawBadge(dc, d.S, d.R.Translate(_dragPt - _pressPt));
        }
    }

    void DrawHint(DrawingContext dc, string text)
    {
        var ft = new FormattedText(text, CultureInfo.CurrentCulture, FlowDirection.LeftToRight, Face,
            Math.Clamp(Bounds.Width / 30, 10, 18), HintBrush) { TextAlignment = TextAlignment.Center, MaxTextWidth = Bounds.Width - 20 };
        var box = new Rect(Bounds.Width / 2 - ft.Width / 2 - 10, Bounds.Height / 2 - ft.Height / 2 - 6, ft.Width + 20, ft.Height + 12);
        dc.DrawRectangle(HintBack, null, box, 6, 6);
        dc.DrawText(ft, new Point(10, box.Y + 6));
    }

    void DrawBadge(DrawingContext dc, Student s, Rect r)
    {
        int level = Math.Min(s.Score, 4);
        double radius = Math.Min(r.Width, r.Height) * 0.22;
        dc.DrawRectangle(Fills[level], BadgePen, r, radius, radius);
        var fg = level >= 4 ? Light : Dark;

        double pad = r.Height * 0.12;
        var area = new Rect(r.X + pad, r.Y + r.Height * 0.06, r.Width - 2 * pad, r.Height * 0.88);
        if (s.Score > 0)
        {
            // Badge large : pastille à droite. Badge étroit : pastille dans le coin, le prénom garde la largeur.
            bool corner = r.Width < r.Height * 2.2;
            double ph = r.Height * (corner ? 0.4 : 0.5);
            var num = new FormattedText(s.Score.ToString(), CultureInfo.InvariantCulture, FlowDirection.LeftToRight, Bold,
                Math.Max(6, ph * 0.72), corner ? Light : fg);
            double pw = Math.Max(ph, num.Width + ph * 0.45);
            var pill = corner
                ? new Rect(r.Right - pw * 0.8, r.Top - ph * 0.25, pw, ph)
                : new Rect(r.Right - pw - r.Height * 0.14, r.Center.Y - ph / 2, pw, ph);
            dc.DrawRectangle(corner ? Bubble : Pill, null, pill, ph / 2, ph / 2);
            dc.DrawText(num, new Point(pill.Center.X - num.Width / 2, pill.Center.Y - num.Height / 2));
            if (corner) area = new Rect(area.X, area.Y + ph * 0.25, area.Width - pad * 0.5, area.Height - ph * 0.25);
            else area = area.WithWidth(Math.Max(4, pill.X - area.X - pad * 0.4));
        }

        double fontScale = State?.Settings.TextScale ?? 1;
        double baseSize = Math.Clamp(Math.Min(r.Height * 0.36, r.Width * 0.16) * fontScale, 6, 40);
        var name = NameText(s, area.Size, baseSize, fg);
        dc.DrawText(name, new Point(area.X, area.Center.Y - name.Height / 2));
    }

    /// <summary>Prénom ajusté à la place disponible : sur une ligne, ou sur deux (prénom composé/long)
    /// si cela permet un texte plus grand. Mis en cache.</summary>
    FormattedText NameText(Student s, Size area, double baseSize, IBrush fg)
    {
        string key = $"{s.Name}|{baseSize:F1}|{area.Width:F0}|{area.Height:F0}|{(fg == Light ? 1 : 0)}";
        if (_names.TryGetValue(s.Id, out var cached) && cached.Key == key) return cached.Text;

        string text = s.Name.Length > 0 ? s.Name : "?";
        var one = Make(text, baseSize, fg);
        double k = Math.Min(1, Math.Min(area.Width / Math.Max(1, one.Width), area.Height / Math.Max(1, one.Height)));
        string two = Placement.TwoLines(text);
        if (k < 1 && two != text)
        {
            var ft2 = Make(two, baseSize, fg);
            double k2 = Math.Min(1, Math.Min(area.Width / Math.Max(1, ft2.Width), area.Height / Math.Max(1, ft2.Height)));
            if (k2 > k * 1.05) { text = two; k = k2; }
        }
        var ft = Make(text, Math.Max(5, baseSize * k * 0.98), fg);
        ft.TextAlignment = TextAlignment.Center;
        ft.MaxTextWidth = Math.Max(area.Width, ft.Width) + 1;
        _names[s.Id] = (key, ft);
        return ft;
    }

    static FormattedText Make(string text, double size, IBrush fg) =>
        new(text, CultureInfo.CurrentCulture, FlowDirection.LeftToRight, Face, size, fg);

    void DrawDropHint(DrawingContext dc)
    {
        var (row, col) = CellAt(_dragPt, Group!);
        dc.DrawRectangle(DropFill, null, new Rect(col * _cellW, row * _cellH, _cellW, _cellH), 4, 4);
    }

    (int Row, int Col) CellAt(Point p, ClassGroup g) =>
        (Math.Clamp((int)(p.Y / _cellH), 0, g.GridRows - 1), Math.Clamp((int)(p.X / _cellW), 0, g.GridCols - 1));

    Item? HitItem(Point p)
    {
        for (int i = _items.Count - 1; i >= 0; i--)
            if (_items[i].R.Contains(p)) return _items[i];
        return null;
    }

    // ---------- Interactions ----------

    protected override void OnPointerPressed(PointerPressedEventArgs e)
    {
        base.OnPointerPressed(e);
        var p = e.GetCurrentPoint(this);
        if (HitItem(p.Position) is not { } hit) return;
        e.Handled = true;
        if (p.Properties.IsRightButtonPressed)
        {
            ShowMenu(hit.S);
            return;
        }
        if (!p.Properties.IsLeftButtonPressed) return;
        _press = hit.S;
        _pressRect = hit.R;
        _pressPt = _dragPt = p.Position;
        _dragging = _longFired = false;
        _pointer = e.Pointer;
        e.Pointer.Capture(this);
        _longPress.Start();
    }

    protected override void OnPointerMoved(PointerEventArgs e)
    {
        base.OnPointerMoved(e);
        if (_press == null) return;
        var pt = e.GetPosition(this);
        if (!_dragging)
        {
            var v = pt - _pressPt;
            if (Math.Sqrt(v.X * v.X + v.Y * v.Y) < DragThreshold) return;
            _dragging = true;
            _longPress.Stop();
        }
        _dragPt = pt;
        InvalidateVisual();
    }

    protected override void OnPointerReleased(PointerReleasedEventArgs e)
    {
        base.OnPointerReleased(e);
        if (_press is not { } s) return;
        bool drag = _dragging, longFired = _longFired;
        var pt = e.GetPosition(this);
        CancelInteraction();
        e.Handled = true;
        if (longFired || State == null) return;
        if (drag) Drop(s, pt);
        else if (e.InitialPressMouseButton == MouseButton.Left) State.AddScore(s, +1);
    }

    protected override void OnPointerCaptureLost(PointerCaptureLostEventArgs e)
    {
        base.OnPointerCaptureLost(e);
        if (_press != null && !_longFired) CancelInteraction();
    }

    void OnLongPress()
    {
        _longPress.Stop();
        if (_press is not { } s || _dragging) return;
        _longFired = true;
        _pointer?.Capture(null);
        ShowMenu(s);
    }

    void ShowMenu(Student s)
    {
        if (State is not { } st) return;
        var menu = new MenuFlyout();
        menu.Items.Add(new MenuItem { Header = s.Score > 0 ? $"{s.Name} — {s.Score}" : s.Name, IsEnabled = false, FontWeight = FontWeight.SemiBold });
        menu.Items.Add(new Separator());
        menu.Items.Add(MenuEntry("−1", s.Score > 0, () => st.AddScore(s, -1)));
        menu.Items.Add(MenuEntry("+1", true, () => st.AddScore(s, +1)));
        menu.Items.Add(MenuEntry("Remettre à 0", s.Score > 0, () => st.ResetScore(s)));
        menu.ShowAt(this, true);
    }

    static MenuItem MenuEntry(string header, bool enabled, Action action)
    {
        var item = new MenuItem { Header = header, IsEnabled = enabled };
        item.Click += (_, _) => action();
        return item;
    }

    void Drop(Student s, Point pt)
    {
        var g = Group;
        if (g == null || State == null || !g.Students.Contains(s)) return;
        var (row, col) = CellAt(pt, g);
        var others = _items.Where(i => i.S != s && i.S.Row == row && i.S.Col == col).OrderBy(i => i.S.Seat).ToList();
        int idx = others.Count(i => i.R.Bottom < pt.Y || (i.R.Top <= pt.Y && i.R.Center.X < pt.X));
        var members = others.Select(i => i.S).ToList();
        members.Insert(idx, s);
        bool same = s.Row == row && s.Col == col
            && g.Students.Where(o => o.Row == row && o.Col == col).OrderBy(o => o.Seat).SequenceEqual(members);
        if (same) return;
        State.Commit($"Déplacement de {s.Name}", () =>
        {
            s.Row = row;
            s.Col = col;
            for (int i = 0; i < members.Count; i++) members[i].Seat = i;
        }, Change.Layout);
    }
}
