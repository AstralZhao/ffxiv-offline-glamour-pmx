"""Read-only storage accounting and narrowly scoped, revalidated cache cleanup."""
import hashlib,json,os,re,stat,zipfile
from pathlib import Path

CATEGORIES=(('resources','已导入资源'),('archives','资源包文件'),('network','网络资源缓存'),('updates','更新下载与旧版本备份'),('cache','角色与渲染缓存'),
    ('personal','预设、模版与个人数据'),('program','程序与运行环境'),('other','其他文件'))
CACHE_DIR='artifacts/demo/character-cache'


def human_size(value):
    for unit in('B','KiB','MiB','GiB','TiB'):
        if value<1024 or unit=='TiB':return f'{value:.2f} {unit}'if unit!='B'else f'{int(value)} B'
        value/=1024


def _reparse(info):return stat.S_ISLNK(info.st_mode)or bool(getattr(info,'st_file_attributes',0)&0x400)
def _stamp(info):return [info.st_size,info.st_mtime_ns,info.st_ctime_ns]
def _cancel(event):
    if event is not None and event.is_set():raise InterruptedError('操作已取消')


def _category(relative):
    if relative.startswith('程序/'):
        category=_category(relative.split('/',1)[1])
        return 'program' if category=='other' else category
    if relative.startswith('用户数据/'):return 'personal'
    if relative.startswith('缓存/network-resources/'):return 'network'
    if relative.startswith('缓存/updates/'):return 'updates'
    if relative.startswith('缓存/'):return 'cache'
    if relative.startswith('artifacts/network-resources/'):return 'network'
    if relative.startswith('resources/'):return 'resources'
    if '/'not in relative and relative.endswith('.ff14resources'):return 'archives'
    if relative.startswith((CACHE_DIR+'/', 'artifacts/prepared-presets/','artifacts/static-hair-cache/')):return 'cache'
    if relative.startswith('presets/')or relative in('config.json','artifacts/demo/outfits.json','artifacts/demo/native-session.json')or relative.startswith('artifacts/demo/equipment-library.sqlite'):return 'personal'
    if relative.startswith(('demo/','tools/','docs/','artifacts/runtime/','artifacts/native/'))or '/'not in relative and relative.endswith(('.exe','.cmd')):return 'program'
    return 'other'


def _base_identity(path):
    with zipfile.ZipFile(path)as archive:
        if archive.getinfo('packet.json').file_size>64*1024**2:raise ValueError('Metadata exceeds limit')
        raw=archive.read('packet.json');root=json.loads(raw)
    def plain(obj):
        if isinstance(obj,list):return[plain(v)for v in obj]
        if not isinstance(obj,dict):return obj
        if obj.get('type')=='dict':return{plain(k):plain(v)for k,v in obj['items']}
        if obj.get('type')=='tuple':return tuple(plain(v)for v in obj['items'])
        raise ValueError('Unsupported identity')
    try:
        value=plain(next(v for k,v in root['items']if k=='identity'));value.pop('preset_sha256')
    except(KeyError,TypeError,AttributeError,StopIteration):raise ValueError('Unknown packet identity')from None
    if not isinstance(value.get('files'),dict)or value.get('format')!=3 or not value.get('executable_sha256'):raise ValueError('Unknown packet identity')
    return value,hashlib.sha256(raw).hexdigest()


def _kind(relative):
    p=Path(relative)
    if _update_file(p) is not None:return 'finished_update'
    if p.parent.as_posix()=='缓存/character-cache' and re.fullmatch('[0-9a-f]{64}\\.cache',p.name):return 'old_character'
    if p.parts and p.parts[0]=='缓存' and len(p.parts)>1 and p.parts[1]=='network-resources':
        return _kind('artifacts/'+p.relative_to('缓存').as_posix())
    if p.parts and p.parts[0]=='程序':
        kind=_kind(p.relative_to('程序').as_posix())
        return None if kind=='finished_update'else kind
    if re.fullmatch(r'artifacts/network-resources/[0-9a-f]{64}/[0-9a-f]{64}/chunks/[0-9a-f]{64}/[0-9a-f]{8}\.chunk',p.as_posix()):return 'network_chunk'
    if p.parent.as_posix()==CACHE_DIR and re.fullmatch('[0-9a-f]{64}\\.cache',p.name):return 'old_character'
    if len(p.parts)==3 and p.parts[0]in('demo','tools')and p.parts[1]=='__pycache__'and p.suffix=='.pyc':return 'bytecode'
    return None


def _update_file(path):
    parts=path.parts
    if len(parts)<4 or parts[:2]!=('缓存','updates')or not re.fullmatch('[0-9a-f]{32}',parts[2]):return None
    if len(parts)==4 and parts[3]in('release.zip','UpdateHelper.exe'):return '/'.join(parts[:3]),parts[3],None
    if len(parts)>4 and parts[3]in('stage','backup'):
        from updates import program_file
        name='/'.join(parts[4:])
        if program_file(name):return '/'.join(parts[:3]),parts[3],name
    return None


