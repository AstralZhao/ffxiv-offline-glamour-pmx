"""Version-pinned HTTP Range source. Only verified metadata and requested blocks live locally.

Block checksums detect local corruption; they are NOT full remote DAT SHA proofs.
Servers must publish immutable versions with strong ETags and support byte ranges.
"""
from collections import OrderedDict
from concurrent.futures import Future
import hashlib,io,json,os,re,stat,threading,uuid
from pathlib import Path
from urllib.parse import urlsplit,urlunsplit,quote
from urllib.request import Request,urlopen
from urllib.error import HTTPError,URLError

MARKER='network-source.json'
BASE='缓存/network-resources'
BLOCK=1024**2
_lock=threading.RLock();_memory=OrderedDict();_memory_size=0;_inflight={}
CONNECTIONS=8
_slots=threading.BoundedSemaphore(CONNECTIONS)


def host_url(value):
    value=str(value).strip()
    if not value:raise ValueError('请填写资源根网址，例如 https://域名/资源目录/')
    if '://'in value and not value.startswith(('https://','http://')):raise ValueError('资源网址只支持 HTTP(S)')
    if not value.startswith(('https://','http://')):value='https://'+value
    p=urlsplit(value)
    if (p.scheme not in('http','https')or not p.hostname or p.username is not None or p.password is not None
        or p.query or p.fragment or '\\'in value or any(ord(c)<33 for c in value)):
        raise ValueError('资源网址须为 HTTP(S) 根网址，可包含目录，不含账号、查询参数或片段')
    try:p.port
    except ValueError:raise ValueError('资源网址端口无效')from None
    if any(part in('.','..')for part in p.path.split('/')):raise ValueError('资源网址不能包含相对目录')
    return urlunsplit((p.scheme,p.netloc,p.path.rstrip('/')+'/','',''))


def _sha(raw):return hashlib.sha256(raw).hexdigest()
def _cancel(event):
    from network_cancellation import check
    check()
    if event is not None and event.is_set():raise InterruptedError('网络资源读取已取消')


def safe_path(root,relative):
    """Reject links in EVERY existing parent, including the managed root itself."""
    root=Path(root).absolute();p=root/relative
    if Path(relative).is_absolute()or '..'in Path(relative).parts:raise ValueError('网络缓存路径无效')
    for current in [*reversed(p.parents),p]:
        try:info=current.lstat()
        except FileNotFoundError:continue
        if stat.S_ISLNK(info.st_mode)or getattr(info,'st_file_attributes',0)&0x400:raise ValueError('网络缓存不能使用链接目录')
    if not p.resolve().is_relative_to(root.resolve()):raise ValueError('网络缓存超出软件目录')
    return p


