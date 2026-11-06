"""Gitee release discovery and verified, exact-base differential updates.

No full-distribution fallback. Download and validation finish before the GUI
shuts down. A separate native helper owns replacement/rollback after exit.
"""
from pathlib import Path,PurePosixPath
import hashlib,json,os,re,shutil,subprocess,urllib.parse,urllib.request,uuid,zipfile
from app_paths import paths,no_links,_atomic_json
from app_version import API,RELEASES,version_key,current_version,can_install

MANIFEST='update-manifest.json'
LIMIT=8*1024**2


def digest(path):
    with Path(path).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()


def program_file(name):
    """Only explicit program-owned paths, never personal/cache/resource paths."""
    if not isinstance(name,str) or '\\' in name or ':' in name or len(name)>300:return False
    p=PurePosixPath(name)
    if p.as_posix()!=name or p.is_absolute() or any(x in ('','..','.') or x.endswith((' ','.')) for x in p.parts):return False
    if '__pycache__' in p.parts or p.suffix in ('.pyc','.pyo'):return False
    reserved={'con','prn','aux','nul',*(f'com{i}' for i in range(1,10)),*(f'lpt{i}' for i in range(1,10))}
    if any(re.search(r'[<>"|?*\x00-\x1f]',x) or x.split('.')[0].casefold() in reserved for x in p.parts):return False
    if name in ('幻化柜.exe','使用说明.txt'):return True
    prefixes=('程序/demo/','程序/tools/','程序/docs/','程序/defaults/','程序/artifacts/runtime/',
        '程序/artifacts/native/','程序/artifacts/cn-data/')
    return name.startswith(prefixes) or name in ('程序/artifacts/compiled-runtime.json','程序/artifacts/source-runtime.json','程序/artifacts/package-manifest.json')


def _url(value,*,local=False):
    u=urllib.parse.urlsplit(value)
    if u.username or u.password or u.fragment:raise ValueError('更新网址格式不正确')
    if not (u.scheme=='https' and u.hostname) and not(local and u.scheme=='http' and u.hostname in ('127.0.0.1','localhost')):
        raise ValueError('更新下载必须使用 HTTPS')
    return value


def _read_json(url,*,local=False):
    _url(url,local=local)
    with _open(urllib.request.Request(url,headers={'User-Agent':'HuanHuaGui-Updater/1','Accept':'application/json'}),local=local) as f:
        _url(f.geturl(),local=local);raw=f.read(LIMIT+1)
    if len(raw)>LIMIT:raise ValueError('更新清单过大')
    return json.loads(raw)


def _open(request,*,local=False):
    class Redirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self,req,fp,code,msg,headers,newurl):
            _url(newurl,local=local)
            return super().redirect_request(req,fp,code,msg,headers,newurl)
    return urllib.request.build_opener(Redirect()).open(request,timeout=15)


def check(current=None,*,api=API,local=False):
    current=current or current_version();version_key(current);releases=[]
    for page in range(1,101):
        rows=_read_json(api+('?'+urllib.parse.urlencode({'page':page,'per_page':100})),local=local)
        if not isinstance(rows,list):raise ValueError('发行列表格式不正确')
        for r in rows:
            if not isinstance(r,dict) or r.get('prerelease') or r.get('draft'):continue
            try:version_key(r.get('tag_name'))
            except ValueError:continue
            releases.append(r)
        if len(rows)<100:break
    else:raise ValueError('发行列表超出检查上限')
    newer=[r for r in releases if version_key(r['tag_name'])>version_key(current)]
    if not newer:return {'status':'current','current':current,'latest':current}
    release=max(newer,key=lambda r:version_key(r['tag_name']))
    result={'current':current,'latest':release['tag_name'],'notes':str(release.get('body')or'')[:20000],
        'page':RELEASES,'status':'no_patch'}
    assets=release.get('assets',[])
    if not isinstance(assets,list):raise ValueError('发行附件格式不正确')
    manifests=[a for a in assets if a.get('name')==MANIFEST]
    if len(manifests)!=1:return result
    data=_read_json(manifests[0]['browser_download_url'],local=local)
    if data.get('format')!='hgg-delta-v1' or data.get('version')!=result['latest']:raise ValueError('更新清单与 Git tag 不一致')
    patches=data.get('patches')
    if not isinstance(patches,list) or len(patches)>1000:raise ValueError('补丁清单格式不正确')
    matches=[p for p in patches if p.get('from')==current]
    if len(matches)!=1:return result
    patch=matches[0];_validate_patch(patch)
    attachments=[a for a in assets if a.get('name')==patch['asset']]
    if len(attachments)!=1:raise ValueError('缺少对应的更新补丁附件')
    result.update(status='available',patch=patch,url=_url(attachments[0]['browser_download_url'],local=local))
    return result


def _hash(value):return isinstance(value,str) and re.fullmatch('[0-9a-f]{64}',value)


