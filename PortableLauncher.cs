using System;
using System.Diagnostics;
using System.IO;
using System.Text;
using System.Windows.Forms;

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
        string root = AppDomain.CurrentDomain.BaseDirectory;
        string python = Path.Combine(root, "artifacts", "runtime", "python", "pythonw.exe");
        string script = Path.Combine(root, "demo", "launch.py");
        string log = Path.Combine(root, "artifacts", "demo", "launcher.log");
        try
        {
            if (!File.Exists(python) || !File.Exists(script))
                throw new FileNotFoundException("软件文件不完整，请解压完整软件包，保留 exe 旁的全部文件夹。");
            Directory.CreateDirectory(Path.GetDirectoryName(log));
            var command = new StringBuilder("-X utf8 "); command.Append(Quote(script));
            foreach (string arg in args) { command.Append(' '); command.Append(Quote(arg)); }
            var info = new ProcessStartInfo(python, command.ToString()) {
                WorkingDirectory = root, UseShellExecute = false, CreateNoWindow = true,
                RedirectStandardOutput = true, RedirectStandardError = true,
                StandardOutputEncoding = Encoding.UTF8, StandardErrorEncoding = Encoding.UTF8
            };
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
                    MessageBox.Show("软件启动未能完成，请查看：\n" + log + "\n\n也可查看 artifacts/demo/client-startup.log。", "幻衣间", MessageBoxButtons.OK, MessageBoxIcon.Error);
                return process.ExitCode;
            }
        }
        catch (Exception error)
        {
            MessageBox.Show(error.Message, "幻衣间启动", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }
    }
}
