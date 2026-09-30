using System.Globalization;
using Avalonia;
using Avalonia.Controls;
using Avalonia.Input;
using Avalonia.Media;
using Avalonia.Media.Immutable;

namespace ClassMap;

/// <summary>Semainier : on « peint » les créneaux de la classe choisie (cliquer-glisser, pas de 15 min).</summary>
public sealed class WeekGrid : Control
{
    const double LabelW = 42, HeaderH = 20, RowH = 9;
    static int Rows => (Timetable.DayEnd - Timetable.DayStart) / Timetable.Step;

    static readonly string[] Palette =
        { "#A5D8FF", "#B2F2BB", "#FFD8A8", "#D0BFFF", "#FFC9C9", "#99E9F2", "#FFEC99", "#C3FAE8", "#EEBEFA", "#D8F5A2", "#FCC2D7", "#BAC8FF" };
    static readonly IImmutableSolidColorBrush Back = new ImmutableSolidColorBrush(Color.Parse("#14808080"));
    static readonly IImmutableSolidColorBrush Label = new ImmutableSolidColorBrush(Color.Parse("#909090"));
    static readonly IImmutableSolidColorBrush Ink = new ImmutableSolidColorBrush(Color.Parse("#1E1E1E"));
    static readonly IImmutableSolidColorBrush Eraser = new ImmutableSolidColorBrush(Color.Parse("#66FF5A5A"));
    static readonly IImmutableSolidColorBrush NowBrush = new ImmutableSolidColorBrush(Color.Parse("#FFE5383B"));
    static readonly ImmutablePen HourPen = new(new ImmutableSolidColorBrush(Color.Parse("#50808080")), 1);
    static readonly ImmutablePen QuarterPen = new(new ImmutableSolidColorBrush(Color.Parse("#20808080")), 1);
    static readonly ImmutablePen SlotPen = new(new ImmutableSolidColorBrush(Color.Parse("#40000000")), 1);
    static readonly Typeface Face = new(FontFamily.Default);
    static readonly Typeface Bold = new(FontFamily.Default, FontStyle.Normal, FontWeight.SemiBold);

    public AppState? State { get; set; }
    /// <summary>Classe à peindre ; null = gomme.</summary>
    public string? Brush { get; set; }

    int _day = -1, _r0, _r1;
    string? _paint;

    public static IBrush ColorOf(AppState s, string classId)
    {
        int i = s.Data.Classes.FindIndex(c => c.Id == classId);
        return new ImmutableSolidColorBrush(Color.Parse(Palette[Math.Max(0, i) % Palette.Length]));
    }

    protected override Size MeasureOverride(Size available) =>
        new(double.IsInfinity(available.Width) ? 520 : available.Width, HeaderH + Rows * RowH + 2);

    double ColW => (Bounds.Width - LabelW) / Timetable.Days.Length;
    double Y(int minute) => HeaderH + (minute - Timetable.DayStart) / (double)Timetable.Step * RowH;

