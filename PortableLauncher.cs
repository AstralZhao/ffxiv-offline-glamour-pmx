using System;
using System.Diagnostics;
using System.IO;
using System.Text;
using System.Windows.Forms;
using System.Reflection;
using System.Security.Cryptography;
using System.Threading;
using System.Collections.Generic;
using System.Web.Script.Serialization;

[assembly: AssemblyTitle("幻化柜")]
[assembly: AssemblyProduct("幻化柜")]

internal static class PortableLauncher
{
    // Windows CRT quoting: preserve Unicode, spaces, quotes and trailing slashes.
    private static string Quote(string value)
    {
        var result = new StringBuilder("\"");
        int slashes = 0;
        foreach (char ch in value)
        {
            if (ch == '\\') { slashes++; continue; }
            result.Append('\\', ch == '"' ? slashes * 2 + 1 : slashes);
            result.Append(ch); slashes = 0;
        }
        result.Append('\\', slashes * 2); result.Append('"');
        return result.ToString();
    }

    [STAThread]
    private static int Main(string[] args)
    {
        string install = AppDomain.CurrentDomain.BaseDirectory.TrimEnd(Path.DirectorySeparatorChar);
        string root = install;
        Mutex instance = null; bool owns = false;
        string program = Path.Combine(root, "程序");
        bool compiled = File.Exists(Path.Combine(program, "artifacts", "compiled-runtime.json")) || File.Exists(Path.Combine(program, "artifacts", "source-runtime.json"));
        if (compiled) root = program;
        string python = Path.Combine(root, "artifacts", "runtime", "python", "pythonw.exe");
        string script = Path.Combine(root, "demo", "launch.py");
        string log = Path.Combine(install, "缓存", "logs", "launcher.log");
        try
        {
            string pending = Path.Combine(install,"缓存","updates","pending.json");
            if(compiled && File.Exists(pending)) {
                var record = new JavaScriptSerializer().Deserialize<Dictionary<string,string>>(File.ReadAllText(pending,Encoding.UTF8));
                string helper=Path.GetFullPath(record["helper"]), job=Path.GetFullPath(record["job"]);
                string managed=Path.Combine(install,"缓存","updates")+Path.DirectorySeparatorChar;
                if(!helper.StartsWith(managed,StringComparison.OrdinalIgnoreCase)||!job.StartsWith(managed,StringComparison.OrdinalIgnoreCase)) throw new Exception("更新恢复路径不正确");
                foreach(string file in new[]{helper,job,pending}) {
                    for(string part=file; part!=null; part=Path.GetDirectoryName(part)) {
                        if((File.Exists(part)||Directory.Exists(part)) && (File.GetAttributes(part)&FileAttributes.ReparsePoint)!=0)throw new Exception("更新恢复路径不能包含链接");
                        if(String.Equals(part,install,StringComparison.OrdinalIgnoreCase))break;
                    }
                }
                string hash;
                using(var f=File.OpenRead(helper))using(var h=SHA256.Create())hash=BitConverter.ToString(h.ComputeHash(f)).Replace("-","").ToLowerInvariant();
                if(hash!=record["sha256"])throw new Exception("更新恢复程序校验失败");
                using(var recovery=Process.Start(new ProcessStartInfo(helper,Quote(job)+" --recover"){UseShellExecute=false,WorkingDirectory=Path.GetDirectoryName(helper)})) {
                    recovery.WaitForExit();if(recovery.ExitCode!=0)throw new Exception("更新恢复未完成，请查看缓存/updates中的记录");
                }
            }
            if(compiled) {
                string name;
                using(var h=SHA256.Create())name="Local\\HGG-"+BitConverter.ToString(h.ComputeHash(Encoding.UTF8.GetBytes(install.ToUpperInvariant()))).Replace("-","");
                instance=new Mutex(false,name);
                try { owns=instance.WaitOne(0); } catch(AbandonedMutexException) { owns=true; }
                if(!owns)throw new Exception("本目录的幻化柜已在运行，请切换到已打开的窗口。");
            }
            if (!File.Exists(python) || (!compiled && !File.Exists(script)))
                throw new FileNotFoundException("软件文件不完整，请解压完整软件包，保留 exe 旁的全部文件夹。");
            Directory.CreateDirectory(Path.GetDirectoryName(log));
            var command = new StringBuilder("-B -X utf8 ");
            if (compiled) { command.Append("-c "); command.Append(Quote("import launch; raise SystemExit(launch.main())")); }
            else command.Append(Quote(script));
            foreach (string arg in args) { command.Append(' '); command.Append(Quote(arg)); }
            var info = new ProcessStartInfo(python, command.ToString()) {
                WorkingDirectory = root, UseShellExecute = false, CreateNoWindow = true,
                RedirectStandardOutput = true, RedirectStandardError = true,
                StandardOutputEncoding = Encoding.UTF8, StandardErrorEncoding = Encoding.UTF8
            };
            info.EnvironmentVariables["HGG_LAUNCHER_PID"] = Process.GetCurrentProcess().Id.ToString();
            info.EnvironmentVariables.Remove("PYTHONHOME");info.EnvironmentVariables.Remove("PYTHONPATH");
            using (var process = new Process())
            using (var writer = new StreamWriter(log, true, new UTF8Encoding(false)))
            {
                writer.AutoFlush = true;
                var sync = new object();
                DataReceivedEventHandler receive = (sender, line) => {
                    if (line.Data != null) lock (sync) writer.WriteLine(line.Data);
                };
                process.StartInfo = info;
                process.OutputDataReceived += receive; process.ErrorDataReceived += receive;
                process.Start(); process.BeginOutputReadLine(); process.BeginErrorReadLine();
                process.WaitForExit();
                if (process.ExitCode != 0)
                    MessageBox.Show("软件启动未能完成，请查看：\n" + log + "\n\n也可查看 缓存/logs/client-startup.log。", "幻化柜", MessageBoxButtons.OK, MessageBoxIcon.Error);
                return process.ExitCode;
            }
        }
        catch (Exception error)
        {
            MessageBox.Show(error.Message, "幻化柜启动", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }
        finally { if(owns)instance.ReleaseMutex();if(instance!=null)instance.Dispose(); }
    }
}