def _atomic(path,raw):
    path.parent.mkdir(parents=True,exist_ok=True);temp=path.with_name('.'+path.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        with temp.open('xb')as f:f.write(raw);f.flush();os.fsync(f.fileno())
        os.replace(temp,path)
    finally:temp.unlink(missing_ok=True)


def _request(url,*,limit,headers=None,start=None,end=None,total=None,etag=None,cancel=None):
    from transfer_progress import measured
    # Waiting requests consume no HTTP response buffers. Cancellation remains
    # responsive while another worker occupies the connection budget.
    slots=_slots
    _cancel(cancel)
    while not slots.acquire(timeout=.1):_cancel(cancel)
    try:
        with measured(url)as report:
            return _request_impl(url,limit=limit,headers=headers,start=start,end=end,total=total,etag=etag,cancel=cancel,report=report)
    finally:slots.release()


def _request_impl(url,*,limit,headers=None,start=None,end=None,total=None,etag=None,cancel=None,report=None):
    _cancel(cancel);h={'Accept-Encoding':'identity','User-Agent':'HuahuaGui/1'}
    h.update(headers or{})
    if start is not None:h['Range']=f'bytes={start}-{end}'
    if etag:h['If-Match']=etag
    try:
        from network_cancellation import current,open_response
        token=current();request=Request(url,headers=h)
        context=open_response(request,timeout=15,token=token)if token is not None else urlopen(request,timeout=15)
        with context as response:
            final=urlsplit(response.url)
            if final.scheme not in('http','https')or urlsplit(url).scheme=='https'and final.scheme!='https':raise ValueError('资源服务器跳转到不受支持的网址')
            if response.headers.get('Content-Encoding','identity').lower()!='identity':raise ValueError('资源服务器不能压缩二进制范围响应')
            if start is not None:
                expected=f'bytes {start}-{end}/{total}'
                if response.status!=206 or response.headers.get('Content-Range')!=expected:
                    raise ValueError('资源服务器不支持正确的 Range 分段读取，请检查对象存储或代理设置')
                if etag and response.headers.get('ETag')!=etag:raise ValueError('服务器资源版本已变化，请使用新的版本目录重新配置')
            declared=response.headers.get('Content-Length')
            if declared is not None and(int(declared)>limit or start is not None and int(declared)!=end-start+1):raise ValueError('网络资源响应大小不正确')
            transfer_total=end-start+1 if start is not None else int(declared)if declared is not None else None
            if report:report(0,transfer_total)
            parts=[];count=0
            while True:
                _cancel(cancel);chunk=response.read(min(65536,limit-count+1))
                if not chunk:break
                count+=len(chunk)
                if count>limit:raise ValueError('网络资源超过大小限制')
                parts.append(chunk)
                if report:report(count,transfer_total)
            raw=b''.join(parts)
            _cancel(cancel)
            if start is not None and len(raw)!=end-start+1:raise ValueError('网络资源分段不完整，请重试')
            return raw,response.headers.get('ETag')
    except HTTPError as error:
        if error.code==412:raise ValueError('服务器资源版本已变化，请使用新的版本目录重新配置')from None
        raise ValueError(f'网络资源读取失败（HTTP {error.code}），请检查根网址和文件访问权限')from None
    except(URLError,TimeoutError,OSError)as error:
        _cancel(cancel)
        raise ValueError('网络资源连接失败，请检查网络后重试；已下载缓存会保留')from error


def _url(host,name):return host+quote(name,safe='/')
def _stamp(path):
    s=path.stat();return[s.st_size,s.st_mtime_ns,s.st_ctime_ns]


def prepare(host,root,*,callback=None,cancel=None,cache_policy='disk'):
    """Settings worker: refresh manifest, verify small files, probe each DAT's Range/ETag."""
    if cache_policy=='memory':
        raise ValueError('网络资源仅支持磁盘缓存')
    if cache_policy!='disk':raise ValueError('未知的网络缓存方式')
    from resource_pack import manifest_data
    root=Path(root).resolve(strict=True);host=host_url(host)
    profile=json.loads((__import__('app_paths').program_root(root)/'docs/cn-customization-profile.json').read_bytes())
    raw,_=_request(_url(host,'resource-pack.json'),limit=1024**2,cancel=cancel);data=manifest_data(raw,profile)
    key=_sha(host.encode());digest=_sha(raw);folder=safe_path(root,f'{BASE}/{key}/{digest}')
    files=data['files'];metadata=[n for n in files if not re.search(r'\.dat[0-7]$',n)]
    done=0;total=sum(files[n]['bytes']for n in metadata);etags={};stamps={}
    if total>512*BLOCK or any(files[n]['bytes']>(4096 if n.endswith('.ver')else 128*BLOCK)for n in metadata):raise ValueError('网络基础资源超过大小限制')
    for name,record in files.items():
        _cancel(cancel);p=safe_path(folder,name)
        if name in metadata:
            if not p.is_file()or p.stat().st_size!=record['bytes']or _sha(p.read_bytes())!=record['sha256']:
                content,_=_request(_url(host,name),limit=record['bytes'],cancel=cancel)
                if len(content)!=record['bytes']or _sha(content)!=record['sha256']:raise ValueError('网络资源文件校验失败：'+name)
                _cancel(cancel);_atomic(p,content)
            stamps[name]=_stamp(p);done+=record['bytes']
            if callback:callback({'phase':'下载并校验基础资源','done':done,'total':total})
        else:
            _,tag=_request(_url(host,name),limit=1,start=0,end=0,total=record['bytes'],cancel=cancel)
            if not tag or tag.startswith('W/')or not re.fullmatch(r'"[^"\r\n]+"',tag):raise ValueError('网络数据文件需要稳定的强 ETag，请检查对象存储响应')
            etags[name]=tag
            # Placeholder for locate/path identity only. Never opened as DAT bytes.
            p.parent.mkdir(parents=True,exist_ok=True)
            if not p.exists():p.touch()
            if p.stat().st_size!=0:raise ValueError('网络资源缓存占位文件异常')
    if (folder/'game/ffxivgame.ver').read_text(encoding='utf8').strip()!=profile['client_version']:raise ValueError('网络资源的客户端版本文件不匹配')
    _cancel(cancel);_atomic(safe_path(folder,'resource-pack.json'),raw)
    marker={'version':1,'host':host,'manifest_sha256':digest,'etags':etags,'metadata_stamps':stamps}
    _atomic(safe_path(folder,MARKER),json.dumps(marker,sort_keys=True).encode())
    NetworkSource(folder);return folder


class NetworkSource:
    cache_policy='disk'

    def __new__(cls,root):
        from memory_resources import descriptor
        if descriptor(root)is not None:raise ValueError('仅内存模式已移除，请重新配置网络资源。')
        return super().__new__(cls)

    def __init__(self,root):
        self.root=Path(root).resolve(strict=True)
        marker=safe_path(self.root,MARKER);manifest=safe_path(self.root,'resource-pack.json')
        if marker.stat().st_size>1024**2 or manifest.stat().st_size>1024**2:raise ValueError('网络资源缓存清单过大')
        raw=manifest.read_bytes()
        self.info=json.loads(marker.read_bytes());self.host=host_url(self.info['host']);self.files=json.loads(raw)['files']
        from resource_pack import manifest_data
        data=json.loads(raw)
        manifest_data(raw,{'client_version':data['client_version'],'executable_sha256':self.files['game/ffxiv_dx11.exe']['sha256']})
        digest=_sha(raw)
        if(self.info.get('version')!=1 or digest!=self.info['manifest_sha256']or self.root.name!=digest
            or self.root.parent.name!=_sha(self.host.encode())):raise ValueError('网络资源缓存身份不正确')
        refreshed=False
        for name,record in self.files.items():
            from resource_pack import allowed
            if not allowed(name):raise ValueError('网络资源缓存路径不正确')
            p=safe_path(self.root,name)
            if re.search(r'\.dat[0-7]$',name):
                tag=self.info['etags'].get(name)
                if not p.is_file()or p.stat().st_size!=0 or not isinstance(tag,str)or not re.fullmatch(r'"[^"\r\n]+"',tag):raise ValueError('网络资源缓存数据目录不完整')
            elif _stamp(p)!=self.info['metadata_stamps'].get(name):
                if p.stat().st_size!=record['bytes']or _sha(p.read_bytes())!=record['sha256']:raise ValueError('网络基础缓存已变化，请在设置中重新连接资源网址')
                self.info['metadata_stamps'][name]=_stamp(p);refreshed=True
        if refreshed:_atomic(marker,json.dumps(self.info,sort_keys=True).encode())

    def source_stamp(self,path):
        name=Path(path).relative_to(self.root).as_posix();r=self.files[name]
        if name in self.info['etags']:return(r['bytes'],r['sha256'],self.info['etags'][name])
        return tuple(_stamp(path))

    def open(self,path):
        name=Path(path).relative_to(self.root).as_posix()
        if name not in self.info['etags']:raise ValueError('请求的网络数据文件不在清单内')
        return RangeReader(self,name)

    def block(self,name,index):
        global _memory_size
        record=self.files[name];start=index*BLOCK;end=min(record['bytes'],start+BLOCK)-1
        if start<0 or start>end:raise ValueError('网络资源读取超出范围')
        key=(str(self.root),record['sha256'],self.info['etags'][name],index)
        with _lock:
            old=_memory.get(key)
            if old is not None:_memory.move_to_end(key);return old
            future=_inflight.get(key);owner=future is None
            if owner:future=Future();_inflight[key]=future
        if not owner:return future.result()
        try:
            p=safe_path(self.root,f"chunks/{record['sha256']}/{index:08x}.chunk")
            try:
                if p.stat().st_size!=36+end-start+1:raise ValueError('Corrupt block size')
                stored=p.read_bytes();raw=stored[36:]
                if stored[:4]!=b'HGN1'or stored[4:36]!=hashlib.sha256(raw).digest():raise ValueError('Corrupt block hash')
            except(OSError,ValueError):
                raw,_=_request(_url(self.host,name),limit=end-start+1,start=start,end=end,total=record['bytes'],etag=self.info['etags'][name])
                _atomic(p,b'HGN1'+hashlib.sha256(raw).digest()+raw)
            with _lock:
                _memory[key]=raw;_memory_size+=len(raw)
                while _memory_size>32*BLOCK:
                    _,v=_memory.popitem(last=False);_memory_size-=len(v)
                future.set_result(raw);_inflight.pop(key,None)
            return raw
        except BaseException as error:
            with _lock:future.set_exception(error);_inflight.pop(key,None)
            raise


class RangeReader(io.RawIOBase):
    def __init__(self,source,name):super().__init__();self.source=source;self.name=name;self.pos=0;self.size=source.files[name]['bytes']
    def readable(self):return True
    def seekable(self):return True
    def tell(self):return self.pos
    def seek(self,offset,whence=0):
        self._checkClosed()
        if whence not in(0,1,2):raise ValueError('无效的读取定位方式')
        value=offset+(self.pos if whence==1 else self.size if whence==2 else 0)
        if value<0:raise ValueError('无效的网络资源偏移')
        self.pos=value;return value
    def read(self,size=-1):
        self._checkClosed()
        if size<0:size=max(0,self.size-self.pos)
        if size>256*BLOCK:raise ValueError('网络资源单次读取过大')
        remaining=min(size,max(0,self.size-self.pos));parts=[]
        while remaining:
            block=self.source.block(self.name,self.pos//BLOCK);offset=self.pos%BLOCK;n=min(remaining,len(block)-offset)
            parts.append(block[offset:offset+n]);self.pos+=n;remaining-=n
        return b''.join(parts)


def validate_cached(path,profile):
    from resource_pack import manifest_data
    source=NetworkSource(path);manifest_data(source.manifest if source.cache_policy=='memory'else(source.root/'resource-pack.json').read_bytes(),profile)
    return source