    public override void Render(DrawingContext dc)
    {
        if (State is not { } st) return;
        double colW = ColW, bottom = Y(Timetable.DayEnd);
        dc.FillRectangle(Back, new Rect(LabelW, HeaderH, Bounds.Width - LabelW, bottom - HeaderH));

        for (int d = 0; d < Timetable.Days.Length; d++)
        {
            var t = Text(Timetable.Days[d], 12, Label, Bold);
            dc.DrawText(t, new Point(LabelW + d * colW + colW / 2 - t.Width / 2, 2));
        }
        for (int m = Timetable.DayStart; m <= Timetable.DayEnd; m += Timetable.Step)
        {
            double y = Math.Round(Y(m)) + 0.5;
            bool hour = m % 60 == 0;
            dc.DrawLine(hour ? HourPen : QuarterPen, new Point(LabelW, y), new Point(Bounds.Width, y));
            if (hour && m < Timetable.DayEnd)
                dc.DrawText(Text($"{m / 60}h", 10, Label, Face), new Point(4, y - 6));
        }
        for (int d = 0; d <= Timetable.Days.Length; d++)
        {
            double x = Math.Round(LabelW + d * colW) + 0.5;
            dc.DrawLine(HourPen, new Point(x, HeaderH), new Point(x, bottom));
        }

        foreach (var s in st.Data.Timetable)
        {
            if (s.Day >= Timetable.Days.Length) continue;
            var r = new Rect(LabelW + s.Day * colW + 1, Y(s.Start) + 1, colW - 2, Y(s.End) - Y(s.Start) - 1);
            dc.DrawRectangle(ColorOf(st, s.ClassId), SlotPen, r, 3, 3);
            if (r.Height >= 11 && st.Find(s.ClassId) is { } g)
            {
                var t = Text($"{g.Name}  {s.Start / 60}:{s.Start % 60:00}–{s.End / 60}:{s.End % 60:00}", 10, Ink, Bold);
                t.MaxTextWidth = Math.Max(10, r.Width - 6);
                t.MaxTextHeight = r.Height - 1;
                t.Trimming = TextTrimming.CharacterEllipsis;
                using (dc.PushClip(r))
                    dc.DrawText(t, new Point(r.X + 3, r.Y + 1));
            }
        }

        if (_day >= 0)
        {
            int a = Math.Min(_r0, _r1), b = Math.Max(_r0, _r1) + 1;
            var r = new Rect(LabelW + _day * colW + 1, HeaderH + a * RowH + 1, colW - 2, (b - a) * RowH - 1);
            dc.DrawRectangle(_paint == null ? Eraser : ColorOf(st, _paint), null, r, 3, 3);
        }

        var now = DateTime.Now;
        int nowDay = Timetable.DayIndex(now.DayOfWeek), nowMin = now.Hour * 60 + now.Minute;
        if (nowDay < Timetable.Days.Length && nowMin >= Timetable.DayStart && nowMin < Timetable.DayEnd)
        {
            double y = Y(nowMin);
            dc.FillRectangle(NowBrush, new Rect(LabelW + nowDay * colW, y - 1, colW, 2));
        }
    }

    static FormattedText Text(string s, double size, IBrush brush, Typeface face) =>
        new(s, CultureInfo.CurrentCulture, FlowDirection.LeftToRight, face, size, brush);

    (int Day, int Row)? CellAt(Point p)
    {
        if (p.X < LabelW || p.Y < HeaderH) return null;
        int day = (int)((p.X - LabelW) / ColW), row = (int)((p.Y - HeaderH) / RowH);
        if (day < 0 || day >= Timetable.Days.Length || row < 0 || row >= Rows) return null;
        return (day, row);
    }

    protected override void OnPointerPressed(PointerPressedEventArgs e)
    {
        base.OnPointerPressed(e);
        if (State is not { } st || CellAt(e.GetPosition(this)) is not { } cell) return;
        var props = e.GetCurrentPoint(this).Properties;
        _paint = props.IsRightButtonPressed ? null : Brush;
        // Cliquer sur un créneau de la classe choisie l'efface.
        var existing = Timetable.ClassAt(st.Data.Timetable, DateOfDay(cell.Day, Timetable.DayStart + cell.Row * Timetable.Step));
        if (_paint != null && existing == _paint) _paint = null;
        if (_paint == null && Brush == null && existing == null && !props.IsRightButtonPressed) return;
        _day = cell.Day;
        _r0 = _r1 = cell.Row;
        e.Pointer.Capture(this);
        e.Handled = true;
        InvalidateVisual();
    }

    protected override void OnPointerMoved(PointerEventArgs e)
    {
        base.OnPointerMoved(e);
        if (_day < 0) return;
        int row = Math.Clamp((int)((e.GetPosition(this).Y - HeaderH) / RowH), 0, Rows - 1);
        if (row == _r1) return;
        _r1 = row;
        InvalidateVisual();
    }

    protected override void OnPointerReleased(PointerReleasedEventArgs e)
    {
        base.OnPointerReleased(e);
        if (_day < 0 || State is not { } st) return;
        int day = _day, start = Timetable.DayStart + Math.Min(_r0, _r1) * Timetable.Step;
        int end = Timetable.DayStart + (Math.Max(_r0, _r1) + 1) * Timetable.Step;
        var paint = _paint;
        _day = -1;
        e.Pointer.Capture(null);
        st.Commit("Emploi du temps", () => Timetable.Paint(st.Data.Timetable, day, start, end, paint), Change.Structure);
        InvalidateVisual();
    }

    protected override void OnPointerCaptureLost(PointerCaptureLostEventArgs e)
    {
        base.OnPointerCaptureLost(e);
        if (_day < 0) return;
        _day = -1;
        InvalidateVisual();
    }

    /// <summary>Une date quelconque tombant ce jour-là à cette minute (pour Timetable.ClassAt).</summary>
    static DateTime DateOfDay(int day, int minute) => new DateTime(2024, 1, 1).AddDays(day).AddMinutes(minute); // 01/01/2024 = lundi
}