def _validate_patch(p):
    version_key(p['from'])
    if not _hash(p.get('sha256')) or type(p.get('bytes')) is not int or not 0<p['bytes']<=1024**3:raise ValueError('补丁大小或摘要不正确')
    if not re.fullmatch(r'hgg-update-v[0-9.]+-to-v[0-9.]+\.zip',p.get('asset','')):raise ValueError('补丁附件名称不正确')
    files=p.get('files');removed=p.get('removed')
    if not isinstance(files,dict) or not files or len(files)>20000 or not isinstance(removed,dict):raise ValueError('补丁文件清单不正确')
    names=list(files)+list(removed)
    if len({n.casefold() for n in names})!=len(names) or any(not program_file(n) for n in names):raise ValueError('补丁包含非程序文件或重复路径')
    for name,r in files.items():
        if (not isinstance(r,dict) or not _hash(r.get('sha256')) or
            type(r.get('bytes')) is not int or not 0<=r['bytes']<=512*1024**2 or
            r.get('old_sha256') is not None and not _hash(r['old_sha256'])):raise ValueError('补丁文件摘要不正确')
    if sum(r['bytes'] for r in files.values())>2*1024**3:raise ValueError('补丁展开大小超出限制')
    if any(not _hash(v) for v in removed.values()):raise ValueError('移除清单摘要不正确')
    # A release must carry its own version and complete runtime identity.
    if '程序/docs/client-version.json' not in files or not {'程序/artifacts/compiled-runtime.json','程序/artifacts/source-runtime.json'} & files.keys():raise ValueError('补丁缺少版本与运行清单')


def preflight(root,patch):
    root=Path(root).resolve();_validate_patch(patch)
    for name,r in {**{n:{'old_sha256':h} for n,h in patch['removed'].items()},**patch['files']}.items():
        target=no_links(root/name,root);expected=r['old_sha256']
        if target.exists():
            actual=digest(target)
            if actual!=expected and actual!=r.get('sha256'):raise ValueError('程序文件已修改，未覆盖：'+name)
        elif expected is not None:raise ValueError('原程序文件缺失：'+name)


def prepare(info,*,layout=None,progress=None,cancel=None,local=False):
    p=layout or paths()
    if layout is None and not can_install():raise ValueError('开发版直接运行源码，请通过 Git 更新源码；不会覆盖开发目录')
    if info.get('status')!='available':raise ValueError('没有可用的差异补丁')
    patch=info['patch'];preflight(p.root,patch)
    if version_key(info['latest'])<=version_key(info['current']):raise ValueError('不允许降级或重复更新')
    folder=no_links(p.derived('updates')/uuid.uuid4().hex,p.root);folder.mkdir(parents=True)
    package=folder/'patch.zip';received=0
    needed=patch['bytes']+sum(r['bytes'] for r in patch['files'].values())*2+64*1024**2
    if shutil.disk_usage(folder).free<needed:raise ValueError('更新可用空间不足')
    def check_cancel():
        if cancel is not None and cancel.is_set():raise InterruptedError('更新已取消，原软件未修改')
    try:
        check_cancel();url=_url(info['url'],local=local)
        with _open(urllib.request.Request(url,headers={'User-Agent':'HuanHuaGui-Updater/1','Accept-Encoding':'identity'}),local=local) as f,package.open('xb') as out:
            _url(f.geturl(),local=local)
            while True:
                check_cancel();block=f.read(min(65536,patch['bytes']-received+1))
                if not block:break
                received+=len(block)
                if received>patch['bytes']:raise ValueError('补丁下载超出声明大小')
                out.write(block)
                if progress:progress(received,patch['bytes'])
            out.flush();os.fsync(out.fileno())
        if received!=patch['bytes'] or digest(package)!=patch['sha256']:raise ValueError('补丁下载校验失败，原软件未修改')
        stage=folder/'stage';stage.mkdir()
        with zipfile.ZipFile(package) as z:
            entries=z.infolist()
            if len(entries)!=len(patch['files']) or {e.filename for e in entries}!=set(patch['files']):raise ValueError('补丁实际内容与清单不一致')
            for e in entries:
                check_cancel();record=patch['files'][e.filename]
                if e.file_size!=record['bytes'] or e.is_dir() or e.flag_bits&1 or (e.external_attr>>16)&0o170000==0o120000:raise ValueError('不支持的补丁文件')
                target=no_links(stage/e.filename,folder);target.parent.mkdir(parents=True,exist_ok=True)
                with z.open(e) as src,target.open('xb') as dst:shutil.copyfileobj(src,dst,65536)
                if digest(target)!=record['sha256']:raise ValueError('补丁文件校验失败：'+e.filename)
        check_cancel();preflight(p.root,patch)
        # Native helper is copied out of the replacement tree before shutdown.
        source=p.program/'tools/UpdateHelper.exe';helper=folder/'UpdateHelper.exe'
        report=json.loads((p.program/'artifacts/package-manifest.json').read_bytes())
        if digest(source)!=report['files']['程序/tools/UpdateHelper.exe']:raise ValueError('更新助手校验失败')
        shutil.copyfile(source,helper)
        job={'root':str(p.root),'stage':str(stage),'files':patch['files'],'removed':patch['removed'],
            'current':info['current'],'version':info['latest'],'pid':os.getpid(),
            'launcher_pid':int(os.environ.get('HGG_LAUNCHER_PID','0')),'restart':True}
        _atomic_json(folder/'job.json',job)
        return folder
    except BaseException:
        # Keep bounded failed evidence; never delete or edit an installation.
        raise


def launch_helper(folder):
    folder=Path(folder)
    return subprocess.Popen([str(folder/'UpdateHelper.exe'),str(folder/'job.json')],cwd=folder,
        creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
