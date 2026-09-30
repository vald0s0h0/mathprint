using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Runtime.Versioning;
using Microsoft.Win32.SafeHandles;

namespace ClassMap;

/// <summary>
/// Éjection de la clé par l'API Windows officielle (Configuration Manager : CM_Request_Device_Eject).
///
/// Un programme lancé depuis la clé ne peut pas l'éjecter lui-même : son propre .exe y est ouvert,
/// Windows refuserait (« périphérique en cours d'utilisation »). ClassMap identifie donc le
/// périphérique USB ici, puis confie l'appel à PowerShell (fourni avec Windows, lancé depuis
/// System32, rien n'est copié sur le PC) qui attend la fermeture de ClassMap, éjecte, et affiche
/// le motif exact en cas de refus. Si PowerShell est bloqué par l'établissement : repli sur la
/// fenêtre Windows « Retirer le périphérique en toute sécurité ».
/// </summary>
[SupportedOSPlatform("windows")]
public static partial class UsbEject
{
    public enum Kind { Ready, NotRemovable, Failed }
    public sealed record Plan(Kind Kind, string Drive, string? DeviceId = null, string? Error = null);

    const uint FILE_SHARE_READ = 1, FILE_SHARE_WRITE = 2, OPEN_EXISTING = 3;
    const uint IOCTL_STORAGE_GET_DEVICE_NUMBER = 0x2D1080, IOCTL_STORAGE_QUERY_PROPERTY = 0x2D1400;
    const uint DIGCF_PRESENT = 0x2, DIGCF_DEVICEINTERFACE = 0x10;
    const uint BusTypeUsb = 7, BusTypeSd = 0xC, BusTypeMmc = 0xD;
    static readonly Guid DiskInterface = new("53f56307-b6bf-11d0-94f2-00a0c91efb8b");

    [StructLayout(LayoutKind.Sequential)]
    struct StorageDeviceNumber { public uint DeviceType, DeviceNumber, PartitionNumber; }

    [StructLayout(LayoutKind.Sequential)]
    struct DeviceInterfaceData { public uint cbSize; public Guid InterfaceClassGuid; public uint Flags; public nint Reserved; }

    [StructLayout(LayoutKind.Sequential)]
    struct DevInfoData { public uint cbSize; public Guid ClassGuid; public uint DevInst; public nint Reserved; }

    [LibraryImport("kernel32.dll", EntryPoint = "CreateFileW", SetLastError = true, StringMarshalling = StringMarshalling.Utf16)]
    private static partial SafeFileHandle CreateFile(string name, uint access, uint share, nint security, uint disposition, uint flags, nint template);

