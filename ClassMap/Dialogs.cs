using Avalonia.Controls;
using Avalonia.Layout;
using Avalonia.Media;

namespace ClassMap;

/// <summary>Petites boîtes de dialogue (Avalonia n'en fournit pas).</summary>
public static class Dialogs
{
    public static Task<bool> Confirm(Window owner, string text, string ok = "OK", string? cancel = "Annuler") =>
        Show(owner, text, null, ok, cancel).ContinueWith(t => t.Result != null, TaskScheduler.FromCurrentSynchronizationContext());

    public static Task Info(Window owner, string text) => Show(owner, text, null, "OK", null);

    public static Task<string?> Prompt(Window owner, string text, string initial = "") => Show(owner, text, initial, "OK", "Annuler");

    static Task<string?> Show(Window owner, string text, string? input, string ok, string? cancel)
    {
        var tcs = new TaskCompletionSource<string?>();
        var w = new Window
        {
            Title = "ClassMap",
            SizeToContent = SizeToContent.WidthAndHeight,
            CanResize = false,
            ShowInTaskbar = false,
            Topmost = owner.Topmost,
            WindowStartupLocation = WindowStartupLocation.CenterOwner,
        };
        var box = input == null ? null : new TextBox { Text = input, MinWidth = 260 };
        var okButton = new Button { Content = ok, IsDefault = true, MinWidth = 80, HorizontalContentAlignment = HorizontalAlignment.Center };
        okButton.Classes.Add("accent");
        okButton.Click += (_, _) =>
        {
            if (box != null && string.IsNullOrWhiteSpace(box.Text)) return;
            tcs.TrySetResult(box?.Text?.Trim() ?? "");
            w.Close();
        };
        var buttons = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8, HorizontalAlignment = HorizontalAlignment.Right };
        if (cancel != null)
        {
            var c = new Button { Content = cancel, IsCancel = true, MinWidth = 80, HorizontalContentAlignment = HorizontalAlignment.Center };
            c.Click += (_, _) => w.Close();
            buttons.Children.Add(c);
        }
        buttons.Children.Add(okButton);
        var panel = new StackPanel { Margin = new(18), Spacing = 14 };
        panel.Children.Add(new TextBlock { Text = text, TextWrapping = TextWrapping.Wrap, MaxWidth = 380 });
        if (box != null) panel.Children.Add(box);
        panel.Children.Add(buttons);
        w.Content = panel;
        w.Closed += (_, _) => tcs.TrySetResult(null);
        w.Opened += (_, _) => (box ?? (Control)okButton).Focus();
        box?.SelectAll();
        w.ShowDialog(owner);
        return tcs.Task;
    }
}
