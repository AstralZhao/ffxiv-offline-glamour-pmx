"""Source-driven, frozen AVFX preview, separate from a native VFX simulation.

The wardrobe uses a fixed frame and the centre of authored random ranges. It
does not animate the character. Unsupported branches are reported, not replaced
with invented particles. Schema/curve conventions follow pinned VFXEditor.
"""
import math
import struct
import hashlib
from functools import lru_cache

import numpy as np

from vfx import records,integer


class UnsupportedEffect(NotImplementedError):pass


def fields(nodes):
    result={}
    for node in nodes:
        if node.tag in result:raise ValueError('Duplicate AVFX field: '+node.tag)
        result[node.tag]=node
    return result


@lru_cache(maxsize=4096)
def nested(node):return fields(records(node.data))if node else{}


def particle_fields(node):return fields(r for r in records(node.data)if r.tag!='UvSt')


def number(data,name,default=0,*,real=False):
    node=data.get(name)
    if node is None:return default
    if real:
        if len(node.data)!=4:raise ValueError('Invalid AVFX float: '+name)
        value=struct.unpack('<f',node.data)[0]
        if not math.isfinite(value):raise ValueError('Non-finite AVFX float: '+name)
        return value
    if len(node.data)==1:return node.data[0]
    return integer(node)


def curve(node,time,default=0,*,rgb=False):
    if node is None:return np.full(3,default)if rgb else default
    source=nested(node);raw=source.get('Keys')
    if raw is None:return np.full(3,default)if rgb else default
    if len(raw.data)%16:raise ValueError('Truncated AVFX key array')
    keys=[struct.unpack_from('<hhfff',raw.data,i)for i in range(0,len(raw.data),16)]
    if len(keys)!=number(source,'KeyC',len(keys))or not keys:raise ValueError('AVFX key count differs')
    if not all(np.isfinite(k[2:]).all()for k in keys):raise ValueError('Non-finite AVFX curve')
    if any(a[0]>=b[0]for a,b in zip(keys,keys[1:])):raise UnsupportedEffect('重叠曲线关键帧')
    value=lambda key:np.array(key[2:])if rgb else key[4]
    start,end=keys[0][0],keys[-1][0];offset=0
    if len(keys)>1 and(time<start or time>end):
        mode=number(source,'BvPr'if time<start else'BvPo')
        if mode in(1,2):
            turns=math.floor((time-start)/(end-start));time-=turns*(end-start)
            if mode==2:offset=turns*(value(keys[-1])-value(keys[0]))
        elif mode not in(0,-1):raise UnsupportedEffect('曲线边界模式')
    if time<=start:return value(keys[0])+offset
    if time>=end:return value(keys[-1])+offset
    for a,b in zip(keys,keys[1:]):
        if not a[0]<=time<b[0]:continue
        x=(time-a[0])/(b[0]-a[0])
        if rgb or a[1]==1:return value(a)*(1-x)+value(b)*x+offset
        if a[1]==2:return value(a)+offset
        if a[1]!=0:raise UnsupportedEffect('曲线插值类型')
        if value(a)==value(b):return value(a)+offset
        # VFXEditor uses normalized horizontal Bezier handles (Data.X/Y),
        # not slope tangents. Solve time on the monotonic cubic first.
        h1,h2=a[2]/2,1-a[3]/2
        if not 0<=h1<=h2<=1:raise UnsupportedEffect('非单调样条时间')
        lo,hi=0.,1.
        for _ in range(32):
            u=(lo+hi)/2;v=1-u
            at=3*v*v*u*h1+3*v*u*u*h2+u*u*u
            if at<x:lo=u
            else:hi=u
        u=(lo+hi)/2;mix=u*u*(3-2*u)
        return value(a)*(1-mix)+value(b)*mix+offset
    raise ValueError('Unresolved AVFX curve interval')


