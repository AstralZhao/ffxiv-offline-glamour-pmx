"""Memory-resident network resource sessions. Disk stores connection identity only.

This controls original downloaded assets, not the existing prepared-character
library or user presets/templates. Active decoded scenes own their own data.
"""
from collections import OrderedDict
from concurrent.futures import Future,TimeoutError as FutureTimeout
import io,json,re,threading,weakref
from pathlib import Path
from network_resources import BLOCK,BASE,MARKER,_sha,_url,_request,_atomic,_cancel,safe_path,host_url

_sessions=weakref.WeakValueDictionary();_pending=OrderedDict();_gate=threading.RLock()
MEMORY_LIMIT=16*BLOCK
METADATA_LIMIT=128*BLOCK


def require_space(bytes_needed):
    """A conservative admission guard, not a promise about total process RSS."""
    import os
    if os.name!='nt':return
    import ctypes as c
    class Status(c.Structure):
        _fields_=[('length',c.c_ulong),('load',c.c_ulong)]+[(n,c.c_ulonglong)for n in('total_phys','available_phys','total_commit','available_commit','total_virtual','available_virtual','extended')]
    value=Status();value.length=c.sizeof(value)
    if c.windll.kernel32.GlobalMemoryStatusEx(c.byref(value)):
        if min(value.available_phys,value.available_commit)<64*BLOCK+max(0,bytes_needed):
            raise MemoryError('可用内存不足，已停止加载新资源；请关闭部分程序后重试')


def descriptor(root):
    p=safe_path(Path(root),MARKER)
    if not p.is_file():return None
    if p.stat().st_size>1024**2:raise ValueError('网络资源连接记录过大')
    data=json.loads(p.read_bytes())
    if data.get('cache_policy')!='memory':return None
    if data.get('version')!=2:raise ValueError('内存资源连接记录版本不正确')
    host=host_url(data['host']);root=Path(root).resolve()
    if (not re.fullmatch('[0-9a-f]{64}',data['manifest_sha256'])or root.name!=data['manifest_sha256']
        or root.parent.name!=_sha(host.encode())or root.parent.parent.name!='memory'):
        raise ValueError('内存资源连接身份不正确')
    return data


def release_pending(root):
    with _gate:_pending.pop(str(Path(root).resolve()),None)


def peek(root):
    with _gate:return _sessions.get(str(Path(root).resolve()))


def connect(host,root,*,callback=None,cancel=None):
    """Called in a worker. No original manifest/index/EXE/DAT bytes are written."""
    host=host_url(host);root=Path(root).resolve(strict=True)
    profile=json.loads((__import__('app_paths').program_root(root)/'docs/cn-customization-profile.json').read_bytes())
    source=MemorySource.load(host,profile,callback=callback,cancel=cancel)
    folder=safe_path(root,f'{BASE}/memory/{_sha(host.encode())}/{source.info["manifest_sha256"]}')
    source.root=folder
    info={'version':2,'cache_policy':'memory','host':host,'manifest_sha256':source.info['manifest_sha256'],
        'client_version':profile['client_version'],'executable_sha256':profile['executable_sha256']}
    _cancel(cancel);_atomic(safe_path(folder,MARKER),json.dumps(info,sort_keys=True).encode())
    key=str(folder.resolve())
    with _gate:
        _sessions[key]=source;_pending[key]=source
        while len(_pending)>2:_pending.popitem(last=False)
    return folder


def open_root(root):
    root=Path(root).resolve(strict=True);key=str(root)
    with _gate:
        source=_sessions.get(key)
        if source is not None:return source
        data=descriptor(root)
        if data is None:raise ValueError('不是内存网络资源来源')
        profile={k:data[k]for k in('client_version','executable_sha256')}
        source=MemorySource.load(data['host'],profile,expected=data['manifest_sha256']);source.root=root
        _sessions[key]=source
        return source


