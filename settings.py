"""Portable game-directory or imported-resource settings; sources stay read-only."""
import hashlib,json,os,uuid
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def game_root(value):
    if not str(value).strip():raise ValueError('请选择国服游戏目录')
    path=Path(str(value).strip().strip('"')).expanduser().resolve()
    if (path/'network-source.json').is_file():
        from memory_resources import descriptor
        if descriptor(path)is not None:return path
    if path.name.lower()=='ffxiv_dx11.exe':path=path.parent
    if (path/'sqpack').is_dir():path=path.parent
    if not(path/'game/sqpack/ffxiv').is_dir():raise ValueError('该目录中没有找到 game/sqpack/ffxiv，请选择国服游戏安装目录')
    for name in('ffxivgame.ver','ffxiv_dx11.exe'):
        if not(path/'game'/name).is_file():raise ValueError('游戏文件不完整，缺少 '+name)
    return path


def validate_game(value,root=ROOT,*,verify_executable=True):
    path=game_root(value);root=Path(root)
    profile=json.loads((root/'docs/cn-customization-profile.json').read_bytes())
    if (path/'network-source.json').is_file():
        from memory_resources import descriptor
        if descriptor(path)is not None:
            if root.is_relative_to(path):raise ValueError('软件不能放在资源目录内')
            from network_resources import validate_cached
            validate_cached(path,profile);return path
    version=(path/'game/ffxivgame.ver').read_text(encoding='utf8').strip()
    if version!=profile['client_version']:
        raise ValueError(f"当前游戏版本 {version} 与软件支持的国服版本 {profile['client_version']} 不一致，请使用对应版本的软件包")
    if root.is_relative_to(path):raise ValueError('请将试衣软件放在游戏安装目录之外，避免写入游戏文件夹')
    if (path/'network-source.json').is_file():
        from network_resources import validate_cached
        validate_cached(path,profile)
    elif (path/'resource-pack.json').is_file():
        from resource_pack import validate_directory
        validate_directory(path,profile)
    if verify_executable:
        digest=hashlib.file_digest if hasattr(hashlib,'file_digest')else None
        with(path/'game/ffxiv_dx11.exe').open('rb')as source:
            if digest:actual=digest(source,'sha256').hexdigest()
            else:actual=hashlib.sha256(source.read()).hexdigest()
        if actual!=profile['executable_sha256']:raise ValueError('当前客户端文件与此软件包支持的国服版本不一致，请检查游戏版本或使用匹配的软件包')
    return path


