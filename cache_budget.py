"""Conservative cache tiers, not a limit on the process or the OS allocator."""
import ctypes as c

MiB=1024**2
GiB=1024**3


def system_memory():
    class Memory(c.Structure):
        _fields_=[('length',c.c_ulong),('load',c.c_ulong)]+[(n,c.c_ulonglong)for n in
            ('total','available','total_commit','available_commit','virtual','available_virtual','extended')]
    value=Memory();value.length=c.sizeof(value)
    if hasattr(c,'windll')and c.windll.kernel32.GlobalMemoryStatusEx(c.byref(value)):
        return value.total,min(value.available,value.available_commit)
    return 16*GiB,4*GiB


def cache_budget(total=None,available=None):
    if total is None or available is None:total,available=system_memory()
    tier=64 if total>=48*GiB else 32 if total>=24*GiB else 16
    asset,raw,entries={16:(128,64,160),32:(256,128,256),64:(512,192,384)}[tier]
    reserve={16:2,32:4,64:8}[tier]*GiB
    allowance=max(32*MiB,(available-reserve)//4)
    return {'tier':tier,'assets':min(asset*MiB,allowance),'raw':min(raw*MiB,allowance),
            'entries':min(entries,64)if available<reserve else entries,'programs':16*MiB,
            'reserve':reserve,'available':available}