class MemorySource:
    cache_policy='memory'

    @classmethod
    def load(cls,host,profile,*,expected=None,callback=None,cancel=None):
        from resource_pack import manifest_data
        source=cls();source.host=host_url(host);source.lock=threading.RLock();source.blocks=OrderedDict();source.block_bytes=0
        source.peak_block_bytes=0;source.requests=0;source.metadata={};source.root=None
        source.inflight={};source.epoch=0
        raw,_=_request(_url(source.host,'resource-pack.json'),limit=BLOCK,cancel=cancel)
        if expected is not None and _sha(raw)!=expected:raise ValueError('网络清单已变化，请重新配置新的资源版本网址')
        data=manifest_data(raw,profile);source.files=data['files'];source.manifest=raw
        names=[n for n in source.files if not re.search(r'\.dat[0-7]$',n)]
        total=sum(source.files[n]['bytes']for n in names)
        if total>METADATA_LIMIT or any(source.files[n]['bytes']>(4096 if n.endswith('.ver')else 64*BLOCK)for n in names):raise ValueError('基础资源超过内存模式预算')
        source.info={'host':source.host,'manifest_sha256':_sha(raw),'etags':{}}
        # Only the version is needed to connect. Indexes, native windows and
        # archive bytes are fetched by the first actual consumer, never probed
        # or prefetched just because they are present in the manifest.
        version=source.read_file('game/ffxivgame.ver',cancel=cancel)
        if version.decode('utf8').strip()!=profile['client_version']:raise ValueError('网络版本文件与软件不一致')
        if callback:callback({'phase':'连接资源完成','done':len(version),'total':len(version)})
        return source

    def relative(self,path):
        name=Path(path).relative_to(self.root).as_posix()
        if name not in self.files:raise ValueError('资源文件不在网络清单中')
        return name

    def has_file(self,path):
        try:return self.relative(path)in self.files
        except ValueError:return False

    def source_stamp(self,path):
        name=self.relative(path);record=self.files[name]
        # Persistent character packets must notice in-place server changes,
        # even when the published manifest was not updated. Establish a tag
        # ONLY for an archive actually needed by this packet/resource.
        with self.lock:needed=re.search(r'\.dat[0-7]$',name)and name not in self.info['etags']
        if needed:self.read_range(name,0,1)
        with self.lock:return(record['bytes'],record['sha256'],self.info['etags'].get(name,'metadata'))

    def read_file(self,path,*,cancel=None):
        name=path if isinstance(path,str)and path in self.files else self.relative(path)
        if re.search(r'\.dat[0-7]$',name):raise ValueError('不能整档读取网络数据档案')
        if name.endswith('.exe')and self.files[name]['bytes']>2*BLOCK:
            raise ValueError('网络客户端文件只允许按所需原始片段读取')
        return self._get(('file',name),lambda:self.metadata.get(name),
            lambda:self._metadata(name,cancel),lambda raw:self.metadata.__setitem__(name,raw))

    def _metadata(self,name,cancel):
        record=self.files[name];require_space(record['bytes']*3)
        raw,_=_request(_url(self.host,name),limit=record['bytes'],cancel=cancel)
        if len(raw)!=record['bytes']or _sha(raw)!=record['sha256']:raise ValueError('网络基础资源校验失败：'+name)
        return raw

    def _get(self,key,cached,load,store):
        # Deduplicate identical reads, but never hold the cache lock across
        # network I/O or waiting for another worker's future.
        from network_cancellation import check
        check()
        with self.lock:
            old=cached()
            if old is not None:return old
            future=self.inflight.get(key);owner=future is None;epoch=self.epoch
            if owner:future=Future();self.inflight[key]=future
        if not owner:
            while True:
                check()
                try:return future.result(timeout=.1)
                except FutureTimeout:continue
        try:
            raw=load()
            check()
            with self.lock:
                if epoch==self.epoch:store(raw)
                future.set_result(raw);self.inflight.pop(key,None)
            return raw
        except BaseException as error:
            with self.lock:future.set_exception(error);self.inflight.pop(key,None)
            raise

    def open_file(self,path):return io.BytesIO(self.read_file(path))

    def index_paths(self,category):
        return sorted(self.root/n for n in self.files if n.startswith('game/sqpack/ffxiv/'+category)and n.endswith('.index2'))

    def open(self,path):
        from network_resources import RangeReader
        name=self.relative(path)
        if not re.search(r'\.dat[0-7]$',name):raise ValueError('不是网络数据档案')
        return RangeReader(self,name)

    def read_range(self,name,start,length):
        if name not in self.files or type(start)is not int or type(length)is not int or start<0 or length<0 or start+length>self.files[name]['bytes']or length>2*BLOCK:
            raise ValueError('网络原始片段超出范围')
        if not length:return b''
        return self._fetch(name,start,start+length-1,('range',name,start,length))

    def block(self,name,index):
        record=self.files[name];start=index*BLOCK;end=min(record['bytes'],start+BLOCK)-1
        if start<0 or start>end:raise ValueError('网络资源读取超出范围')
        return self._fetch(name,start,end,(name,index))

    def _fetch(self,name,start,end,key):
        record=self.files[name]
        def cached():
            if key in self.blocks:self.blocks.move_to_end(key);return self.blocks[key]
        def load():
            require_space((end-start+1)*3)
            with self.lock:expected=self.info['etags'].get(name)
            raw,tag=_request(_url(self.host,name),limit=end-start+1,start=start,end=end,total=record['bytes'],etag=expected)
            if not tag or tag.startswith('W/')or not re.fullmatch(r'"[^"\r\n]+"',tag):raise ValueError('网络数据文件需要稳定的强 ETag')
            with self.lock:
                previous=self.info['etags'].get(name)
                if previous is not None and previous!=tag:raise ValueError('服务器资源版本已变化，请使用新的版本目录重新配置')
                self.info['etags'][name]=tag;self.requests+=1
            return raw
        def store(raw):
            while self.blocks and self.block_bytes+len(raw)>MEMORY_LIMIT:
                _,old=self.blocks.popitem(last=False);self.block_bytes-=len(old)
            self.blocks[key]=raw;self.block_bytes+=len(raw);self.peak_block_bytes=max(self.peak_block_bytes,self.block_bytes)
        return self._get(key,cached,load,store)

    def release_idle(self):
        with self.lock:self.epoch+=1;self.blocks.clear();self.block_bytes=0;self.metadata.clear()

    def metrics(self):
        return {'metadata_bytes':sum(map(len,self.metadata.values())),'block_bytes':self.block_bytes,'block_limit':MEMORY_LIMIT,
            'peak_block_bytes':self.peak_block_bytes,'range_requests':self.requests}