def axes(node,time,default=0,dimensions=3):
    source=nested(node);names='XYZ'[:dimensions]
    value=np.array([curve(source.get(axis),time,default)for axis in names],dtype=float)
    connect=number(source,'ACT')
    links={0:(),1:((1,0),(2,0)),2:((1,0),),3:((2,0),),4:((0,1),(2,1)),
        5:((0,1),),6:((2,1),),7:((0,2),(1,2)),8:((0,2),),9:((1,2),)}if dimensions==3 else{0:(),1:((1,0),),2:((0,1),)}
    if connect not in links:raise UnsupportedEffect('坐标轴连接类型')
    for dst,src in links[connect]:value[dst]=value[src]
    return value


def color(node,time):
    data=nested(node);value=np.r_[curve(data.get('RGB'),time,1,rgb=True),curve(data.get('A'),time,1)]
    value*=np.array([curve(data.get('Scl'+axis),time,1)for axis in'RGBA'])
    value[:3]*=curve(data.get('Bri'),time,1)
    return value


def rotation(value,order):
    orders=('XYZ','YZX','ZXY','XZY','YXZ','ZYX')
    if not 0<=order<len(orders):raise UnsupportedEffect('旋转顺序')
    x,y,z=value;cx,sx=math.cos(x),math.sin(x);cy,sy=math.cos(y),math.sin(y);cz,sz=math.cos(z),math.sin(z)
    matrices={'X':np.array(((1,0,0),(0,cx,-sx),(0,sx,cx))),
        'Y':np.array(((cy,0,sy),(0,1,0),(-sy,0,cy))),'Z':np.array(((cz,-sz,0),(sz,cz,0),(0,0,1)))}
    result=np.eye(3)
    for axis in orders[order]:result=matrices[axis]@result
    return result


def transform(data,time,*,emitter=False):
    if number(data,'CCOT')!=0:raise UnsupportedEffect('坐标计算顺序')
    result=np.eye(4);result[:3,:3]=rotation(axes(data.get('Rot'),time),number(data,'ROT'if emitter else'RoOT'))@np.diag(axes(data.get('Scl'),time,1))
    result[:3,3]=axes(data.get('Pos'),time)
    return result


def items(nodes,container,start,count):
    # These files append cumulative snapshots. Only the LAST container is
    # authoritative; parsing all containers duplicates earlier particles.
    containers=[n for n in nodes if n.tag==container]
    if not containers:
        if count:raise ValueError('Missing AVFX item container')
        return ()
    result=[];group=[]
    for node in records(containers[-1].data):
        if node.tag==start and group:result.append(fields(group));group=[]
        group.append(node)
    if group:result.append(fields(group))
    if len(result)!=count:raise ValueError('AVFX item count differs: '+container)
    return tuple(result)


def alive(data,time):
    lifetime=number(nested(data.get('Life')),'Val',-1.,real=True)
    return time>=0 and(lifetime<0 or time<lifetime)


def emitter_items(nodes,data):
    particles=items(nodes,'ItPr','bEnb',number(data,'PrCn'))
    count=number(data,'EmCn')
    if not count:return particles,()
    # ItEm's final cumulative container starts with the complete ItPr list.
    # VFXEditor AvfxEmitter.ReadContents/WriteContents removes this prefix.
    combined=items(nodes,'ItEm','bEnb',len(particles)+count)
    if combined[:len(particles)]!=particles:raise ValueError('AVFX emitter particle prefix differs')
    return particles,combined[len(particles):]


def stationary(data,time):
    # Nonzero velocity/gravity requires integration, rather than sampling the
    # position curve alone. Keep that path visibly pending in this preview.
    for name in('Gra','VRX','VRY','VRZ'):
        if curve(data.get(name),time)!=0:raise UnsupportedEffect('非零速度或重力')


# Only names explicitly documented by the pinned BinderProperties reader.
# Positional targets (above head/front of character/etc.) are not guessed.
BONES={1:'j_kao',28:'j_sebo_c',29:'n_root',30:'n_hara',31:'j_kosi',32:'j_te_r',33:'j_te_l',71:'n_root'}