class SettingsStore:
    def __init__(self,root=ROOT):self.root=Path(root).resolve();self.path=self.root/'config.json'

    def read(self):
        if not self.path.is_file():return None,''
        try:
            data=json.loads(self.path.read_bytes())
            if data.get('version')==3 and data.get('source')=='network':
                from network_resources import BASE,host_url,validate_cached
                relative=data.get('resource_directory')
                if not isinstance(relative,str):raise ValueError('网络资源配置格式不正确')
                path=(self.root/relative).resolve()
                if not path.is_relative_to(self.root/BASE):raise ValueError('网络缓存必须位于软件网络资源目录内')
                from memory_resources import descriptor
                memory=descriptor(path)
                if memory is not None:
                    profile=json.loads((self.root/'docs/cn-customization-profile.json').read_bytes())
                    if any(memory[k]!=profile[k]for k in('client_version','executable_sha256')):raise ValueError('网络连接与软件支持的国服版本不一致')
                    if memory['host']!=host_url(data.get('host','')):raise ValueError('网络资源网址与连接记录不一致')
                    return path,''
                source=validate_cached(path,json.loads((self.root/'docs/cn-customization-profile.json').read_bytes()))
                if source.host!=host_url(data.get('host','')):raise ValueError('网络资源网址与缓存不一致')
                return validate_game(path,self.root,verify_executable=False),''
            if data.get('version')==2 and data.get('source')=='resource_pack':
                relative=data.get('resource_directory')
                if not isinstance(relative,str):raise ValueError('资源包配置格式不正确')
                path=(self.root/relative).resolve()
                if not path.is_relative_to(self.root/'resources'):raise ValueError('资源包必须位于软件 resources 目录内')
                return validate_game(path,self.root,verify_executable=False),''
            if data.get('version')!=1 or not isinstance(data.get('game_directory'),str):raise ValueError('配置格式不正确')
            return validate_game(data['game_directory'],self.root,verify_executable=False),''
        except(OSError,ValueError,TypeError,AttributeError,KeyError)as error:return None,str(error)

    def studio(self):
        from studio_lighting import options
        try:return options(json.loads(self.path.read_bytes()).get('studio'))
        except(OSError,ValueError,TypeError,AttributeError):return options()

    def resource_inputs(self):
        """UI drafts only: never activate or validate an inactive source."""
        try:data=json.loads(self.path.read_bytes())
        except(OSError,ValueError):return {}
        if not isinstance(data,dict):return {}
        saved=data.get('resource_inputs',{});result={}
        if isinstance(saved,dict):
            for key in('game','pack','network'):
                value=saved.get(key)
                if isinstance(value,dict)and key!='network':
                    relative=value.get('software_relative')
                    if isinstance(relative,str)and len(relative)<=8192:
                        candidate=(self.root/relative).resolve()
                        if candidate.is_relative_to(self.root):result[key]=str(candidate)
                elif isinstance(value,str)and len(value)<=8192:result[key]=value
            if type(saved.get('disk_cache'))is bool:result['disk_cache']=saved['disk_cache']
        # Preserve the previous active source when upgrading an old config.
        if data.get('source')=='network':
            result.setdefault('network',data.get('host',''));result.setdefault('disk_cache',data.get('cache_policy','disk')=='disk')
        elif data.get('source')=='resource_pack':
            relative=data.get('resource_directory')
            if isinstance(relative,str):
                candidate=(self.root/relative).resolve()
                if candidate.is_relative_to(self.root/'resources'):result.setdefault('pack',str(candidate))
        elif isinstance(data.get('game_directory'),str):result.setdefault('game',data['game_directory'])
        return result

    def save(self,game,*,studio=None,resource_inputs=None):
        # Caller validates fully off the GUI thread immediately before saving.
        path=game_root(game)
        if self.root.is_relative_to(path):raise ValueError('软件不能放在游戏安装目录内')
        if (path/'network-source.json').is_file():
            from network_resources import BASE,NetworkSource
            if not path.is_relative_to(self.root/BASE):raise ValueError('网络资源缓存不在软件目录内')
            from memory_resources import descriptor
            memory=descriptor(path)
            host=memory['host']if memory else NetworkSource(path).host
            data={'version':3,'source':'network','host':host,'resource_directory':path.relative_to(self.root).as_posix(),'cache_policy':'memory'if memory else'disk'}
        elif (path/'resource-pack.json').is_file():
            if not path.is_relative_to(self.root/'resources'):raise ValueError('请先通过“导入资源包”将资源包导入软件目录')
            data={'version':2,'source':'resource_pack','resource_directory':path.relative_to(self.root).as_posix()}
        else:data={'version':1,'game_directory':str(path)}
        from studio_lighting import options
        data['studio']=self.studio()if studio is None else options(studio)
        remembered=self.resource_inputs()
        if resource_inputs is not None:
            if not isinstance(resource_inputs,dict):raise ValueError('资源输入记录格式不正确')
            for key,value in resource_inputs.items():
                if key in('game','pack','network')and isinstance(value,str)and len(value)<=8192:remembered[key]=value
                elif key=='disk_cache'and type(value)is bool:remembered[key]=value
        current='network'if data.get('source')=='network'else'pack'if data.get('source')=='resource_pack'else'game'
        if resource_inputs is None or current not in resource_inputs:
            remembered[current]=data['host']if current=='network'else str(path)
        if current=='network':remembered['disk_cache']=data['cache_policy']=='disk'
        # Internal directory/file hints move with the software; external paths
        # and unfinished text remain exactly as the user entered them.
        for key in('game','pack'):
            value=remembered.get(key)
            if isinstance(value,str)and value:
                candidate=Path(value)
                if candidate.is_absolute()and candidate.resolve().is_relative_to(self.root):
                    remembered[key]={'software_relative':candidate.resolve().relative_to(self.root).as_posix()}
        data['resource_inputs']=remembered
        temporary=self.root/('.config-'+uuid.uuid4().hex+'.tmp')
        try:
            with temporary.open('x',encoding='utf8')as file:
                json.dump(data,file,ensure_ascii=False,indent=2)
                file.write('\n');file.flush();os.fsync(file.fileno())
            temporary.replace(self.path)
        finally:
            if temporary.exists():temporary.unlink()
        return path
