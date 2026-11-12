"""Weapon-owned source AVFX binding and bounded cold-state preview sampling."""
from collections import OrderedDict
import hashlib,json,struct
from pathlib import Path
import numpy as np
from effect_binding import BindPoint
from effect_sample import UnsupportedEffect,fields,number,items
from vfx import records,read_effect


def model_points(raw):
    if len(raw)<68 or struct.unpack_from('<I',raw)[0]not in(0x01000005,0x01000006):
        raise ValueError('武器特效MDL版本尚未核实')
    start=68+struct.unpack_from('<H',raw,12)[0]*136
    if start+8>len(raw):raise ValueError('武器MDL字符串区截断')
    size=struct.unpack_from('<I',raw,start+4)[0];header=start+8+size
    if header+56>len(raw):raise ValueError('武器MDL模型头截断')
    count=struct.unpack_from('<H',raw,header+24)[0]
    if count>4096 or header+56+count*32>len(raw):raise ValueError('武器MDL挂点区越界')
    strings=raw[start+8:header];result={}
    for i in range(count):
        identifier,name,*v=struct.unpack_from('<II6f',raw,header+56+i*32)
        if identifier in result or not 0<=name<len(strings)or(name and strings[name-1]):
            raise ValueError('武器MDL挂点标识不正确')
        end=strings.find(b'\0',name)
        if end<0 or not np.isfinite(v).all():raise ValueError('武器MDL挂点数据不正确')
        bone=strings[name:end].decode('ascii')
        if not bone:raise ValueError('武器MDL挂点缺少骨骼名')
        angles=np.asarray(v[3:],dtype='<f4')*np.float32(3.1415927410125732)/np.float32(180)
        result[identifier]=BindPoint(bone,tuple(v[:3]),tuple(float(a)for a in angles))
    return tuple(result.items())


def posed_bones(skeleton,extra,deltas):
    from geometry_probe import local_matrix
    from prepare_fixed_stance import world_pose
    result={}
    for source in(skeleton,extra):
        if not source:continue
        bones=source['bones'];world=world_pose(np.asarray([local_matrix(b)for b in bones]),[b['parent']for b in bones])
        for bone,base in zip(bones,world):
            name=bone['name']
            if name in result:continue
            current=deltas.get(name,np.eye(4))@base
            current.setflags(write=False);result[name]=current
    return tuple(result.items())


class WeaponBindPoints:
    def __init__(self,points):self.points=dict(points)
    def vfx_scale(self):return 1. # Reviewed normal Weapon construction + own callback, not human race scale.
    def resolve(self,identifier,bones):
        if identifier not in self.points:raise UnsupportedEffect('武器模型未定义挂点 '+str(identifier))
        return self.points[identifier].world(bones)


