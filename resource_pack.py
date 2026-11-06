"""Portable, verified original resource archives for offline wardrobe use."""
import hashlib,json,re,shutil,time,uuid,zipfile
from pathlib import Path,PurePosixPath

FORMAT='ff14-wardrobe-resources-v1'
CATEGORIES=('00','04','05','06','08','0a')
MANIFEST='resource-pack.json'
ARCHIVE=re.compile(r'game/sqpack/ffxiv/((?:00|04|05|06|08|0a)[0-9a-f]{4})\.win32\.(index2|dat[0-7])\Z')
CHUNK=4*1024**2
MAX_TOTAL=100*1024**3


def fingerprint(path,*,cancel=None):
    h=hashlib.sha256()
    with Path(path).open('rb')as source:
        for chunk in iter(lambda:source.read(CHUNK),b''):check_cancel(cancel);h.update(chunk)
    return h.hexdigest()


def check_cancel(cancel):
    if cancel is not None and cancel.is_set():raise InterruptedError('操作已取消')


def publish(source,target,*,cancel=None):
    # Windows may briefly deny directory rename after the last file is closed.
    # Retry the same complete staging tree, without deleting an existing pack.
    for attempt,delay in enumerate((0,.02,.05,.1,.2,.4)):
        check_cancel(cancel)
        if delay:time.sleep(delay)
        if target.exists():raise FileExistsError('目标已存在，未覆盖：'+str(target))
        try:source.rename(target);return
        except PermissionError as error:
            if getattr(error,'winerror',None)not in(5,32,33)or attempt==5:raise


def progress(callback,phase,done,total):
    if callback:callback({'phase':phase,'done':done,'total':total})


def allowed(name):
    return name in('game/ffxivgame.ver','game/ffxiv_dx11.exe')or ARCHIVE.fullmatch(name)is not None


def manifest_data(raw,profile):
    data=json.loads(raw)
    supported=((FORMAT,'CN','wardrobe-base-archives'),('ff14-wardrobe-resources-v2','CN','wardrobe-selected-all-races'))
    if (data.get('format'),data.get('region'),data.get('scope'))not in supported:
        raise ValueError('不是受支持的幻化柜国服资源包')
    if data.get('format')=='ff14-wardrobe-resources-v2'and data.get('selection',{}).get('race_gender_clan_filters')is not False:
        raise ValueError('精简包缺少全部种族保留声明')
    if data.get('client_version')!=profile['client_version']:raise ValueError('资源包的国服版本与当前软件不匹配')
    if data.get('categories')!=list(CATEGORIES):raise ValueError('资源包缺少必需的资源分类')
    files=data.get('files')
    if not isinstance(files,dict)or not 8<=len(files)<=256:raise ValueError('资源包文件清单不正确')
    total=0
    for name,record in files.items():
        if not isinstance(name,str)or not allowed(name):raise ValueError('资源包包含不允许的文件路径')
        if not isinstance(record,dict)or type(record.get('bytes'))is not int or not 0<record['bytes']<=32*1024**3:raise ValueError('资源文件大小不正确')
        if not isinstance(record.get('sha256'),str)or not re.fullmatch('[0-9a-f]{64}',record['sha256']):raise ValueError('资源文件校验值不正确')
        total+=record['bytes']
    if total>MAX_TOTAL:raise ValueError('资源包超过支持的体积上限')
    exe=files.get('game/ffxiv_dx11.exe',{})
    if exe.get('sha256')!=profile['executable_sha256']or'game/ffxivgame.ver'not in files:raise ValueError('资源包客户端来源不匹配')
    for category in CATEGORIES:
        indexes=[name for name in files if ARCHIVE.fullmatch(name)and name.split('/')[-1].startswith(category)and name.endswith('.index2')]
        if not indexes:raise ValueError('资源包缺少资源索引：'+category)
        for name in indexes:
            if name.removesuffix('.index2')+'.dat0'not in files:raise ValueError('资源索引缺少对应数据档案')
    return data


def read_manifest(path,profile):
    path=Path(path)
    if path.stat().st_size>1024**2:raise ValueError('资源包清单过大')
    return manifest_data(path.read_bytes(),profile)


def validate_directory(path,profile,*,full=False,callback=None,cancel=None):
    path=Path(path).resolve();data=read_manifest(path/MANIFEST,profile)
    total=sum(record['bytes']for record in data['files'].values());done=0
    for name,record in data['files'].items():
        check_cancel(cancel);file=path.joinpath(*PurePosixPath(name).parts)
        if not file.resolve(strict=True).is_relative_to(path)or file.is_symlink():raise ValueError('资源包文件不能链接到目录外')
        if file.stat().st_size!=record['bytes']:raise ValueError('资源包文件大小已改变：'+name)
        if full and fingerprint(file,cancel=cancel)!=record['sha256']:raise ValueError('资源包文件校验失败：'+name)
        done+=record['bytes'];progress(callback,'检查资源',done,total)
    return path