def binding(node,bones):
    data=nested(node)
    if number(data,'BnVr')!=0 or number(data,'RoTp')!=0:raise UnsupportedEffect('非固定点绑定')
    source=nested(data.get('PrpS'))
    if number(source,'BPT')!=0 or number(source,'bRng'):raise UnsupportedEffect('目标或环形绑定')
    mode=number(source,'BPTP');name=None
    if mode==3:
        raw=source.get('Name');text=raw.data.split(b'\0',1)[0].decode('utf8')if raw else''
        name=text if text and text!='null'else BONES.get(number(source,'BPID',-1))
        if name not in bones:raise UnsupportedEffect('未接入绑定点 '+str(name or number(source,'BPID',-1)))
        result=np.array(bones[name],copy=True)
    elif mode==0:
        if 'n_root'not in bones:raise UnsupportedEffect('缺少角色原点')
        result=np.array(bones['n_root'],copy=True)
    else:raise UnsupportedEffect('非骨架绑定位置')
    if number(data,'bStG'):
        # Source requests global initial direction: the target supplies the
        # attachment position, not the chest bone's authored local axes.
        if 'n_root'not in bones:raise UnsupportedEffect('缺少全局方向根节点')
        result[:3,:3]=bones['n_root'][:3,:3]
    offset=axes(source.get('Pos'),0)
    result[:3,3]+=(result@np.r_[offset,0])[:3]
    return result


def quad(effect,node,time,parent,parent_color):
    data=particle_fields(node)
    if number(data,'PrVT')!=8:raise UnsupportedEffect('粒子类型 '+str(number(data,'PrVT')))
    if number(data,'bSCt'):raise UnsupportedEffect('简化动画')
    stationary(data,time)
    if number(data,'CulT')!=0:raise UnsupportedEffect('特效剔除模式')
    if any(number(data,n)for n in('bNea','bFar')):raise UnsupportedEffect('距离裁剪')
    for tag in('TC2','TC3','TC4','TN','TR','TD','TP'):
        if number(nested(data.get(tag)),'bEna'):raise UnsupportedEffect('多贴图、法线或扭曲')
    texture=nested(data.get('TC1'))
    if not number(texture,'bEna'):raise UnsupportedEffect('无颜色贴图')
    if any(number(texture,n)for n in('bUSC','bPFC','bUOS')):raise UnsupportedEffect('画面或肖像贴图')
    if curve(texture.get('TxN'),time)!=0:raise UnsupportedEffect('动画贴图索引')
    index=number(texture,'TxNo',-1)
    if index<0:
        choice=texture.get('TLst')
        if choice is None or len(choice.data)!=1:raise UnsupportedEffect('多贴图选择')
        index=choice.data[0]
    if not 0<=index<len(effect.textures):raise ValueError('AVFX texture index out of range')
    uv_nodes=[r for r in records(node.data)if r.tag=='UvSt']
    selected=number(texture,'UvSN')
    if not 0<=selected<len(uv_nodes):raise ValueError('AVFX UV set index out of range')
    uv_data=nested(uv_nodes[selected])
    if number(uv_data,'CUvT'):raise UnsupportedEffect('屏幕坐标UV')
    uv=np.array(((0,0),(1,0),(1,1),(0,1)),dtype=float)
    uv=uv*axes(uv_data.get('Scl'),time,1,2)+axes(uv_data.get('Scr'),time,0,2)
    angle=curve(uv_data.get('Rot'),time)
    if angle:
        c,s=math.cos(angle),math.sin(angle);uv=(uv-.5)@np.array(((c,s),(-s,c)))+.5
    frame=number(data,'RBDT')
    if frame not in(0,1,2,5,6):raise UnsupportedEffect('粒子朝向类型 '+str(frame))
    mode=number(data,'RMT')
    if mode not in(0,1,2,3,4,8):raise UnsupportedEffect('混合模式 '+str(mode))
    local=transform(data,time)
    base=np.array(((-.5,-.5,0),(.5,-.5,0),(.5,.5,0),(-.5,.5,0)),dtype=float)
    if frame==0:base=base[:,(2,0,1)]
    elif frame==1:base=base[:,(0,2,1)]
    return {'kind':'quad','frame':frame,'local':local,'parent':parent,'positions':base,'uv':uv,
        'color':color(data.get('Col'),time)*parent_color,'texture':effect.textures[index],
        'color_to_alpha':bool(number(texture,'bC2A')),'filter':number(texture,'TFT'),
        'address':(number(texture,'TBUT'),number(texture,'TBVT')),
        'blend':mode,'soft':bool(number(data,'DsSp')),'depth_test':bool(number(data,'DsDt',1)),'depth_write':bool(number(data,'DsDw')),
        'cull':number(data,'CulT'),'depth_offset':number(data,'DpOf',0.,real=True)}


