"""Gitee release discovery and verified whole-distribution updates.

Download a complete release, preserve personal data. Validation finishes before the GUI
shuts down. A separate native helper owns replacement/rollback after exit.
"""
from pathlib import Path,PurePosixPath
from contextlib import contextmanager
import hashlib,json,os,re,shutil,subprocess,urllib.parse,urllib.request,uuid,zipfile
from app_paths import paths,no_links,_atomic_json
from app_version import API,RELEASES,version_key,current_version,can_install,is_beta,update_notice

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


def check(current=None,*,api=API,local=False,channel="stable",return_to_stable=False):
    current=current or current_version();version_key(current);releases=[]
    if channel not in('stable','beta'):raise ValueError('更新通道不正确')
    if return_to_stable and(channel!='stable'or not is_beta(current)):raise ValueError('仅测试版可返回正式版')
    for page in range(1,101):
        rows=_read_json(api+('?'+urllib.parse.urlencode({'page':page,'per_page':100})),local=local)
        if not isinstance(rows,list):raise ValueError('发行列表格式不正确')
        for r in rows:
            if not isinstance(r,dict) or r.get('draft'):continue
            try:version_key(r.get('tag_name'))
            except ValueError:continue
            beta=is_beta(r['tag_name'])
            if channel=='stable'and beta or r.get('prerelease')and not beta:continue
            releases.append(r)
        if len(rows)<100:break
    else:raise ValueError('发行列表超出检查上限')
    newer=[r for r in releases if return_to_stable or version_key(r['tag_name'])>version_key(current)]
    if not newer:return {'status':'current','current':current,'latest':current}
    release=max(newer,key=lambda r:version_key(r['tag_name']))
    result={'current':current,'latest':release['tag_name'],'notes':str(release.get('body')or'')[:20000],
        'page':RELEASES,'status':'no_package','channel':channel,'return_to_stable':return_to_stable}
    assets=release.get('assets',[])
    if not isinstance(assets,list):raise ValueError('发行附件格式不正确')
    choices=[a for a in assets if isinstance(a,dict) and distribution_name(a.get('name'))]
    tagged=[a for a in choices if a['name'].startswith('幻化柜-'+result['latest']+'-') or a['name']=='幻化柜-'+result['latest']+'.zip']
    if tagged:choices=tagged
    if len(choices)!=1:
        result['reason']='发行附件中没有完整软件包' if not choices else '发行附件中有多个软件包，请只保留一个当前版本的 Windows ZIP'
        return result
    asset=choices[0];size=asset.get('size')
    if size is not None and (type(size)is not int or not 0<size<=1024**3):size=None
    result.update(status='available',asset=asset['name'],bytes=size,url=_url(asset['browser_download_url'],local=local))
    return result


def distribution_name(name):
    return isinstance(name,str) and bool(re.fullmatch(r'幻化柜-[^/\\:]+\.zip',name)) and '资源' not in name


def _hash(value):return isinstance(value,str) and re.fullmatch('[0-9a-f]{64}',value)


def _validate_patch(p):
    version_key(p['from'])
    if not _hash(p.get('sha256')) or type(p.get('bytes')) is not int or not 0<p['bytes']<=1024**3:raise ValueError('补丁大小或摘要不正确')
    if not re.fullmatch(r'hgg-update-v[0-9.]+(?:-beta\.[0-9]+)?-to-v[0-9.]+(?:-beta\.[0-9]+)?\.zip',p.get('asset','')):raise ValueError('补丁附件名称不正确')
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