def cold_schedule(effect,drawn,frame):
    """Final static state, after source KILL or post-REST root initialization.

    Transition playback remains separate from the settled state. Normal loop
    and RTRG events are expanded by weapon_timeline with their own identities.
    """
    schedulers=[r for r in effect.source_records if r.tag=='Schd']
    if len(schedulers)!=1:raise UnsupportedEffect('武器多调度器尚未接入')
    scheduler=records(schedulers[0].data);top=fields(r for r in scheduler if r.tag not in('Item','Trgr'))
    count=number(top,'ItCn');triggers=number(top,'TrCn')
    if triggers!=12:raise UnsupportedEffect('武器状态触发数量尚未核实')
    normal=items(scheduler,'Item','bEna',count)
    all_rows=items(scheduler,'Trgr','bEna',count+triggers)
    selected=all_rows[count+(5 if drawn else 4)]
    if not number(selected,'bEna',1)or number(selected,'TlNo',-1)<0:
        raise UnsupportedEffect('武器待机通知未指定时间线')
    timelines=[r for r in effect.source_records if r.tag=='TmLn']
    def timeline(index):
        if not 0<=index<len(timelines):raise ValueError('武器状态时间线索引越界')
        data=records(timelines[index].data)
        head=fields(r for r in data if r.tag not in('Item','Clip'))
        return data,head
    data,head=timeline(number(selected,'TlNo'));clips=[r for r in data if r.tag=='Clip']
    if len(clips)!=number(head,'CpCn')or number(head,'LpEd'):
        raise UnsupportedEffect('武器状态时间线布局尚未核实')
    hidden=set()
    for entry in items(data,'Item','bEna',number(head,'TICn')):
        if not number(entry,'bEna',1):continue
        if any(number(entry,n,-1)>=0 for n in('EmNo','EfNo')):
            raise UnsupportedEffect('武器状态混合粒子控制尚未接入')
        index=number(entry,'ClNo',-1)
        if not 0<=index<len(clips)or len(clips[index].data)!=164:
            raise ValueError('武器状态控制片段越界')
        kind,*values=struct.unpack_from('<I4i4f',clips[index].data)
        start=number(selected,'StTm')+number(entry,'StTm')
        if start!=0:raise UnsupportedEffect('武器延迟状态控制尚未接入')
        if drawn:
            if kind!=0x52455354 or values[4]!=0:
                raise UnsupportedEffect('武器拔出重置方式尚未接入')
        else:
            if kind!=0x4b494c4c or values[0]<=0 or any(values[4:]):
                raise UnsupportedEffect('武器收起淡出方式尚未接入')
            if frame<values[0]:raise UnsupportedEffect('武器收起过渡淡出尚未接入，请保持默认定格')
            for target in values[1:4]:
                if target>0:hidden.add(target-1)
                # Negative targets select already existing trigger instances.
                # At this settled cold state they have faded along with their
                # killed normal parent; no new normal dispatch occurs.
                elif target<0 and not 0<=-target-1<triggers:
                    raise ValueError('武器派生触发项淡出目标越界')
    entries=tuple((number(e,'TlNo'),float(number(e,'StTm')))for i,e in enumerate(normal)
                  if i not in hidden and number(e,'bEna',1)and number(e,'TlNo',-1)>=0)
    return entries,{'state':'drawn-post-reset'if drawn else'sheathed-source-hidden'if not entries else'sheathed',
                    'notification':6 if drawn else 5,'timeline_entries':entries,'transition_playback':False}


class WeaponEffects:
    def __init__(self,pack):
        from source_executable import executable_sha256
        from renderer_reference import renderer_reference
        from cn_offscreen_lighting import rva_bytes
        self.pack=pack;self.sources=OrderedDict()
        profile=json.loads((Path(__file__).resolve().parents[1]/'docs/cn-weapon-effects.json').read_bytes())
        exe=renderer_reference()
        if executable_sha256(exe)!=profile['executable_sha256']:
            raise UnsupportedEffect('武器特效来源版本尚未核实')
        for r in profile['native_ranges'].values():
            if hashlib.sha256(rva_bytes(exe,int(r['rva'],16),r['bytes'])).hexdigest()!=r['sha256']:
                raise UnsupportedEffect('原生武器特效规则来源已变化')
        for path,digest in profile['resources'].items():
            if hashlib.sha256(pack.read(path)).hexdigest()!=digest:raise UnsupportedEffect('武器待机通知来源已变化')
    def source(self,context):
        path=context.path
        if path not in self.sources:
            self.sources[path]=read_effect(path,self.pack.read(path))
            while len(self.sources)>32:self.sources.popitem(last=False)
        self.sources.move_to_end(path)
        return self.sources[path]
    def sample(self,context,effect,frame,history):
        from effect_sample import prepare
        from weapon_timeline import events
        entries,policy=cold_schedule(effect,context.drawn,frame)
        if not entries:return (),(),policy
        if not context.bones or not context.points:raise UnsupportedEffect('武器特效缺少自身骨架或模型挂点')
        samples,pending=prepare(effect,dict(context.bones),frame,bind_points=WeaponBindPoints(context.points),
                                simple_history=history,timeline_events=events(effect,entries,frame),
                                emitter_history=getattr(getattr(history,'__self__',None),'emitters',None))
        return samples,pending,policy
