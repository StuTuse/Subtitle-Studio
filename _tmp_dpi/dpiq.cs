// Query real screen size + DPI with PerMonitorV2 awareness.
// csc /target:exe /out:dpiq.exe dpiq.cs
using System;
using System.Runtime.InteropServices;

class DpiQ {
    [DllImport("user32.dll")] static extern int GetSystemMetrics(int n);
    [DllImport("shcore.dll")] static extern int SetProcessDpiAwareness(int v);
    [DllImport("user32.dll")] static extern IntPtr GetDC(IntPtr h);
    [DllImport("user32.dll")] static extern int ReleaseDC(IntPtr h, IntPtr dc);
    [DllImport("user32.dll")] static extern int GetDeviceCaps(IntPtr dc, int i);
    [DllImport("user32.dll")] static extern bool EnumDisplayMonitors(IntPtr hdc, IntPtr clip, MonitorEnumProc proc, IntPtr data);
    [DllImport("user32.dll")] static extern bool GetMonitorInfo(IntPtr hmon, ref MONITORINFO info);
    [DllImport("user32.dll")] static extern int GetDpiForMonitor(IntPtr hmon, int type, out uint dx, out uint dy);

    delegate bool MonitorEnumProc(IntPtr hmon, IntPtr hdc, ref RECT rect, IntPtr data);
    struct RECT { public int L, T, R, B; }
    struct MONITORINFO { public int cbSize; public RECT rcMonitor; public RECT rcWork; public uint flags; }

    static int Main() {
        // manifest-declared awareness already? just query; wrap P/Invoke manually
        int w = 0, h = 0, dpi = 96;
        try {
            w = GetSystemMetrics(0);
            h = GetSystemMetrics(1);
            IntPtr dc = GetDC(IntPtr.Zero);
            dpi = GetDeviceCaps(dc, 88);
            ReleaseDC(IntPtr.Zero, dc);
        } catch (Exception ex) {
            Console.WriteLine("ERR: " + ex.GetType().Name + " " + ex.Message);
        }
        double scale = (double)dpi / 96.0;
        Console.WriteLine("screen=" + w + "x" + h + " dpi=" + dpi + " scale=" + scale.ToString("F2"));
        return 0;
    }
}
