"""Operation-wide resource progress plus bounded active HTTP counters.

Discovery remains indeterminate. Once declared, the total counts real resource
preparations plus final presentation; only successful operation completion is
100%. There is no invented whole-operation byte denominator.
"""
import threading
from contextlib import contextmanager
from functools import wraps
from urllib.parse import urlsplit

_local=threading.local()


class TransferProgress:
    def __init__(self):self.lock=threading.Lock();self.record=None;self.serial=0;self.active={};self.job=None
    def start_job(self):
        with self.lock:
            self.serial+=1;token=self.serial
            self.job={'token':token,'kind':'总进度','phase':'整理资源','unit':'items','done':0,'total':None,'received':0,'completed_requests':0}
            return token
    def plan(self,count):
        if type(count)is not int or count<0:raise ValueError('Invalid resource plan count')
        with self.lock:
            if self.job is not None:
                self.job['total']=(self.job['total']or 1)+count;self.job['phase']='加载资源'
    def item_ready(self):
        with self.lock:
            if self.job is not None and self.job['total']is not None:
                self.job['done']=min(self.job['total']-1,self.job['done']+1)
    def preparing(self):
        with self.lock:
            if self.job is not None and self.job['total']is not None:self.job['phase']='准备预览'
    def end_job(self,token,success):
        with self.lock:
            if self.job is None or self.job['token']!=token:return
            if success and self.job['total']is not None:self.job.update(done=self.job['total'],phase='完成')
            else:self.job=None
    def abort(self):
        with self.lock:self.job=None;self.record=None
    def begin(self,url):
        with self.lock:
            self.serial+=1;token=self.serial
            self.record={'token':token,'kind':'当前片段'if '.dat'in urlsplit(url).path.rsplit('/',1)[-1]else'当前文件','done':0,'total':None}
            self.active[token]={'record':self.record,'job':self.job['token']if self.job else None}
            return token
    def update(self,token,done,total):
        with self.lock:
            active=self.active.get(token)
            if active is not None:
                record=active['record']
                if self.job is not None and active['job']==self.job['token']:self.job['received']+=max(0,done-record['done'])
                record.update(done=done,total=total if isinstance(total,int)and total>0 else None)
    def finish(self,token,success=True):
        with self.lock:
            active=self.active.pop(token,None)
            if active and success and self.job is not None and active['job']==self.job['token']:self.job['completed_requests']+=1
            if self.record is not None and self.record['token']==token:self.record=None
    def snapshot(self):
        with self.lock:
            if self.job is not None and(self.job['received']or self.job['total']is not None or self.active):
                return {**self.job,'active_requests':sum(v['job']==self.job['token']for v in self.active.values())}
            return dict(self.record)if self.record is not None else None


def bind(progress):_local.progress=progress
def current():return getattr(_local,'progress',None)


@contextmanager
def measured(url):
    progress=current();token=progress.begin(url)if progress else None
    success=False
    try:
        yield(lambda done,total:progress.update(token,done,total))if progress else None
        success=True
    finally:
        if progress:progress.finish(token,success)


@contextmanager
def operation(progress):
    if progress is None or getattr(_local,'operation',None)is progress:
        yield;return
    previous=getattr(_local,'operation',None);_local.operation=progress;token=progress.start_job();success=False
    try:yield;success=True
    finally:progress.end_job(token,success);_local.operation=previous


def tracked(fn):
    @wraps(fn)
    def wrapper(self,*args,**kwargs):
        progress=getattr(self,'transfer_progress',None)
        with operation(progress):
            result=fn(self,*args,**kwargs)
            if progress is not None and(fn.__name__=='initialize'and getattr(self,'status',None)=='error'
                    or isinstance(result,dict)and result.get('model_loaded')is False):progress.abort()
            return result
    return wrapper


def presentation(record,fallback='正在准备预览…'):
    if record is None:return None,fallback
    total=record['total'];fraction=record['done']/total if total else None
    if record.get('unit')=='items':
        phase=record['phase'];text='正在'+phase+'…'
        if total:text=f"{phase} · 总进度 {fraction:.0%} · {min(record['done'],total-1)}/{total-1} 项资源"
        elif record.get('received'):text+=f" 已接收 {record['received']/1024**2:.1f} MiB"
        return fraction,text
    return fraction,'正在下载资源 · '+record['kind']+(f' {fraction:.0%}'if fraction is not None else'…')
