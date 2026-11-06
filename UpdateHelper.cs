// Standalone .NET Framework updater. Runs outside 程序 after Python/launcher exit.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using System.Web.Script.Serialization;
using System.Windows.Forms;

internal static class UpdateHelper
{
    static JavaScriptSerializer Json = new JavaScriptSerializer { MaxJsonLength = 8*1024*1024 };
    static string folder, root, jobPath;
    static Dictionary<string,object> job;
    static bool quiet;
    static Form window;
    static Label status;
    static ProgressBar bar;
    // Extended-length IO is local to this updater; no OS/registry settings.
    // Safe() still validates ordinary absolute paths and every ancestor first.
    static string Io(string p) {
        if(p==null || p.StartsWith(@"\\?\"))return p;
        return p.StartsWith(@"\\") ? @"\\?\UNC\"+p.Substring(2) : @"\\?\"+p;
    }
    static bool Exists(string p) {return File.Exists(Io(p));}
    static bool DirExists(string p) {return Directory.Exists(Io(p));}
    static FileAttributes Attributes(string p) {return File.GetAttributes(Io(p));}
    static FileStream OpenRead(string p) {return File.OpenRead(Io(p));}
    static string ReadText(string p,Encoding e) {return File.ReadAllText(Io(p),e);}
    static void WriteText(string p,string text,Encoding e) {File.WriteAllText(Io(p),text,e);}
    static void Replace(string a,string b,string c) {File.Replace(Io(a),Io(b),Io(c));}
    static void Move(string a,string b) {File.Move(Io(a),Io(b));}
    static void CopyFile(string a,string b,bool overwrite) {File.Copy(Io(a),Io(b),overwrite);}
    static void Delete(string p) {File.Delete(Io(p));}
    static void CreateDirectory(string p) {Directory.CreateDirectory(Io(p));}
    static string Hash(string p) { using(var f=OpenRead(p)) using(var h=SHA256.Create()) return BitConverter.ToString(h.ComputeHash(f)).Replace("-", "").ToLowerInvariant(); }
    static Dictionary<string,object> Read(string p) { if(new FileInfo(Io(p)).Length>8*1024*1024) throw new Exception("更新记录过大"); return Json.Deserialize<Dictionary<string,object>>(ReadText(p,Encoding.UTF8)); }
    static void Write(string p, object value) {
        string t=p+".tmp";
        using(var f=new FileStream(Io(t),FileMode.Create,FileAccess.Write,FileShare.None,4096,FileOptions.WriteThrough)) {
            byte[] b=Encoding.UTF8.GetBytes(Json.Serialize(value)); f.Write(b,0,b.Length); f.Flush(true);
        }
        if(Exists(p)) Replace(t,p,null); else Move(t,p);
    }
    static string Safe(string baseDir,string relative) {
        if(Path.IsPathRooted(relative)||relative.Contains(":")||relative.Contains("\\")||relative.Split('/').Any(x=>x==".."||x=="."||x==""||x.EndsWith(".")||x.EndsWith(" "))) throw new Exception("更新路径不正确");
        foreach(string part in relative.Split('/')) {
            string stem=part.Split('.')[0].ToUpperInvariant();
            if(part.IndexOfAny(new[]{'<','>','"','|','?','*','\0'})>=0 || part.Any(c=>c<32) || new[]{"CON","PRN","AUX","NUL","COM1","COM2","COM3","COM4","COM5","COM6","COM7","COM8","COM9","LPT1","LPT2","LPT3","LPT4","LPT5","LPT6","LPT7","LPT8","LPT9"}.Contains(stem)) throw new Exception("不支持的更新文件名");
        }
        string r=Path.GetFullPath(baseDir).TrimEnd(Path.DirectorySeparatorChar);
        string p=Path.GetFullPath(Path.Combine(r,relative.Replace('/',Path.DirectorySeparatorChar)));
        if(!p.StartsWith(r+Path.DirectorySeparatorChar,StringComparison.OrdinalIgnoreCase)) throw new Exception("更新路径超出安装目录");
        for(string x=p; x!=null; x=Path.GetDirectoryName(x)) {
            if((Exists(x)||DirExists(x)) && (Attributes(x)&FileAttributes.ReparsePoint)!=0) throw new Exception("更新路径不能包含链接");
            if(String.Equals(x,r,StringComparison.OrdinalIgnoreCase)) break;
        }
        return p;
    }
    static bool Owned(string p) {
        if(p.Split('/').Contains("__pycache__")||p.EndsWith(".pyc",StringComparison.OrdinalIgnoreCase)||p.EndsWith(".pyo",StringComparison.OrdinalIgnoreCase))return false;
        if(p=="幻化柜.exe"||p=="使用说明.txt"||p=="程序/artifacts/compiled-runtime.json"||p=="程序/artifacts/source-runtime.json"||p=="程序/artifacts/package-manifest.json") return true;
        return new[]{"程序/demo/","程序/tools/","程序/docs/","程序/defaults/","程序/artifacts/runtime/","程序/artifacts/native/","程序/artifacts/cn-data/"}.Any(prefix=>p.StartsWith(prefix,StringComparison.Ordinal));
    }
    static Dictionary<string,object> Map(object o) { return (Dictionary<string,object>)o; }
    static string Text(object o) { return o==null?null:Convert.ToString(o); }
    static void Wait(int pid) {
        if(pid<=0 || pid==Process.GetCurrentProcess().Id) return;
        try { using(var p=Process.GetProcessById(pid)) if(!p.WaitForExit(120000)) throw new Exception("旧程序仍在运行，更新尚未修改任何文件"); }
        catch(ArgumentException) {}
    }
    static void Report(string message,int done,int total) {
        if(quiet) return;
        window.Invoke((Action)(()=>{ status.Text=message;bar.Value=Math.Min(100,done*100/Math.Max(1,total)); }));
    }
    static string MutexName() { using(var h=SHA256.Create()) return "Local\\HGG-"+BitConverter.ToString(h.ComputeHash(Encoding.UTF8.GetBytes(root.ToUpperInvariant()))).Replace("-",""); }
    static void Copy(string src,string dst) {
        try { CreateDirectory(Path.GetDirectoryName(dst));string t=dst+".update-tmp";CopyFile(src,t,true);if(Exists(dst)) Replace(t,dst,null);else Move(t,dst); }
        catch(IOException error) { throw new IOException("更新文件替换失败："+dst,error); }
    }
    static Dictionary<string,object> Entries() {
        var files=Map(job["files"]);var removed=Map(job["removed"]);var all=new Dictionary<string,object>(StringComparer.OrdinalIgnoreCase);
        foreach(var e in files) { if(!Owned(e.Key)||all.ContainsKey(e.Key))throw new Exception("更新包包含非程序或重复文件");Safe(root,e.Key);all.Add(e.Key,e.Value); }
        foreach(var e in removed) { if(!Owned(e.Key)||all.ContainsKey(e.Key))throw new Exception("移除清单包含非程序或重复文件");Safe(root,e.Key);all.Add(e.Key,new Dictionary<string,object>{{"old_sha256",e.Value},{"sha256",null}}); }
        return all;
    }
    static void Rollback() {
        var snapshot=Read(Path.Combine(folder,"backup.json")); int n=0;
        foreach(var e in snapshot) {
            var r=Map(e.Value);string target=Safe(root,e.Key);string old=Text(r["old"]),current=Exists(target)?Hash(target):null;
            if(current!=null && current!=old && current!=Text(r["new"])) throw new Exception("回滚时发现外部修改，已保留文件："+e.Key);
            if(old!=null) {
                string backup=Safe(Path.Combine(folder,"backup"),e.Key);
                if(Hash(backup)!=old)throw new Exception("回滚备份校验失败");
                if(current!=old)Copy(backup,target);
            } else if(Exists(target))Delete(target);
            Report("正在恢复原版本…",++n,snapshot.Count);
        }
        Delete(Safe(root,"缓存/updates/pending.json"));Write(Path.Combine(folder,"result.json"),new{status="rolled_back"});
    }
    static void Execute(bool recover) {
        job=Read(jobPath);root=Path.GetFullPath((string)job["root"]).TrimEnd(Path.DirectorySeparatorChar);
        string expected=Safe(root,"缓存/updates/"+new DirectoryInfo(folder).Name+"/job.json");
        if(!String.Equals(expected,jobPath,StringComparison.OrdinalIgnoreCase))throw new Exception("更新任务不属于本安装目录");
        string stage=Safe(root,"缓存/updates/"+new DirectoryInfo(folder).Name+"/stage");
        if(!String.Equals(stage,(string)job["stage"],StringComparison.OrdinalIgnoreCase))throw new Exception("更新暂存目录不正确");
        if(!recover) { Wait(Convert.ToInt32(job["pid"]));Wait(Convert.ToInt32(job["launcher_pid"])); }
        using(var mutex=new Mutex(false,MutexName())) {
            bool locked=false;
            try { try { locked=mutex.WaitOne(10000); } catch(AbandonedMutexException) { locked=true; }
                if(!locked)throw new Exception("另一个客户端仍在使用本安装目录");
                var all=Entries();
                string pending=Safe(root,"缓存/updates/pending.json");
                if(recover) {
                    // Another updater may have committed while this recovery
                    // request waited for the installation mutex.
                    if(!Exists(pending))return;
                    if(!String.Equals(Text(Read(pending)["job"]),jobPath,StringComparison.OrdinalIgnoreCase))throw new Exception("待恢复更新任务已变化");
                    Rollback();return;
                }
                if(Exists(pending))throw new Exception("存在尚未恢复的更新，请重新启动软件");
                var snapshot=new Dictionary<string,object>();int n=0;
                foreach(var e in all) {
                    var r=Map(e.Value);string dst=Safe(root,e.Key);string old=Exists(dst)?Hash(dst):null;
                    string wanted=Text(r["sha256"]);
                    if(old!=Text(r["old_sha256"]) && old!=wanted)throw new Exception("原程序校验失败，未覆盖："+e.Key);
                    if(wanted!=null && Hash(Safe(stage,e.Key))!=wanted)throw new Exception("暂存文件校验失败");
                    if(old!=null)Copy(dst,Safe(Path.Combine(folder,"backup"),e.Key));
                    snapshot.Add(e.Key,new Dictionary<string,object>{{"old",old},{"new",wanted}});
                    Report("正在备份程序文件…",++n,all.Count);
                }
                Write(Path.Combine(folder,"backup.json"),snapshot);
                Write(pending,new{job=jobPath,helper=Path.Combine(folder,"UpdateHelper.exe"),sha256=Hash(Path.Combine(folder,"UpdateHelper.exe"))});
                try {
                    n=0;
                    foreach(var e in all) {
                        var snap=Map(snapshot[e.Key]);string dst=Safe(root,e.Key);string old=Exists(dst)?Hash(dst):null;
                        if(old!=Text(snap["old"]))throw new Exception("更新期间程序文件发生变化");
                        if(snap["new"]!=null) {
                            if(old!=Text(snap["new"]))Copy(Safe(stage,e.Key),dst);
                        } else if(Exists(dst))Delete(dst);
                        Report("正在更新程序文件…",++n,all.Count);
                    }
                    // Probe the real installed Python/Qt route before commit.
                    string python=Safe(root,"程序/artifacts/runtime/python/python.exe");
                    {
                        var info=new ProcessStartInfo(python,"-B -X utf8 -c \"import launch; raise SystemExit(launch.main())\" --startup-check") {WorkingDirectory=Path.Combine(root,"程序"),UseShellExecute=false,CreateNoWindow=true};
                        info.EnvironmentVariables.Remove("PYTHONHOME");info.EnvironmentVariables.Remove("PYTHONPATH");
                        using(var process=Process.Start(info)) {
                            if(!process.WaitForExit(60000)) {process.Kill();process.WaitForExit();throw new Exception("新版本启动验证超时");}
                            if(process.ExitCode!=0)throw new Exception("新版本启动验证未通过");
                        }
                    }
                    Delete(pending);Write(Path.Combine(folder,"result.json"),new{status="updated",version=job["version"]});
                } catch { Rollback();throw; }
            } finally { if(locked)mutex.ReleaseMutex(); }
        }
        if(!recover && Convert.ToBoolean(job["restart"]))Process.Start(new ProcessStartInfo(Path.Combine(root,"幻化柜.exe")){UseShellExecute=true,WorkingDirectory=root});
    }
    [STAThread] static int Main(string[] args) {
        AppContext.SetSwitch("Switch.System.IO.UseLegacyPathHandling",false);
        AppContext.SetSwitch("Switch.System.IO.BlockLongPaths",false);
        quiet=args.Contains("--headless");bool recover=args.Contains("--recover");
        if(args.Length<1)return 2;
        jobPath=Path.GetFullPath(args[0]);folder=Path.GetDirectoryName(jobPath);
        Exception error=null;
        if(quiet) {try{Execute(recover);}catch(Exception e){error=e;} }
        else {
            Application.EnableVisualStyles();window=new Form{Text="幻化柜 · 软件更新",Width=460,Height=160,StartPosition=FormStartPosition.CenterScreen,ControlBox=false};
            status=new Label{Text="等待客户端保存数据并退出…",Left=20,Top=20,Width=410,Height=35};bar=new ProgressBar{Left=20,Top=65,Width=400};window.Controls.Add(status);window.Controls.Add(bar);
            window.Shown+=(sender,e)=>{new Thread(()=>{try{Execute(recover);}catch(Exception ex){error=ex;}finally{window.BeginInvoke((Action)(()=>window.Close()));}}){IsBackground=true}.Start();};Application.Run(window);
        }
        if(error!=null) {
            try {WriteText(Path.Combine(folder,"error.log"),error.ToString(),Encoding.UTF8);} catch {}
            if(!quiet)MessageBox.Show("更新未完成。原有用户数据未修改。\n"+error.Message+"\n记录："+folder,"幻化柜更新",MessageBoxButtons.OK,MessageBoxIcon.Error);
            return 1;
        }
        return 0;
    }
}
