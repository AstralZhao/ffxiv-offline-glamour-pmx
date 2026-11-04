"""Application-owned native DAT library. Never writes to imported game files."""
import hashlib,json,os,re,struct,time,uuid
from copy import deepcopy
from pathlib import Path
from probe_appearance_presets import FIELDS
from cn_character import reviewed_identity,CLANS


def digest(raw):return hashlib.sha256(raw).hexdigest()


def decode(raw):
    if len(raw)!=212:raise ValueError('请导入212字节的原生DAT外观预设')
    magic,version,saved=struct.unpack_from('<III',raw)
    checksum=0
    for i,value in enumerate(raw[16:]):checksum^=value<<(i%24)
    if magic!=0x2013ff14 or version!=5 or saved!=(checksum&0xffffffff):
        raise ValueError('预设格式或校验和不正确')
    p=dict(zip(FIELDS,raw[16:42]))
    try:reviewed_identity(p)
    except NotImplementedError as error:raise ValueError(str(error))from error
    return {'parameters':p,'comment':raw[48:].split(b'\0',1)[0].decode('utf8',errors='replace'),
        'sha256':digest(raw)}


def change_bytes(raw,changes):
    decode(raw);data=bytearray(raw)
    if not isinstance(changes,dict)or set(changes)-set(FIELDS):raise ValueError('存在未知的捏脸参数')
    for field,value in changes.items():
        if type(value)is not int or not 0<=value<=255:raise ValueError('捏脸参数必须是有效整数')
        if field in('race','gender','body_type')and value!=data[16+FIELDS.index(field)]:
            raise ValueError('当前预设编辑保持原种族与性别身份')
        data[16+FIELDS.index(field)]=value
    checksum=0
    for i,value in enumerate(data[16:]):checksum^=value<<(i%24)
    struct.pack_into('<I',data,8,checksum&0xffffffff)
    result=bytes(data);decode(result);return result


class PresetLibrary:
    def __init__(self,directory,game):
        self.root=Path(directory).resolve();self.game=Path(game).resolve()
        if self.root.is_relative_to(self.game):raise ValueError('预设库不能位于游戏目录内')
        self.root.mkdir(parents=True,exist_ok=True)
        self.index=self.root/'library.json'
        self.metadata=json.loads(self.index.read_bytes())if self.index.is_file()else{'version':1,'files':{},'seeded':{}}
        if self.metadata.get('version')!=1:raise ValueError('未知的本地预设库版本')

    def path(self,name):
        if not isinstance(name,str)or Path(name).name!=name or not name.lower().endswith('.dat'):
            raise ValueError('预设文件名不正确')
        result=(self.root/name).resolve()
        if result.parent!=self.root or result.is_relative_to(self.game):raise ValueError('预设文件不能位于软件预设库之外')
        return result

    def atomic(self,path,raw):
        path=Path(path).resolve()
        if path.parent!=self.root or path.is_relative_to(self.game):raise ValueError('拒绝写入软件预设库以外的文件')
        temporary=self.root/('.'+uuid.uuid4().hex+'.tmp')
        try:
            with temporary.open('xb')as file:file.write(raw);file.flush();os.fsync(file.fileno())
            # Windows readers (including indexing/security software) can hold
            # a just-created file without FILE_SHARE_DELETE. Retry the same
            # atomic replacement briefly; never truncate/delete the old DAT.
            delays=(0,.02,.05,.1,.2,.4)
            for attempt,delay in enumerate(delays):
                if delay:time.sleep(delay)
                try:temporary.replace(path);break
                except PermissionError as error:
                    if getattr(error,'winerror',None)not in(5,32,33)or attempt==len(delays)-1:raise
        finally:
            if temporary.exists():temporary.unlink()

    def save_index(self):self.atomic(self.index,(json.dumps(self.metadata,ensure_ascii=False,indent=2)+'\n').encode('utf8'))

    def add(self,raw,name,*,identifier=None):
        decode(raw)
        stem=re.sub(r'[<>:"/\\|?*\x00-\x1f]','_',Path(name or'外观预设.dat').stem).strip(' .')[:60]or'外观预设'
        if stem.upper()in{'CON','PRN','AUX','NUL',*(f'COM{i}'for i in range(1,10)),*(f'LPT{i}'for i in range(1,10))}:stem='预设_'+stem
        number=1;name=stem+'.dat'
        while self.path(name).exists():number+=1;name=f'{stem} ({number}).dat'
        path=self.path(name);key=identifier or digest(str(path).encode())[:24]
        self.atomic(path,raw);self.metadata['files'][name]={'id':key};self.save_index()
        return path,key

    def seed(self,source,*,default=False):
        source=Path(source).resolve();origin=str(source)
        if origin in self.metadata.get('deleted_seeds',[]):return
        if origin in self.metadata['seeded']and self.path(self.metadata['seeded'][origin]).is_file():return
        if not source.is_file():return
        raw=source.read_bytes();decode(raw);key=digest(str(source).encode())[:24]
        # An existing app-owned test.dat may already contain user edits.
        if default and self.path('test.dat').is_file():
            path=self.path('test.dat');decode(path.read_bytes());self.metadata['files'].setdefault(path.name,{'id':key})
        else:path,_=self.add(raw,'test.dat'if default else source.name,identifier=key)
        self.metadata['seeded'][origin]=path.name;self.save_index()

    def scan(self):
        records=[]
        for candidate in sorted(self.root.glob('*.dat'),key=lambda p:(p.name!='test.dat',p.name.casefold())):
            path=self.path(candidate.name)
            try:record=decode(path.read_bytes())
            except ValueError:continue
            key=self.metadata['files'].get(path.name,{}).get('id')or digest(str(path).encode())[:24]
            p=record['parameters']
            records.append((key,path,{'id':key,'name':record['comment']or path.stem,'file_name':path.name,
                'clan':CLANS[p['tribe']],'face':p['face'],'hair':p['hair'],
                'parameters':p,'sha256':record['sha256'],'app_owned':True}))
        return records

    def replace(self,path,changes,expected):
        path=self.path(Path(path).name);old=path.read_bytes()
        if digest(old)!=expected:raise ValueError('这份预设已被其他窗口或程序修改，请重新打开编辑')
        new=change_bytes(old,changes);self.atomic(path,new);return old,new

    def rollback(self,path,old,edited):
        path=self.path(Path(path).name)
        if path.read_bytes()!=edited:raise ValueError('预设文件已被其他程序修改，未覆盖它的新内容')
        self.atomic(path,old)

    def remove(self,name,expected):
        path=self.path(name);raw=path.read_bytes()
        if digest(raw)!=expected:raise ValueError('预设已被其他程序修改，请刷新后再删除')
        previous=deepcopy(self.metadata);backup=self.root/('.'+uuid.uuid4().hex+'.deleting')
        path.replace(backup)
        try:
            if digest(backup.read_bytes())!=expected:raise ValueError('预设在删除前发生变化，请刷新后重试')
            self.metadata['files'].pop(name,None)
            deleted=self.metadata.setdefault('deleted_seeds',[])
            for origin,seeded in self.metadata['seeded'].items():
                if seeded==name and origin not in deleted:deleted.append(origin)
            self.save_index();backup.unlink()
        except Exception:
            self.metadata=previous
            if backup.exists():backup.replace(path)
            self.save_index();raise
