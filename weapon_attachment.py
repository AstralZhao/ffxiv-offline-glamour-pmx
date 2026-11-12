"""Original CN weapon attach types and final sheathed ATCH transforms."""
from bisect import bisect_right
import hashlib,json,math,struct
from pathlib import Path
import numpy as np
from geometry_probe import local_matrix


class WeaponAttachments:
    def __init__(self,pack,race):
        self.pack=pack;profile=json.loads((Path(__file__).resolve().parents[1]/'docs/cn-weapon-attachment.json').read_bytes())
        from source_executable import executable_sha256
        from renderer_reference import renderer_reference
        from cn_offscreen_lighting import rva_bytes
        exe=renderer_reference()
        if executable_sha256(exe)!=profile['executable_sha256']:raise ValueError('武器挂接来源版本尚未核实')
        for record in profile['native_ranges'].values():
            if hashlib.sha256(rva_bytes(exe,int(record['rva'],16),record['bytes'])).hexdigest()!=record['sha256']:raise ValueError('原生武器挂接代码已变化')
        def read(path):
            raw=pack.read(path)
            if hashlib.sha256(raw).hexdigest()!=profile['resources'][path]:raise ValueError('原生武器挂接资源已变化：'+path)
            return raw
        raw=read('chara/xls/weapontype/attach.wtd');version,count=struct.unpack_from('<HH',raw)
        if version!=1 or 4+count*8>len(raw):raise ValueError('武器类型映射表格式不正确')
        rows=list(struct.iter_unpack('<II',raw[4:4+count*8]));self.ids=[r[0]for r in rows];self.types=[r[1]for r in rows]
        if self.ids!=sorted(set(self.ids)):raise ValueError('武器类型映射顺序不正确')
        self.path=f'chara/xls/attachoffset/c{race:04}.atch';raw=read(self.path);count,states=struct.unpack_from('<HH',raw)
        if states!=6 or count>256:raise ValueError('武器挂接状态格式尚未核实')
        types=struct.unpack_from('<'+str(count)+'I',raw,4);start=4+count*4+32
        if start+count*states*32>len(raw):raise ValueError('武器挂接数据截断')
        self.states={}
        for state in range(states):
            self.states[state]={}
            for i,kind in enumerate(types):
                at=start+(i*states+state)*32 # CN8b0948..095b: !weaponDrawn -> state1.
                name,scale,x,y,z,rx,ry,rz=struct.unpack_from('<I7f',raw,at)
                if not start+count*states*32<=name<len(raw):raise ValueError('武器挂点名称偏移不正确')
                bone=raw[name:raw.index(0,name)].decode('ascii')
                self.states[state][kind]={'bone':bone,'scale':scale,'offset':(x,y,z),'rotation':(rx,ry,rz),
                    'accessory':bool(raw[4+count*4+i//8]&(1<<(i%8))),'state':state}
        self.entries=self.states[1]

    def entry(self,model,slot_index,*,drawn=False,state=None):
        index=bisect_right(self.ids,model)-1
        state=(0 if drawn else 1)if state is None else state
        if state not in self.states:raise ValueError('武器挂接状态超出原始表范围')
        entries=self.states[state]
        if index<0 or self.types[index]not in entries:raise NotImplementedError('当前武器类型没有原生挂点')
        entry=dict(entries[self.types[index]]);mirror=entry['accessory']^(slot_index==1)
        if mirror and entry['bone'].endswith(('_l','_r')):entry['bone']=entry['bone'][:-1]+('r'if entry['bone'][-1]=='l'else'l')
        entry.update(mirror=mirror,type=self.types[index].to_bytes(4,'big').lstrip(b'\0').decode('ascii'),source=self.path)
        return entry


def attach_local(entry):
    scale=entry['scale'];x,y,z=entry['offset'];rx,ry,rz=entry['rotation']
    if entry.get('mirror'):z=-z;rx=-rx;ry=-ry
    if not all(math.isfinite(v)for v in(scale,x,y,z,rx,ry,rz))or scale<=0:raise NotImplementedError('当前武器收刀状态不可见')
    sx,cx=math.sin(rx/2),math.cos(rx/2);sy,cy=math.sin(ry/2),math.cos(ry/2);sz,cz=math.sin(rz/2),math.cos(rz/2)
    q=(sx*cy*cz-cx*sy*sz,cx*sy*cz+sx*cy*sz,cx*cy*sz-sx*sy*cz,cx*cy*cz+sx*sy*sz)
    return local_matrix({'translation':(x,y,z),'rotation':q,'scale':(scale,)*3})
