"""Small thread-owned HTTP transfer meter, polled by Qt; no Qt worker calls.

Percentages refer ONLY to the current response, never a whole outfit whose
dependency set is discovered progressively. Completion clears the meter.
"""
import threading
from contextlib import contextmanager
from urllib.parse import urlsplit

_local=threading.local()


class TransferProgress:
    def __init__(self):self.lock=threading.Lock();self.record=None;self.serial=0
    def begin(self,url):
        with self.lock:
            self.serial+=1;token=self.serial
            self.record={'token':token,'kind':'当前片段'if '.dat'in urlsplit(url).path.rsplit('/',1)[-1]else'当前文件','done':0,'total':None}
            return token
    def update(self,token,done,total):
        with self.lock:
            if self.record is not None and self.record['token']==token:
                self.record.update(done=done,total=total if isinstance(total,int)and total>0 else None)
    def finish(self,token):
        with self.lock:
            if self.record is not None and self.record['token']==token:self.record=None
    def snapshot(self):
        with self.lock:return dict(self.record)if self.record is not None else None


def bind(progress):_local.progress=progress
def current():return getattr(_local,'progress',None)


@contextmanager
def measured(url):
    progress=current();token=progress.begin(url)if progress else None
    try:yield(lambda done,total:progress.update(token,done,total))if progress else None
    finally:
        if progress:progress.finish(token)