def export_resource_pack(game,output,*,root=None,callback=None,cancel=None,compact=False):
    from settings import ROOT,validate_game
    root=Path(root or ROOT);game=validate_game(game,root);output=Path(output).resolve()
    if (game/'network-source.json').is_file():raise ValueError('网络资源不支持导出，请使用原始资源包或本机游戏目录')
    if output.is_relative_to(game):raise ValueError('资源包不能写入游戏安装目录')
    if output.exists():raise ValueError('目标文件已存在，请使用新的资源包文件名')
    if compact:
        from compact_resources import export_compact
        return export_compact(game,output,root,callback=callback,cancel=cancel)
    files=[game/'game/ffxivgame.ver',game/'game/ffxiv_dx11.exe']
    for path in sorted((game/'game/sqpack/ffxiv').iterdir()):
        if path.is_file()and allowed(path.relative_to(game).as_posix()):files.append(path)
    if any(not p.resolve(strict=True).is_relative_to(game)or p.is_symlink()for p in files):raise ValueError('源文件不能链接到游戏目录外')
    stats={p:(p.stat().st_size,p.stat().st_mtime_ns)for p in files};total=sum(v[0]for v in stats.values())
    if shutil.disk_usage(output.parent).free<total+64*1024**2:raise ValueError('导出位置可用空间不足')
    temporary=output.with_name(output.name+'.'+uuid.uuid4().hex+'.partial');done=0;records={}
    try:
        with zipfile.ZipFile(temporary,'x',compression=zipfile.ZIP_STORED,allowZip64=True)as archive:
            for path in files:
                check_cancel(cancel);name=path.relative_to(game).as_posix();h=hashlib.sha256();size=0
                with path.open('rb')as source,archive.open(name,'w',force_zip64=True)as target:
                    for chunk in iter(lambda:source.read(CHUNK),b''):
                        check_cancel(cancel);target.write(chunk);h.update(chunk);size+=len(chunk);done+=len(chunk);progress(callback,'导出资源',done,total)
                if (path.stat().st_size,path.stat().st_mtime_ns)!=stats[path]or size!=stats[path][0]:raise ValueError('导出时游戏资源发生变化，请在更新结束后重试')
                records[name]={'bytes':size,'sha256':h.hexdigest()}
            data={'format':FORMAT,'region':'CN','scope':'wardrobe-base-archives','categories':list(CATEGORIES),
                'client_version':(game/'game/ffxivgame.ver').read_text().strip(),'files':records}
            profile=json.loads((__import__('app_paths').program_root(root)/'docs/cn-customization-profile.json').read_bytes());manifest_data(json.dumps(data),profile)
            archive.writestr(MANIFEST,json.dumps(data,ensure_ascii=False,sort_keys=True,indent=2))
        check_cancel(cancel)
        if any((p.stat().st_size,p.stat().st_mtime_ns)!=expected for p,expected in stats.items()):raise ValueError('导出时游戏资源发生变化')
        # Atomic no-overwrite publish on Windows; existing archives are retained.
        publish(temporary,output,cancel=cancel)
        return output
    finally:
        if temporary.exists():temporary.unlink()


def import_resource_pack(archive_path,root,*,callback=None,cancel=None):
    root=Path(root).resolve();profile=json.loads((__import__('app_paths').program_root(root)/'docs/cn-customization-profile.json').read_bytes())
    source=Path(archive_path).resolve(strict=True);managed=root/'resources';managed.mkdir(exist_ok=True)
    if managed.is_symlink()or not managed.resolve().is_relative_to(root):raise ValueError('软件资源目录不能链接到外部位置')
    staging=managed/('.import-'+uuid.uuid4().hex);staging.mkdir()
    try:
        with zipfile.ZipFile(source)as archive:
            infos=archive.infolist();names=[i.filename for i in infos]
            if len(infos)>257 or len(set(names))!=len(names)or MANIFEST not in names:raise ValueError('资源包文件清单不正确或存在重名文件')
            info=archive.getinfo(MANIFEST)
            if info.file_size>1024**2:raise ValueError('资源包清单过大')
            raw=archive.read(info);data=manifest_data(raw,profile)
            if set(names)!=set(data['files'])|{MANIFEST}:raise ValueError('资源包实际文件与清单不一致')
            total=sum(r['bytes']for r in data['files'].values());done=0
            identity=hashlib.sha256(raw).hexdigest();destination=managed/identity
            if destination.is_symlink()or not destination.resolve().is_relative_to(managed):raise ValueError('资源包目标目录不正确')
            for entry in infos:
                if (entry.external_attr>>16)&0o170000==0o120000 or entry.flag_bits&1:raise ValueError('资源包不支持符号链接或加密文件')
                if entry.filename!=MANIFEST and entry.file_size!=data['files'][entry.filename]['bytes']:raise ValueError('资源包文件大小与清单不一致')
            if destination.exists():
                if (destination/MANIFEST).read_bytes()!=raw:raise ValueError('已有资源包身份不一致')
                return validate_directory(destination,profile,full=True,callback=callback,cancel=cancel)
            if shutil.disk_usage(managed).free<total+64*1024**2:raise ValueError('软件所在磁盘可用空间不足，无法导入资源包')
            for name,record in data['files'].items():
                check_cancel(cancel);target=staging.joinpath(*PurePosixPath(name).parts);target.parent.mkdir(parents=True,exist_ok=True)
                h=hashlib.sha256();size=0
                with archive.open(name)as incoming,target.open('xb')as outgoing:
                    for chunk in iter(lambda:incoming.read(CHUNK),b''):
                        check_cancel(cancel);size+=len(chunk)
                        if size>record['bytes']:raise ValueError('资源文件超过声明的大小')
                        outgoing.write(chunk);h.update(chunk);done+=len(chunk);progress(callback,'导入资源',done,total)
                if size!=record['bytes']or h.hexdigest()!=record['sha256']:raise ValueError('资源文件校验失败：'+name)
            (staging/MANIFEST).write_bytes(raw)
            publish(staging,destination,cancel=cancel)
            return destination
    finally:
        if staging.exists():
            if staging.is_symlink()or staging.resolve().parent!=managed.resolve():raise ValueError('拒绝清理软件资源目录外的位置')
            shutil.rmtree(staging)
