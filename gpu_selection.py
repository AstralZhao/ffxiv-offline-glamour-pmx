"""Prefer the OS high-performance hardware adapter, without a fixed ordinal."""
import ctypes as c
import uuid


def choose_adapter(adapters,performance_order=()):
    hardware=[row for row in adapters if not row['software']]
    if not hardware:return None
    ranks={luid:index for index,luid in enumerate(performance_order)}
    hardware.sort(key=lambda row:(ranks.get(row['luid'],len(ranks)),-row['dedicated_bytes'],row['index']))
    return hardware[0]['index']


def adapter_inventory():
    from shader_feasibility import D3DProbe
    class Description(c.Structure):
        _fields_=[('name',c.c_wchar*128)]+[(k,c.c_uint)for k in('vendor','device','subsystem','revision')]+[(k,c.c_size_t)for k in('dedicated','system','shared')]+[('luid',c.c_ulonglong),('flags',c.c_uint)]
    factory=c.c_void_p();high=c.c_void_p();adapters=[];order=[]
    create=c.WinDLL('dxgi.dll').CreateDXGIFactory1;create.restype=c.c_long;create.argtypes=[c.c_void_p,c.POINTER(c.c_void_p)]
    iid=c.create_string_buffer(uuid.UUID('770aae78-f26f-4dba-a829-253c83d1b387').bytes_le)
    if create(iid,c.byref(factory))<0:return adapters,order
    try:
        for index in range(16):
            adapter=c.c_void_p()
            result=D3DProbe.method(factory,12,c.c_long,c.c_uint,c.POINTER(c.c_void_p))(factory,index,c.byref(adapter))
            if result<0:break
            try:
                desc=Description()
                result=D3DProbe.method(adapter,10,c.c_long,c.c_void_p)(adapter,c.byref(desc))
                if result>=0:adapters.append({'index':index,'name':desc.name,'luid':int(desc.luid),'software':bool(desc.flags&2),'dedicated_bytes':int(desc.dedicated)})
            finally:D3DProbe.release(adapter)
        iid6=c.create_string_buffer(uuid.UUID('c1b6694f-ff09-44a9-b03c-77900a0a1d17').bytes_le)
        result=D3DProbe.method(factory,0,c.c_long,c.c_void_p,c.POINTER(c.c_void_p))(factory,iid6,c.byref(high))
        if result>=0:
            iid1=c.create_string_buffer(uuid.UUID('29038f61-3839-4626-91fd-086879011a05').bytes_le)
            for index in range(16):
                adapter=c.c_void_p()
                result=D3DProbe.method(high,29,c.c_long,c.c_uint,c.c_uint,c.c_void_p,c.POINTER(c.c_void_p))(high,index,2,iid1,c.byref(adapter))
                if result<0:break
                try:
                    desc=Description()
                    if D3DProbe.method(adapter,10,c.c_long,c.c_void_p)(adapter,c.byref(desc))>=0:order.append(int(desc.luid))
                finally:D3DProbe.release(adapter)
    finally:D3DProbe.release(high);D3DProbe.release(factory)
    return adapters,order


def preferred_adapter():
    adapters,order=adapter_inventory()
    return choose_adapter(adapters,order)
