"""Source ESLB selection of weapon partial skeletons, without pose synthesis."""
import struct


def missing_root(pack,bones,missing):
    """Original ModelDrawInit: absent name -> j_kao if present, else index 0.

    Only the authored generic weapon root seen in the current source corpus is
    admitted here. Partial-skeleton names still require the original ESLB.
    """
    if missing!={'skeleton'}or not bones:raise NotImplementedError('武器自身骨骼缺少原始模型绑定')
    from facewear import native_missing_bone
    name=native_missing_bone(pack) # Pins shared native branch, EXE and literal.
    return {n:name if any(b['name']==name for b in bones)else bones[0]['name']for n in missing}


def extra_skeletons(raw):
    if not 32<=len(raw)<=4*1024**2 or raw[4:8]!=b'ESLB':raise ValueError('武器额外骨架表格式不正确')
    def value(fmt,at):
        if at<0 or at+struct.calcsize(fmt)>len(raw):raise ValueError('武器额外骨架表截断')
        return struct.unpack_from(fmt,raw,at)[0]
    def field(at,index):
        vt=at-value('<i',at);size=value('<H',vt);pos=vt+4+index*2
        offset=value('<H',pos)if pos+2<=vt+size else 0
        return at+offset if offset else None
    def scalar(at,index,fmt):
        pos=field(at,index);return value(fmt,pos)if pos is not None else 0
    def vector(at,index):
        pos=field(at,index)
        if pos is None:return []
        start=pos+value('<I',pos);count=value('<I',start)
        if count>100000:raise ValueError('武器额外骨架表超出范围')
        return [p+value('<I',p)for p in range(start+4,start+4+count*4,4)]
    result=set()
    for row in vector(value('<I',0),2):
        if scalar(row,1,'<H')!=3:continue # Original1886290 model-type remap: weapon ->3.
        model=scalar(row,0,'<I')
        for sub in vector(row,2):
            pos=field(sub,1)
            if pos is None:continue
            string=pos+value('<I',pos);length=value('<I',string)
            if length==1 and raw[string+4:string+5]==b'p'and scalar(sub,2,'<B'):
                result.add((model,scalar(sub,0,'<I')))
    return frozenset(result)