def _release_index(z,version):
    entries=z.infolist()
    if len(entries)>20000 or sum(e.file_size for e in entries)>2*1024**3:
        raise ValueError('发行包展开大小超出限制')
    names=[e.filename for e in entries if not e.is_dir()]
    if len({n.casefold() for n in names})!=len(names):raise ValueError('发行包包含重复路径')
    manifests=[n for n in names if n.endswith('程序/artifacts/package-manifest.json')]
    if len(manifests)!=1:raise ValueError('完整发行包缺少唯一程序清单')
    manifest=manifests[0];prefix=manifest.removesuffix('程序/artifacts/package-manifest.json')
    if prefix and (not prefix.endswith('/') or len(PurePosixPath(prefix).parts)!=1 or not program_file('程序/demo/'+prefix[:-1]+'.bin')):
        raise ValueError('发行包根目录不正确')
    info=z.getinfo(manifest)
    if info.file_size>LIMIT:raise ValueError('发行文件清单过大')
    raw=z.read(info);report=json.loads(raw)
    if report.get('format') not in ('source-py-v1','compiled-pyd-v1') or report.get('version')!=version:
        raise ValueError('发行包版本或布局与 Git tag 不一致')
    files=report.get('files')
    if not isinstance(files,dict) or not files or any(not program_file(n) or not _hash(h) for n,h in files.items()):
        raise ValueError('发行清单含非程序文件或无效摘要')
    if len({n.casefold() for n in files})!=len(files):raise ValueError('发行清单包含重复路径')
    files=dict(files);files['程序/artifacts/package-manifest.json']=hashlib.sha256(raw).hexdigest()
    marker='程序/artifacts/'+('source-runtime.json' if report['format']=='source-py-v1' else 'compiled-runtime.json')
    required={'幻化柜.exe','程序/docs/client-version.json','程序/demo/launch.py' if report['format']=='source-py-v1' else '程序/demo/launch.cp311-win_amd64.pyd',marker,'程序/tools/UpdateHelper.exe','程序/artifacts/runtime/python/python.exe'}
    if not required<=files.keys():raise ValueError('发行包缺少必要启动文件')
    if set(names)!={prefix+n for n in files}:raise ValueError('发行包实际文件与清单不一致')
    indexed={}
    allowed_dirs={prefix} if prefix else set()
    for name in files:
        allowed_dirs.update(prefix+parent.as_posix()+'/' for parent in PurePosixPath(name).parents if parent.as_posix()!='.')
    for e in entries:
        if e.flag_bits&1 or (e.external_attr>>16)&0o170000==0o120000:raise ValueError('发行包不支持加密或链接')
        if e.is_dir():
            if e.filename not in allowed_dirs:
                raise ValueError('发行包目录路径不正确')
            continue
        name=e.filename[len(prefix):]
        if not program_file(name) or e.file_size>512*1024**2:raise ValueError('发行包文件不正确')
        indexed[name]=e
    if json.loads(z.read(indexed['程序/docs/client-version.json'])).get('version')!=version:
        raise ValueError('发行包内版本配置不一致')
    return files,indexed


def _local_files(p,current):
    manifest=p.program/'artifacts/package-manifest.json'
    no_links(manifest,p.root)
    if manifest.stat().st_size>LIMIT:raise ValueError('本地程序清单过大')
    report=json.loads(manifest.read_bytes());files=report.get('files')
    if not isinstance(files,dict):raise ValueError('本地程序清单不正确')
    owned={n:h for n,h in files.items() if program_file(n)}
    if not all(_hash(h) for h in owned.values()):raise ValueError('本地程序摘要不正确')
    # This JSON is explicitly user-managed, unlike executable program files.
    # Bind the transaction to its actual validated bytes, not the release hash.
    version_path=no_links(p.program/'docs/client-version.json',p.root)
    if version_path.stat().st_size>4096:raise ValueError('本地版本配置过大')
    version_raw=version_path.read_bytes();declared=json.loads(version_raw)
    if not isinstance(declared,dict):raise ValueError('本地版本配置不正确')
    version_key(declared.get('version'))
    if declared['version']!=current:raise ValueError('本地版本号已变化，请重新检查更新')
    owned['程序/docs/client-version.json']=hashlib.sha256(version_raw).hexdigest()
    owned['程序/artifacts/package-manifest.json']=digest(manifest)
    return owned


@contextmanager
def _preparation_workspace(layout):
    """Keep successful update jobs; discard this attempt on failure/cancel."""
    parent=layout.derived('updates')
    folder=no_links(parent/uuid.uuid4().hex,layout.root);folder.mkdir(parents=True)
    try:
        yield folder
    except BaseException:
        try:
            # Remove only our newly allocated direct child, never a linked or
            # user-selected directory. Cleanup cannot hide the original error.
            if no_links(folder,layout.root).resolve().parent==parent.resolve():shutil.rmtree(folder)
        except(OSError,ValueError):pass
        raise