def _finished_update(root,folder):
    """Only terminal native transactions; keep their small audit records."""
    try:
        pending=root/'缓存/updates/pending.json'
        if pending.exists()or pending.is_symlink():return None
        controls=[];raws=[];values=[]
        for name in('job.json','result.json','backup.json'):
            path=_safe_file(root,folder+'/'+name);before=_stamp(path.stat())
            if before[0]>8*1024**2:return None
            raw=path.read_bytes()
            if _stamp(path.stat())!=before:return None
            value=json.loads(raw)
            if not isinstance(value,dict):return None
            controls.append((path,before));raws.append(raw);values.append(value)
        job,result,backup=values;directory=root/folder
        if Path(job['root']).resolve()!=root or Path(job['stage']).resolve()!=directory/'stage':return None
        if result.get('status')not in('updated','rolled_back'):return None
        if result['status']=='updated'and(not job.get('version')or result.get('version')!=job['version']):return None
        files=job['files'];removed=job['removed']
        from updates import program_file
        if not isinstance(files,dict)or not isinstance(removed,dict)or not files:return None
        if any(not program_file(n)for n in set(files)|set(removed)|set(backup)):return None
        if set(backup)!=set(files)|set(removed):return None
        if any(not isinstance(v,dict)or 'old'not in v or 'new'not in v for v in backup.values()):return None
        return {'fingerprint':hashlib.sha256(b'\0'.join(raws)).hexdigest(),'controls':controls,
            'stage':set(files),'backup':{n for n,v in backup.items()if v['old']is not None}}
    except(OSError,ValueError,KeyError,TypeError):return None


def _update_unchanged(root,context):
    pending=root/'缓存/updates/pending.json'
    if pending.exists()or pending.is_symlink():return False
    return all(_safe_file(root,p.relative_to(root).as_posix())==p and _stamp(p.stat())==stamp for p,stamp in context['controls'])


def _update_allowed(context,area,name):
    return context is not None and(name is None or name in context[area])


def scan(root,identity=None,*,cancel=None,callback=None):
    root=Path(root).resolve(strict=True);totals={key:0 for key,_ in CATEGORIES};candidates=[];count=0;unreadable=0;unknown=0
    stack=[root];updates={}
    while stack:
        _cancel(cancel);folder=stack.pop()
        try:
            with os.scandir(folder)as iterator:entries=list(iterator)
        except OSError:unreadable+=1;continue
        for entry in entries:
            _cancel(cancel)
            try:
                info=entry.stat(follow_symlinks=False)
                if _reparse(info):continue
                if stat.S_ISDIR(info.st_mode):stack.append(Path(entry.path));continue
                if not stat.S_ISREG(info.st_mode):continue
                p=Path(entry.path);relative=p.relative_to(root).as_posix();totals[_category(relative)]+=info.st_size;count+=1
                kind=_kind(relative);meta=None
                if kind=='finished_update':
                    folder,area,name=_update_file(Path(relative))
                    if folder not in updates:updates[folder]=_finished_update(root,folder)
                    context=updates[folder]
                    if not _update_allowed(context,area,name):continue
                    meta=context['fingerprint']
                if kind=='old_character':
                    if identity is None:continue
                    try:
                        previous,meta=_base_identity(p)
                        if previous==identity:continue
                    except Exception:unknown+=1;continue
                if kind:candidates.append({'path':relative,'kind':kind,'stamp':_stamp(info),'metadata_sha256':meta})
            except OSError:unreadable+=1
            if callback and count%250==0:callback({'files':count})
    return {'root':str(root),'totals':totals,'total':sum(totals.values()),'files':count,'candidates':candidates,
        'reclaimable':sum(c['stamp'][0]for c in candidates),'unreadable':unreadable,'unknown_caches_preserved':unknown}


def _safe_file(root,relative):
    rel=Path(relative)
    if rel.is_absolute()or '..'in rel.parts:raise ValueError('清理目标超出软件目录')
    path=root/rel
    current=root
    for part in rel.parts:
        current/=part
        if _reparse(current.lstat()):raise ValueError('清理目标不能为链接')
    resolved=path.resolve(strict=True)
    if not resolved.is_relative_to(root)or not resolved.is_file():raise ValueError('清理目标不是软件内的缓存文件')
    return resolved


def clear_cache(root,candidates,identity=None,*,cancel=None,callback=None):
    root=Path(root).resolve(strict=True);removed=0;size=0;skipped=0;updates={}
    for index,entry in enumerate(candidates):
        _cancel(cancel)
        try:
            kind=_kind(entry['path'])
            if kind is None or kind!=entry['kind']:raise ValueError('不允许清除此类文件')
            path=_safe_file(root,entry['path'])
            if _stamp(path.stat())!=entry['stamp']:raise ValueError('缓存已变化，已保留')
            if kind=='old_character':
                if identity is None:raise ValueError('未取得当前缓存身份')
                old,meta=_base_identity(path)
                if old==identity or meta!=entry['metadata_sha256']:raise ValueError('缓存仍在使用或已变化')
            if kind=='finished_update':
                folder,area,name=_update_file(Path(entry['path']))
                if folder not in updates:updates[folder]=_finished_update(root,folder)
                context=updates[folder]
                if not _update_allowed(context,area,name)or context['fingerprint']!=entry['metadata_sha256']or not _update_unchanged(root,context):
                    raise ValueError('更新状态已变化或仍需恢复，已保留')
            # No directory removal: derived geometry, resource imports, live
            # sessions, user files and unknown formats are never candidates.
            if _stamp(path.stat())!=entry['stamp']:raise ValueError('缓存在检查期间发生变化，已保留')
            path.unlink();removed+=1;size+=entry['stamp'][0]
        except(OSError,ValueError,KeyError,zipfile.BadZipFile):skipped+=1
        if callback:callback({'done':index+1,'total':len(candidates)})
    return {'removed':removed,'bytes':size,'skipped':skipped}
