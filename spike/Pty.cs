using System; using System.Runtime.InteropServices; using System.Text; using System.Threading;
public static class Pty {
  [StructLayout(LayoutKind.Sequential)] public struct COORD { public short X, Y; }
  [StructLayout(LayoutKind.Sequential, CharSet=CharSet.Unicode)] public struct SI { public int cb; public string r, d, t; public int x,y,xs,ys,xc,yc,fill,flags; public short sw, cb2; public IntPtr r2, i, o, e; }
  [StructLayout(LayoutKind.Sequential)] public struct SIEX { public SI si; public IntPtr attrs; }
  [StructLayout(LayoutKind.Sequential)] public struct PI { public IntPtr hp, ht; public int pid, tid; }
  [DllImport("kernel32.dll", SetLastError=true)] static extern int CreatePseudoConsole(COORD size, IntPtr hIn, IntPtr hOut, uint flags, out IntPtr hpc);
  [DllImport("kernel32.dll", SetLastError=true)] static extern void ClosePseudoConsole(IntPtr hpc);
  [DllImport("kernel32.dll", SetLastError=true)] static extern bool CreatePipe(out IntPtr r, out IntPtr w, IntPtr sa, int size);
  [DllImport("kernel32.dll", SetLastError=true)] static extern bool CloseHandle(IntPtr h);
  [DllImport("kernel32.dll", SetLastError=true)] static extern bool ReadFile(IntPtr h, byte[] buf, int n, out int read, IntPtr ov);
  [DllImport("kernel32.dll", SetLastError=true)] static extern bool InitializeProcThreadAttributeList(IntPtr list, int count, int flags, ref IntPtr size);
  [DllImport("kernel32.dll", SetLastError=true)] static extern bool UpdateProcThreadAttribute(IntPtr list, uint flags, IntPtr attr, IntPtr value, IntPtr size, IntPtr prev, IntPtr ret);
  [DllImport("kernel32.dll", SetLastError=true, CharSet=CharSet.Unicode)] static extern bool CreateProcessW(string app, StringBuilder cmd, IntPtr pa, IntPtr ta, bool inherit, uint flags, IntPtr env, string cwd, ref SIEX si, out PI pi);
  [DllImport("advapi32.dll", SetLastError=true, CharSet=CharSet.Unicode)] static extern bool CreateProcessWithLogonW(string u, string d, string p, int lf, string app, StringBuilder cmd, uint flags, IntPtr env, string cwd, ref SIEX si, out PI pi);
  [DllImport("advapi32.dll", SetLastError=true, CharSet=CharSet.Unicode)] static extern bool LogonUserW(string u, string d, string p, int type, int prov, out IntPtr tok);
  [DllImport("advapi32.dll", SetLastError=true, CharSet=CharSet.Unicode)] static extern bool CreateProcessAsUserW(IntPtr tok, string app, StringBuilder cmd, IntPtr pa, IntPtr ta, bool inherit, uint flags, IntPtr env, string cwd, ref SIEX si, out PI pi);
  [DllImport("kernel32.dll")] static extern uint WaitForSingleObject(IntPtr h, uint ms);
  public static string Run(string how, string user, string pw, string cmd) {
    IntPtr inR, inW, outR, outW;
    CreatePipe(out inR, out inW, IntPtr.Zero, 0); CreatePipe(out outR, out outW, IntPtr.Zero, 0);
    IntPtr hpc; var size = new COORD { X = 120, Y = 30 };
    int hr = CreatePseudoConsole(size, inR, outW, 0, out hpc);
    if (hr != 0) return "CreatePseudoConsole " + hr;
    IntPtr lsize = IntPtr.Zero; InitializeProcThreadAttributeList(IntPtr.Zero, 1, 0, ref lsize);
    IntPtr list = Marshal.AllocHGlobal(lsize);
    if (!InitializeProcThreadAttributeList(list, 1, 0, ref lsize)) return "InitAttr " + Marshal.GetLastWin32Error();
    if (!UpdateProcThreadAttribute(list, 0, (IntPtr)0x00020016, hpc, (IntPtr)IntPtr.Size, IntPtr.Zero, IntPtr.Zero)) return "UpdateAttr " + Marshal.GetLastWin32Error();
    var si = new SIEX(); si.si.cb = Marshal.SizeOf(typeof(SIEX)); si.attrs = list; PI pi; bool ok;
    uint flags = 0x00080000; // EXTENDED_STARTUPINFO_PRESENT
    if (how == "self") ok = CreateProcessW(null, new StringBuilder(cmd), IntPtr.Zero, IntPtr.Zero, false, flags, IntPtr.Zero, "C:\\cgspike", ref si, out pi);
    else if (how == "logon") ok = CreateProcessWithLogonW(user, ".", pw, 1, null, new StringBuilder(cmd), flags, IntPtr.Zero, "C:\\cgspike", ref si, out pi);
    else { IntPtr tok; if (!LogonUserW(user, ".", pw, 2, 0, out tok)) return "LogonUser " + Marshal.GetLastWin32Error();
      ok = CreateProcessAsUserW(tok, null, new StringBuilder(cmd), IntPtr.Zero, IntPtr.Zero, false, flags, IntPtr.Zero, "C:\\cgspike", ref si, out pi); }
    if (!ok) return how + ": create failed " + Marshal.GetLastWin32Error();
    CloseHandle(inR); CloseHandle(outW);
    var sb = new StringBuilder();
    var reader = new Thread(() => { var b = new byte[4096]; int n; while (ReadFile(outR, b, b.Length, out n, IntPtr.Zero) && n > 0) lock (sb) sb.Append(Encoding.UTF8.GetString(b, 0, n)); });
    reader.IsBackground = true; reader.Start();
    uint w = WaitForSingleObject(pi.hp, 20000);
    Thread.Sleep(1500); ClosePseudoConsole(hpc); reader.Join(3000);
    string text; lock (sb) text = sb.ToString();
    text = System.Text.RegularExpressions.Regex.Replace(text, "\x1b\\[[0-9;?]*[A-Za-z]|\x1b\\][^\x07]*\x07", "");
    return how + ": wait=" + w + " output=[" + text.Trim() + "]";
  }
}