// Query real screen size + DPI. Minimal P/Invoke only.
using System;
using System.Runtime.InteropServices;

class DpiQ {
    [DllImport("user32.dll")] static extern int GetSystemMetrics(int n);
    [DllImport("user32.dll")] static extern IntPtr GetDC(IntPtr h);
    [DllImport("user32.dll")] static extern int ReleaseDC(IntPtr h, IntPtr dc);
    [DllImport("user32.dll")] static extern int GetDeviceCaps(IntPtr dc, int i);

    static int Main() {
        int w = GetSystemMetrics(0);
        int h = GetSystemMetrics(1);
        IntPtr dc = GetDC(IntPtr.Zero);
        int dpi = GetDeviceCaps(dc, 88);
        ReleaseDC(IntPtr.Zero, dc);
        double scale = (double)dpi / 96.0;
        Console.WriteLine("screen=" + w + "x" + h + " dpi=" + dpi + " scale=" + scale.ToString("F2"));
        return 0;
    }
}