def prepare(info,*,layout=None,progress=None,cancel=None,local=False,allow_stable_return=False):
    p=layout or paths()
    if layout is None and not can_install():raise ValueError(update_notice())
    if info.get('status')!='available' or not distribution_name(info.get('asset')):raise ValueError('没有可用的完整软件发行包')
    stable_return=allow_stable_return is True and info.get('return_to_stable')is True and is_beta(info['current'])and not is_beta(info['latest'])
    if info.get('return_to_stable')and not stable_return:raise ValueError('返回正式版需要明确确认')
    if version_key(info['latest'])<=version_key(info['current'])and not stable_return:raise ValueError('不允许降级或重复更新')
    old=_local_files(p,info['current'])
    with _preparation_workspace(p)as folder:
        return _prepare_download(info,p,old,folder,progress,cancel,local)


def _prepare_download(info,p,old,folder,progress,cancel,local):
    package=folder/'release.zip';received=0;total=info.get('bytes')
    if total is not None and (type(total)is not int or not 0<total<=1024**3):raise ValueError('发行包大小不正确')
    def check_cancel():
        if cancel is not None and cancel.is_set():raise InterruptedError('更新已取消，原软件未修改')
    def space(amount):
        if shutil.disk_usage(folder).free<amount+64*1024**2:raise ValueError('更新可用空间不足')
    check_cancel();space(total or 0);url=_url(info['url'],local=local)
    with _open(urllib.request.Request(url,headers={'User-Agent':'HuanHuaGui-Updater/1','Accept-Encoding':'identity'}),local=local) as f,package.open('xb') as out:
        _url(f.geturl(),local=local)
        length=f.headers.get('Content-Length')
        if length is not None:
            length=int(length)
            if not 0<length<=1024**3 or total is not None and total!=length:raise ValueError('发行包下载大小不一致')
            total=length;space(total)
        if progress:progress(0,total)
        while True:
            check_cancel();block=f.read(65536)
            if not block:break
            received+=len(block)
            if received>1024**3 or total is not None and received>total:raise ValueError('发行包下载超出声明大小')
            out.write(block)
            if progress:progress(received,total)
        out.flush();os.fsync(out.fileno())
    if not received or total is not None and received!=total:raise ValueError('发行包下载校验失败，原软件未修改')
    stage=folder/'stage';stage.mkdir()
    with zipfile.ZipFile(package) as z:
        files,indexed=_release_index(z,info['latest'])
        backup_bytes=sum(no_links(p.root/n,p.root).stat().st_size for n in old if (p.root/n).is_file())
        space(sum(e.file_size for e in indexed.values())+backup_bytes)
        changes={n:{'sha256':h,'old_sha256':old.get(n),'bytes':indexed[n].file_size} for n,h in files.items()}
        removed={n:h for n,h in old.items() if n not in files}
        # The same native transaction contract is shared with historical patch
        # tools. Payload now always comes from a whole, version-checked release.
        transaction={'from':info['current'],'asset':'hgg-update-'+info['current']+'-to-'+info['latest']+'.zip',
            'bytes':received,'sha256':digest(package),'files':changes,'removed':removed}
        preflight(p.root,transaction)
        for name,e in indexed.items():
            check_cancel();target=no_links(stage/name,folder);target.parent.mkdir(parents=True,exist_ok=True)
            with z.open(e) as src,target.open('xb') as dst:
                while True:
                    check_cancel();block=src.read(65536)
                    if not block:break
                    dst.write(block)
            if digest(target)!=files[name]:raise ValueError('发行文件校验失败：'+name)
    check_cancel();preflight(p.root,transaction)
    source=p.program/'tools/UpdateHelper.exe';helper=folder/'UpdateHelper.exe'
    if digest(source)!=old.get('程序/tools/UpdateHelper.exe'):raise ValueError('更新助手校验失败')
    shutil.copyfile(source,helper)
    job={'root':str(p.root),'stage':str(stage),'files':changes,'removed':removed,
        'current':info['current'],'version':info['latest'],'pid':os.getpid(),
        'launcher_pid':int(os.environ.get('HGG_LAUNCHER_PID','0')),'restart':True,
        'source':'full-release','archive_sha256':transaction['sha256']}
    _atomic_json(folder/'job.json',job)
    return folder


def launch_helper(folder):
    folder=Path(folder)
    return subprocess.Popen([str(folder/'UpdateHelper.exe'),str(folder/'job.json')],cwd=folder,
        creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