    [LibraryImport("kernel32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static unsafe partial bool DeviceIoControl(SafeFileHandle device, uint code, void* inBuffer, uint inSize,
        void* outBuffer, uint outSize, out uint returned, nint overlapped);

    [LibraryImport("setupapi.dll", EntryPoint = "SetupDiGetClassDevsW", SetLastError = true)]
    private static partial nint SetupDiGetClassDevs(in Guid classGuid, nint enumerator, nint parent, uint flags);

    [LibraryImport("setupapi.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static partial bool SetupDiEnumDeviceInterfaces(nint set, nint devInfo, in Guid guid, uint index, ref DeviceInterfaceData data);

    [LibraryImport("setupapi.dll", EntryPoint = "SetupDiGetDeviceInterfaceDetailW", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static partial bool SetupDiGetDeviceInterfaceDetail(nint set, ref DeviceInterfaceData data, nint detail, uint size,
        out uint required, ref DevInfoData devInfo);

    [LibraryImport("setupapi.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static partial bool SetupDiDestroyDeviceInfoList(nint set);

    [LibraryImport("cfgmgr32.dll")]
    private static partial uint CM_Get_Parent(out uint parent, uint devInst, uint flags);

    [LibraryImport("cfgmgr32.dll", EntryPoint = "CM_Get_Device_IDW")]
    private static unsafe partial uint CM_Get_Device_ID(uint devInst, char* buffer, uint length, uint flags);

    /// <summary>Identifie la clé qui contient <paramref name="dataDir"/>. Aucun handle ne reste ouvert.</summary>
    public static Plan Prepare(string dataDir)
    {
        string? root = Path.GetPathRoot(Path.GetFullPath(dataDir));
        if (string.IsNullOrEmpty(root) || root.StartsWith(@"\\")) return new Plan(Kind.NotRemovable, root ?? "");
        string drive = root.TrimEnd('\\');
        string? system = Path.GetPathRoot(Environment.SystemDirectory)?.TrimEnd('\\');
        if (string.Equals(drive, system, StringComparison.OrdinalIgnoreCase)) return new Plan(Kind.NotRemovable, drive);

        DriveType type;
        try { type = new DriveInfo(root).DriveType; }
        catch { return new Plan(Kind.NotRemovable, drive); }
        if (type is not (DriveType.Removable or DriveType.Fixed)) return new Plan(Kind.NotRemovable, drive);

        try
        {
            using var volume = CreateFile(@"\\.\" + drive, 0, FILE_SHARE_READ | FILE_SHARE_WRITE, 0, OPEN_EXISTING, 0, 0);
            if (volume.IsInvalid)
                return new Plan(Kind.Failed, drive, Error: $"accès au lecteur {drive} refusé (erreur {Marshal.GetLastPInvokeError()}).");

            uint bus = BusType(volume);
            bool usb = bus is BusTypeUsb or BusTypeSd or BusTypeMmc;
            if (!usb && type != DriveType.Removable) return new Plan(Kind.NotRemovable, drive); // disque interne : jamais éjecté

            if (!DeviceNumber(volume, out var number))
                return new Plan(Kind.Failed, drive, Error: $"numéro de disque introuvable (erreur {Marshal.GetLastPInvokeError()}).");

            uint disk = FindDisk(number);
            if (disk == 0) return new Plan(Kind.Failed, drive, Error: "disque introuvable dans le gestionnaire de périphériques.");

            // On éjecte le périphérique USB (parent du disque), comme l'Explorateur.
            uint target = CM_Get_Parent(out uint parent, disk, 0) == 0 ? parent : disk;
            string? id = DeviceId(target);
            return id == null
                ? new Plan(Kind.Failed, drive, Error: "identifiant du périphérique illisible.")
                : new Plan(Kind.Ready, drive, id);
        }
        catch (Exception e)
        {
            return new Plan(Kind.Failed, drive, Error: e.Message);
        }
    }

    static unsafe uint BusType(SafeFileHandle volume)
    {
        byte* query = stackalloc byte[12]; // StorageDeviceProperty, PropertyStandardQuery
        new Span<byte>(query, 12).Clear();
        byte* output = stackalloc byte[1024];
        if (!DeviceIoControl(volume, IOCTL_STORAGE_QUERY_PROPERTY, query, 12, output, 1024, out uint got, 0) || got < 32) return 0;
        return *(uint*)(output + 28); // STORAGE_DEVICE_DESCRIPTOR.BusType
    }

    static unsafe bool DeviceNumber(SafeFileHandle handle, out StorageDeviceNumber number)
    {
        StorageDeviceNumber n;
        bool ok = DeviceIoControl(handle, IOCTL_STORAGE_GET_DEVICE_NUMBER, null, 0, &n, (uint)sizeof(StorageDeviceNumber), out _, 0);
        number = n;
        return ok;
    }

    static unsafe uint FindDisk(StorageDeviceNumber wanted)
    {
        nint set = SetupDiGetClassDevs(in DiskInterface, 0, 0, DIGCF_PRESENT | DIGCF_DEVICEINTERFACE);
        if (set == -1) return 0;
        try
        {
            for (uint i = 0; ; i++)
            {
                var itf = new DeviceInterfaceData { cbSize = (uint)sizeof(DeviceInterfaceData) };
                if (!SetupDiEnumDeviceInterfaces(set, 0, in DiskInterface, i, ref itf)) return 0;
                var info = new DevInfoData { cbSize = (uint)sizeof(DevInfoData) };
                SetupDiGetDeviceInterfaceDetail(set, ref itf, 0, 0, out uint size, ref info);
                if (size == 0) continue;
                nint detail = Marshal.AllocHGlobal((int)size);
                try
                {
                    *(uint*)detail = IntPtr.Size == 8 ? 8u : 6u; // cbSize de SP_DEVICE_INTERFACE_DETAIL_DATA_W
                    if (!SetupDiGetDeviceInterfaceDetail(set, ref itf, detail, size, out _, ref info)) continue;
                    string path = new((char*)(detail + 4));
                    using var disk = CreateFile(path, 0, FILE_SHARE_READ | FILE_SHARE_WRITE, 0, OPEN_EXISTING, 0, 0);
                    if (!disk.IsInvalid && DeviceNumber(disk, out var n)
                        && n.DeviceType == wanted.DeviceType && n.DeviceNumber == wanted.DeviceNumber)
                        return info.DevInst;
                }
                finally { Marshal.FreeHGlobal(detail); }
            }
        }
        finally { SetupDiDestroyDeviceInfoList(set); }
    }

    static unsafe string? DeviceId(uint devInst)
    {
        char* buffer = stackalloc char[400];
        return CM_Get_Device_ID(devInst, buffer, 400, 0) == 0 ? new string(buffer) : null;
    }

    /// <summary>Lance l'éjection différée (après la fermeture de ClassMap). false si PowerShell est bloqué.</summary>
    public static bool LaunchHelper(Plan plan)
    {
        var psi = new ProcessStartInfo(Path.Combine(Environment.SystemDirectory, @"WindowsPowerShell\v1.0\powershell.exe"))
        {
            UseShellExecute = false,
            CreateNoWindow = true,
            WorkingDirectory = Environment.SystemDirectory, // surtout pas la clé : ce serait un handle ouvert dessus
        };
        foreach (var a in new[] { "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-Command", Script(plan) })
            psi.ArgumentList.Add(a);
        try
        {
            using var p = Process.Start(psi);
            return p != null;
        }
        catch { return false; }
    }

    /// <summary>Fenêtre Windows « Retirer le périphérique en toute sécurité ».</summary>
    public static void OpenSafeRemoval()
    {
        try
        {
            var psi = new ProcessStartInfo(Path.Combine(Environment.SystemDirectory, "rundll32.exe"), "shell32.dll,Control_RunDLL hotplug.dll")
            { UseShellExecute = false, WorkingDirectory = Environment.SystemDirectory };
            using var _ = Process.Start(psi);
        }
        catch { }
    }

    static string Q(string s) => "'" + s.Replace("'", "''") + "'";

    // Script sans guillemets doubles (passage en ligne de commande) ; chaînes en guillemets simples échappés.
    static string Script(Plan plan) => $$"""
        $id = {{Q(plan.DeviceId!)}}
        $drive = {{Q(plan.Drive)}}
        function Say([string]$m, [int]$type, [int]$wait) {
          try { return (New-Object -ComObject WScript.Shell).Popup($m, $wait, 'ClassMap', $type) } catch { return -1 }
        }
        function Fallback([string]$m) {
          $r = Say ($m + [char]10 + [char]10 + {{Q("Ouvrir « Retirer le périphérique en toute sécurité » ?")}}) 52 0
          if ($r -eq 6 -or $r -eq -1) { Start-Process -FilePath 'rundll32.exe' -ArgumentList 'shell32.dll,Control_RunDLL hotplug.dll' }
        }
        try { Wait-Process -Id {{Environment.ProcessId}} -Timeout 30 -ErrorAction SilentlyContinue } catch {}
        Start-Sleep -Milliseconds 500
        try {
          $ab = [AppDomain]::CurrentDomain.DefineDynamicAssembly((New-Object Reflection.AssemblyName('ClassMapEject')), 'Run')
          $tb = $ab.DefineDynamicModule('ClassMapEject').DefineType('CfgMgr', 'Public,Class')
          $m = $tb.DefinePInvokeMethod('CM_Locate_DevNodeW', 'cfgmgr32.dll', 'Public,Static,PinvokeImpl', 'Standard', [int], [Type[]]@([int].MakeByRefType(), [string], [int]), 'Winapi', 'Unicode')
          $m.SetImplementationFlags('PreserveSig')
          $m = $tb.DefinePInvokeMethod('CM_Request_Device_EjectW', 'cfgmgr32.dll', 'Public,Static,PinvokeImpl', 'Standard', [int], [Type[]]@([int], [int].MakeByRefType(), [Text.StringBuilder], [int], [int]), 'Winapi', 'Unicode')
          $m.SetImplementationFlags('PreserveSig')
          $cm = $tb.CreateType()
        } catch {
          Fallback ({{Q("La politique de sécurité de ce PC empêche l'éjection automatique de ")}} + $drive + {{Q(". Vos données sont enregistrées.")}})
          exit
        }
        $dev = 0
        $cr = $cm::CM_Locate_DevNodeW([ref]$dev, $id, 0)
        if ($cr -ne 0) {
          if ($cr -ne 13) { Fallback ({{Q("Périphérique introuvable (code ")}} + $cr + {{Q("). Vos données sont enregistrées.")}}) }
          exit
        }
        $veto = 0
        $name = New-Object Text.StringBuilder 520
        for ($i = 0; $i -lt 6; $i++) {
          $veto = 0
          [void]$name.Clear()
          $cr = $cm::CM_Request_Device_EjectW($dev, [ref]$veto, $name, 520, 0)
          if ($cr -eq 0 -and $veto -eq 0) {
            [void](Say ({{Q("La clé ")}} + $drive + {{Q(" peut être retirée en toute sécurité.")}}) 64 4)
            exit
          }
          if ($veto -eq 13) { exit }
          Start-Sleep -Milliseconds 800
        }
        $why = switch ($veto) {
          {$_ -in 2,3,5} { {{Q("un programme ou une fenêtre utilise encore la clé (Explorateur, document ouvert, antivirus…). Fermez-le puis réessayez.")}} }
          4 { {{Q("un service Windows utilise encore la clé.")}} }
          12 { {{Q("droits insuffisants (politique de l'établissement).")}} }
          default { {{Q("Windows a refusé (code ")}} + $cr + ', veto ' + $veto + ').' }
        }
        if ($name.Length -gt 0 -and $veto -in 3,4) { $why = $why + ' [' + $name.ToString() + ']' }
        Fallback ({{Q("Éjection de ")}} + $drive + {{Q(" impossible : ")}} + $why + [char]10 + {{Q("Vos données sont enregistrées.")}})
        """;
}
