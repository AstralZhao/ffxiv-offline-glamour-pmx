"""Portable application settings; game resources are always read-only."""
import hashlib,json,os,uuid
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def game_root(value):
    if not str(value).strip():raise ValueError('请选择国服游戏目录')
    path=Path(str(value).strip().strip('"')).expanduser().resolve()
    if path.name.lower()=='ffxiv_dx11.exe':path=path.parent
    if (path/'sqpack').is_dir():path=path.parent
    if not(path/'game/sqpack/ffxiv').is_dir():raise ValueError('该目录中没有找到 game/sqpack/ffxiv，请选择国服游戏安装目录')
    for name in('ffxivgame.ver','ffxiv_dx11.exe'):
        if not(path/'game'/name).is_file():raise ValueError('游戏文件不完整，缺少 '+name)
    return path


def validate_game(value,root=ROOT,*,verify_executable=True):
    path=game_root(value);root=Path(root)
    profile=json.loads((root/'docs/cn-customization-profile.json').read_bytes())
    version=(path/'game/ffxivgame.ver').read_text(encoding='utf8').strip()
    if version!=profile['client_version']:
        raise ValueError(f"当前游戏版本 {version} 与软件支持的国服版本 {profile['client_version']} 不一致，请使用对应版本的软件包")
    if root.is_relative_to(path):raise ValueError('请将试衣软件放在游戏安装目录之外，避免写入游戏文件夹')
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
            if data.get('version')!=1 or not isinstance(data.get('game_directory'),str):raise ValueError('配置格式不正确')
            return validate_game(data['game_directory'],self.root,verify_executable=False),''
        except(OSError,ValueError,TypeError,AttributeError)as error:return None,str(error)

    def save(self,game):
        # Caller validates fully off the GUI thread immediately before saving.
        path=game_root(game)
        if self.root.is_relative_to(path):raise ValueError('软件不能放在游戏安装目录内')
        temporary=self.root/('.config-'+uuid.uuid4().hex+'.tmp')
        try:
            with temporary.open('x',encoding='utf8')as file:
                json.dump({'version':1,'game_directory':str(path)},file,ensure_ascii=False,indent=2)
                file.write('\n');file.flush();os.fsync(file.fileno())
            temporary.replace(self.path)
        finally:
            if temporary.exists():temporary.unlink()
        return path