def prepare(effect,bones,frame=30.):
    """Normal equipment scheduler, deterministic frozen source sample.

    Independent preview policy: scheduler0, no state-change triggers, no
    random displacement. The result always reports this policy and gaps;
    zero random offsets are a fixed preview choice, not a game RNG replay.
    """
    from effect_geometry import sample
    result=[];pending=[];visits=0
    if effect.missing:return (),('特效来源缺失',)
    groups={tag:[r for r in effect.source_records if r.tag==tag]for tag in('Schd','TmLn','Bind','Emit','Ptcl')}
    def get(tag,index):
        if not 0<=index<len(groups[tag]):raise ValueError('AVFX graph index out of range: '+tag)
        return groups[tag][index]
    def inherit(child,parent,bound):
        mode=number(child,'PICd')
        if number(child,'PrLk',-1)>=0 or number(child,'IPbV')or number(child,'IPbL')or number(child,'bOvr'):
            raise UnsupportedEffect('链接、速度或寿命继承')
        if mode in(2,3):result=parent.copy()
        elif mode in(0,1):
            result=bound.copy();result[:3,3]=parent[:3,3]
            if mode==1:
                relative=np.linalg.solve(bound[:3,:3],parent[:3,:3]);scales=np.linalg.norm(relative,axis=0)
                if np.any(scales<1e-8):raise ValueError('Singular AVFX inherited transform')
                basis=relative/scales if number(child,'ICbR')else np.eye(3)
                if number(child,'ICbS'):basis=basis@np.diag(scales)
                result[:3,:3]=bound[:3,:3]@basis
                if not number(child,'ICbP'):result[:3,3]=bound[:3,3]
        else:raise UnsupportedEffect('继承模式 '+str(mode))
        result[:3,:3]=result[:3,:3]@rotation(np.array([number(child,'BIA'+a,0.,real=True)for a in'XYZ']),0)
        return result
    def births(child,data,time,identity,max_age):
        # Bounded independent frozen scheduling policy. CrTm=1 is sampled once;
        # interval emissions use the authored CrI/CrC. Random choices are stable
        # across redraws, not a replay of the game's RNG or simulation state.
        once=number(child,'CrTm',1);count=number(child,'CrCn',1);probability=number(child,'CrPr',100)
        if once not in(0,1)or not 0<=count<=64 or not 0<=probability<=100:raise UnsupportedEffect('发射次数或概率范围')
        start=number(child,'StFr')+number(child,'GenD')+once
        interval=curve(data.get('CrI'),time,1)
        if not once and interval<=0:raise UnsupportedEffect('非正发射间隔')
        count=count if once else max(1,int(curve(data.get('CrC'),time,1)))
        if once:times=[start]
        else:
            if max_age<0:raise UnsupportedEffect('无限寿命的重复发射尚未接入')
            first=max(0,math.floor((time-max_age-start)/interval)+1)
            last=math.floor((time-start)/interval)
            if last-first>512:raise UnsupportedEffect('发射预算')
            times=[start+i*interval for i in range(first,last+1)]
        if len(times)*count>512:raise UnsupportedEffect('发射预算')
        for birth in times:
            for i in range(count):
                if time<birth:continue
                seed=(effect.sha256+repr((identity,birth,i))).encode()
                draw=int.from_bytes(hashlib.sha256(seed).digest()[:4],'little')/2**32
                if draw*100>=probability:continue
                yield time-birth,birth
    def emit(index,time,bound,inherited,inherited_color,stack=()):
        nonlocal visits
        visits+=1
        if index in stack or len(stack)>12 or visits>256:raise UnsupportedEffect('发射器循环或预算')
        nodes=records(get('Emit',index).data);data=fields(r for r in nodes if r.tag not in('ItPr','ItEm'))
        if number(data,'EVT')!=0:raise UnsupportedEffect('非点发射器 '+str(number(data,'EVT')))
        if number(data,'EfNo',-1)>=0:raise UnsupportedEffect('发射器效果器')
        if not alive(data,time):return
        stationary(data,time);parent=inherited@transform(data,time,emitter=True)
        parent_color=inherited_color*color(data.get('Col'),time)
        particles,emitters=emitter_items(nodes,data)
        for container,children,tag in(('ItPr',particles,'Ptcl'),('ItEm',emitters,'Emit')):
            for ordinal,child in enumerate(children):
                if not number(child,'bEnb',1):continue
                try:
                    target=number(child,'TgtB',-1)
                    target_node=get(tag,target)
                    target_data=particle_fields(target_node)if tag=='Ptcl'else fields(r for r in records(target_node.data)if r.tag not in('ItPr','ItEm'))
                    max_age=number(nested(target_data.get('Life')),'Val',-1.,real=True)
                    for age,birth in births(child,data,time,(stack,index,container,ordinal),max_age):
                        matrix=inherit(child,parent,bound)
                        tint=parent_color if number(child,'PICo')else np.ones(4)
                        if tag=='Emit':emit(target,age,bound,matrix,tint,stack+(index,));continue
                        particle=get(tag,target);particle_data=particle_fields(particle)
                        if not alive(particle_data,age):continue
                        value=sample(effect,particle,age,matrix,tint)
                        value.update(source_particle=target,source_emitter=index,source_age=age,source_birth=birth)
                        if len(result)>=512:raise UnsupportedEffect('粒子预算')
                        result.append(value)
                except UnsupportedEffect as error:pending.append(str(error))
    try:
        root=fields(r for r in effect.source_records if r.tag.startswith('Rv'))
        for name in('RvPx','RvPy','RvPz','RvRx','RvRy','RvRz'):
            if number(root,name,0.,real=True)!=0:raise UnsupportedEffect('全局特效位置或旋转修正')
        for name in('RvSx','RvSy','RvSz','RvR','RvG','RvB'):
            if number(root,name,1.,real=True)!=1:raise UnsupportedEffect('全局特效缩放或颜色修正')
        scheduler=records(get('Schd',0).data);state=fields([r for r in scheduler if r.tag not in('Item','Trgr')])
        scheduled=items(scheduler,'Item','bEna',number(state,'ItCn'))
        for entry in scheduled:
            if not number(entry,'bEna',1):continue
            timeline_time=frame-number(entry,'StTm')
            if timeline_time<0:continue
            timeline=records(get('TmLn',number(entry,'TlNo',-1)).data)
            top=fields([r for r in timeline if r.tag not in('Item','Clip')])
            if number(top,'CpCn')or number(top,'LpEd'):raise UnsupportedEffect('时间轴片段或循环')
            for item in items(timeline,'Item','bEna',number(top,'TICn')):
                if not number(item,'bEna',1):continue
                time=timeline_time-number(item,'StTm');end=number(item,'EdTm',-1)
                if time<0 or(end>=0 and timeline_time>=end):continue
                try:
                    if number(item,'EfNo',-1)>=0:raise UnsupportedEffect('后处理效果器')
                    bound=binding(get('Bind',number(item,'BdNo',-1)),bones)
                    emit(number(item,'EmNo',-1),time,bound,bound,np.ones(4))
                except UnsupportedEffect as error:pending.append(str(error))
    except UnsupportedEffect as error:pending.append(str(error))
    return tuple(result),tuple(sorted(set(pending)))
