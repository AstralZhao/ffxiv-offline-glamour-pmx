"""Per-client load cancellation, including blocked HTTP socket reads.

Cancellation is worker-local, never a global cancellation of a resource host.
Only the owning GPU worker destroys D3D resources after its work unwinds.
"""
from contextlib import contextmanager
import http.client,io,socket,threading,time
from urllib.request import HTTPHandler,HTTPSHandler,build_opener


class LoadCancelled(Exception):pass


class LoadCancellation:
    def __init__(self):self.event=threading.Event();self.lock=threading.Lock();self.sockets={}
    def is_set(self):return self.event.is_set()
    def check(self):
        if self.is_set():raise LoadCancelled('已取消旧资源加载')
    def set(self):
        self.event.set()
        with self.lock:sockets=list(self.sockets.values())
        for value in sockets:self._interrupt(value)
    @staticmethod
    def _interrupt(value):
        try:value.shutdown(socket.SHUT_RDWR)
        except(OSError,ValueError):pass
    def watch(self,value):
        with self.lock:self.sockets[id(value)]=value;cancelled=self.is_set()
        if cancelled:self._interrupt(value);self.check()
    def unwatch(self,value):
        with self.lock:self.sockets.pop(id(value),None)


def current():
    from transfer_progress import current as progress
    return getattr(progress(),'cancel',None)


def check():
    token=current()
    if token is not None:token.check()


@contextmanager
def open_response(request,*,timeout,token):
    """Retain socket objects until response close, so cancellation can wake a
    read even after urllib releases its connection's own socket reference.
    DNS/TCP operations before a socket exists retain the original timeout.
    """
    watched=[]
    class Reader(io.RawIOBase):
        def __init__(self,sock):
            self.sock=sock;self.holder=sock.makefile('rb',buffering=0)
            # Poll recv directly: SocketIO permanently marks itself failed
            # after a timeout. Our lifetime-only holder never performs reads.
            sock.settimeout(.2)
        def readable(self):return True
        def readinto(self,buffer):
            deadline=time.monotonic()+timeout
            while True:
                token.check()
                try:return self.sock.recv_into(buffer)
                except socket.timeout:
                    token.check()
                    if time.monotonic()>=deadline:raise TimeoutError('资源响应读取超时')
        def close(self):
            if not self.closed:
                try:self.holder.close()
                finally:super().close()
    class Socket:
        def __init__(self,sock):self.sock=sock
        def __getattr__(self,name):return getattr(self.sock,name)
        def makefile(self,mode,*args,**kwargs):
            if mode!='rb':raise ValueError('资源连接只支持二进制读取')
            return io.BufferedReader(Reader(self.sock))
    def factory(cls):
        def connection(*args,**kwargs):
            token.check();value=cls(*args,**kwargs);create=value._create_connection;connect=value.connect
            def create_socket(*a,**kw):
                token.check();sock=create(*a,**kw);watched.append(sock)
                try:token.watch(sock)
                except BaseException:sock.close();raise
                return sock
            def connect_socket():
                connect()
                if value.sock is not None:
                    watched.append(value.sock);token.watch(value.sock);value.sock=Socket(value.sock)
                token.check()
            value._create_connection=create_socket;value.connect=connect_socket;return value
        return connection
    class HTTP(HTTPHandler):
        def http_open(self,req):return self.do_open(factory(http.client.HTTPConnection),req)
    class HTTPS(HTTPSHandler):
        def https_open(self,req):return self.do_open(factory(http.client.HTTPSConnection),req,context=self._context,check_hostname=self._check_hostname)
    try:
        token.check()
        with build_opener(HTTP(),HTTPS()).open(request,timeout=timeout)as response:
            token.check();yield response
    except BaseException:
        token.check();raise
    finally:
        for sock in watched:token.unwatch(sock)
