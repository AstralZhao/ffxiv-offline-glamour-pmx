"""Source-driven, frozen AVFX preview, separate from a native VFX simulation.

The wardrobe starts at a fixed frame, with optional effect-only playback. UV,
Simple size and initial particle TRS random bytes follow reviewed native math
with independent stable birth seeds, not a captured native RNG stream.
It does not animate the character. Unsupported branches are reported, not replaced
with invented particles. Schema names follow pinned VFXEditor; scalar/RGB
packing, lookup and interpolation follow reviewed CN executable routines.
"""
import math
import struct
import hashlib
from functools import lru_cache
from types import MappingProxyType

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


@lru_cache(maxsize=4096)
def particle_fields(node):return MappingProxyType(fields(r for r in records(node.data)if r.tag!='UvSt'))


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


def soft_particle_range(effect):
    # CN3ae6e2 stores root SPFR at +0x50. 3ba576..3ba621 selects
    # the positive runtime override, otherwise this exact source value;
    # 3835c7 writes it to DocumentParameters.ScreenSize.w. The independent
    # wardrobe has no runtime override, so never substitute a shared .1.
    data=fields(n for n in effect.source_records if n.tag=='SPFR')
    if 'SPFR'not in data:raise UnsupportedEffect('缺少软粒子淡出距离来源')
    value=number(data,'SPFR',real=True)
    if value<=0:raise UnsupportedEffect('非正软粒子淡出距离尚未审阅')
    return value


@lru_cache(maxsize=4096)
def curve_source(node):
    """Validate immutable source keys once, independent of playback time."""
    source=nested(node);raw=source.get('Keys')
    if raw is None:return source,None
    if len(raw.data)%16:raise ValueError('Truncated AVFX key array')
    keys=tuple(struct.unpack_from('<hhfff',raw.data,i)for i in range(0,len(raw.data),16))
    if len(keys)!=number(source,'KeyC',len(keys))or not keys:raise ValueError('AVFX key count differs')
    if not all(np.isfinite(k[2:]).all()for k in keys):raise ValueError('Non-finite AVFX curve')
    if any(a[0]>=b[0]for a,b in zip(keys,keys[1:])):raise UnsupportedEffect('重叠曲线关键帧')
    return source,keys


def curve(node,time,default=0,*,rgb=False):
    if node is not None:
        source,keys=curve_source(node)
        if keys:
            if len(keys)==1:time=keys[0][0]
            elif time<keys[0][0]and number(source,'BvPr')in(0,-1):time=keys[0][0]
            elif time>keys[-1][0]and number(source,'BvPo')in(0,-1):time=keys[-1][0]
    value=curve_value(node,time,default,rgb=rgb)
    # Never expose the cached RGB array to a caller's in-place color edits.
    return value.copy() if rgb else value


@lru_cache(maxsize=16384)
def curve_value(node,time,default=0,*,rgb=False):
    if node is None:return np.full(3,default)if rgb else default
    source,keys=curve_source(node)
    if keys is None:return np.full(3,default)if rgb else default
    from effect_curve import scalar_value,rgb_value
    pre,post=number(source,'BvPr'),number(source,'BvPo')
    if any(k[1]not in(0,1,2)for k in keys[1:]):raise UnsupportedEffect('曲线插值类型')
    if len(keys)>1 and(time<=keys[0][0]and pre not in(-1,0,1,2)or time>=keys[-1][0]and post not in(-1,0,1,2)):
        raise UnsupportedEffect('曲线边界模式')
    return rgb_value(keys,time,pre,post)if rgb else float(scalar_value(keys,time,pre,post))


def axes(node,time,default=0,dimensions=3):
    if constant_axes(node,dimensions):time=0
    return axes_value(node,time,default,dimensions).copy()


@lru_cache(maxsize=4096)
def constant_axes(node,dimensions):
    source=nested(node)
    return all(not keys or len(keys)==1 for _,keys in(curve_source(source.get(axis))for axis in'XYZ'[:dimensions]))


@lru_cache(maxsize=16384)
def axes_value(node,time,default=0,dimensions=3):
    source=nested(node);names='XYZ'[:dimensions]
    value=np.array([curve(source.get(axis),time,default)for axis in names],dtype=float)
    connect=number(source,'ACT')
    links={0:(),1:((1,0),(2,0)),2:((1,0),),3:((2,0),),4:((0,1),(2,1)),
        5:((0,1),),6:((2,1),),7:((0,2),(1,2)),8:((0,2),),9:((1,2),)}if dimensions==3 else{0:(),1:((1,0),),2:((0,1),)}
    if connect not in links:raise UnsupportedEffect('坐标轴连接类型')
    for dst,src in links[connect]:value[dst]=value[src]
    return value


def color(node,time):
    if constant_color(node):time=0
    return color_value(node,time).copy()


@lru_cache(maxsize=4096)
def constant_color(node):
    source=nested(node)
    return all(not keys or len(keys)==1 for _,keys in(curve_source(source.get(name))
        for name in('RGB','A','SclR','SclG','SclB','SclA','Bri')))


@lru_cache(maxsize=16384)
def color_value(node,time):
    data=nested(node);value=np.r_[curve(data.get('RGB'),time,1,rgb=True),curve(data.get('A'),time,1)]
    value*=np.array([curve(data.get('Scl'+axis),time,1)for axis in'RGBA'])
    value[:3]*=curve(data.get('Bri'),time,1)
    return value


def rotation(value,order):
    return rotation_value(tuple(value),order).copy()


@lru_cache(maxsize=4096)
def rotation_value(value,order):
    orders=('XYZ','YZX','ZXY','XZY','YXZ','ZYX')
    if not 0<=order<len(orders):raise UnsupportedEffect('旋转顺序')
    x,y,z=value;cx,sx=math.cos(x),math.sin(x);cy,sy=math.cos(y),math.sin(y);cz,sz=math.cos(z),math.sin(z)
    matrices={'X':np.array(((1,0,0),(0,cx,-sx),(0,sx,cx))),
        'Y':np.array(((cy,0,sy),(0,1,0),(-sy,0,cy))),'Z':np.array(((cz,-sz,0),(sz,cz,0),(0,0,1)))}
    result=np.eye(3)
    for axis in orders[order]:result=matrices[axis]@result
    return result


def transform(data,time,*,emitter=False,random_values=None):
    order=number(data,'CCOT')
    if not 0<=order<6:raise UnsupportedEffect('坐标计算顺序')
    coordinate=lambda name,default=0:random_values[name]if random_values is not None else axes(data.get(name),time,default)
    values=[np.asarray(coordinate(name,default))for name,default in(('Scl',1),('Rot',0),('Pos',0))]
    return transform_value(order,number(data,'ROT'if emitter else'RoOT'),
        tuple((v.dtype.str,v.tobytes())for v in values)).copy()


@lru_cache(maxsize=4096)
def transform_value(order,rotation_order,coordinates):
    scales,angles,position=(np.frombuffer(raw,dtype=dtype)for dtype,raw in coordinates)
    rotate=np.eye(4);rotate[:3,:3]=rotation(angles,rotation_order)
    scale=np.diag(np.r_[scales,1]);translate=np.eye(4);translate[:3,3]=position
    matrices={'R':rotate,'S':scale,'T':translate};result=np.eye(4)
    for component in('SRT','TSR','RTS','TRS','STR','RST')[order]:result=matrices[component]@result
    result.flags.writeable=False
    return result


def injection_angles(child,occurrence):
    # CN3cc9e8 clears per-link signed16 counters. 3d26a0 uses the OLD
    # counter times BIA XYZ, then increments it. BIA is not a fixed rotation.
    source=np.array([number(child,'BIA'+a,0.,real=True)for a in'XYZ'],dtype=np.float32)
    if np.any(source)and number(child,'CrPr',100)!=100:
        raise UnsupportedEffect('带随机淘汰的累计注入角度计数尚未接入')
    ordinal=(int(occurrence)+32768)%65536-32768
    return source*np.float32(ordinal)


def inherited_coordinates(chain,*,scale=False,z_only=False):
    """CN3b1a50/3b1b90/3b1ca0, supplied source-time ancestor channels.

    These are separate from ordinary matrix assembly. In particular a
    billboard never decomposes a bone/world matrix to invent Euler angles.
    """
    if not chain:return np.ones(3,dtype='<f4')if scale else np.zeros(3,dtype='<f4')
    f=np.float32;first=chain[0]
    value=np.asarray(first['scale'],dtype='<f4').copy()if scale else f(
        np.asarray(first['rotation'],dtype='<f4')+np.asarray(first['extra_rotation'],dtype='<f4'))
    for i,node in enumerate(chain[:-1]):
        if not node['flags']&(2 if scale else 4):break
        if node['mode']==3:
            saved=np.asarray(node['birth_scale'if scale else'birth_rotation'],dtype='<f4')
            if scale:value=f(value*saved)
            elif z_only:value[2]=f(value[2]+saved[2])
            else:value=f(value+saved)
            break
        parent=chain[i+1]
        if scale:value=f(value*np.asarray(parent['scale'],dtype='<f4'))
        elif z_only:value[2]=f(f(value[2]+f(parent['rotation'][2]))+f(parent['extra_rotation'][2]))
        else:
            value=f(value+np.asarray(parent['rotation'],dtype='<f4'))
            value=f(value+np.asarray(parent['extra_rotation'],dtype='<f4'))
    return value


def coordinate_node(data,time,child,parents,birth_parents,extra,coordinates=None):
    coordinate=lambda name,default=0:axes(data.get(name),time,default)if coordinates is None else coordinates[name]
    mode=number(child,'PICd')
    # CN3a07f8..3a089c normalizes inheritance flags after parsing. AllAlways
    # and AllCreate force scale/rotation on; None clears them. Only Custom
    # consumes the individual source checkboxes.
    flags=0 if mode==0 else 6 if mode in(2,3)else 2*bool(number(child,'ICbS'))+4*bool(number(child,'ICbR'))
    return {'scale':coordinate('Scl',1),'rotation':coordinate('Rot'),'extra_rotation':extra,
        'flags':flags,'mode':mode,
        'birth_scale':inherited_coordinates(birth_parents,scale=True),
        'birth_rotation':inherited_coordinates(birth_parents)}


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


def binding(node,bones,bind_points=None):
    data=nested(node)
    if number(data,'BnVr')!=0 or number(data,'RoTp')!=0:raise UnsupportedEffect('非固定点绑定')
    source=nested(data.get('PrpS'))
    if number(source,'BPT')!=0 or number(source,'bRng'):raise UnsupportedEffect('目标或环形绑定')
    mode=number(source,'BPTP')
    if mode==3:
        raw=source.get('Name');text=raw.data.split(b'\0',1)[0].decode('utf8')if raw else''
        if text and text!='null':
            if text not in bones:raise UnsupportedEffect('未接入绑定骨骼 '+text)
            result=np.array(bones[text],copy=True)
        else:
            if bind_points is None:raise UnsupportedEffect('缺少角色 EID 附着点来源')
            result=bind_points.resolve(number(source,'BPID',-1),bones)
    elif mode==0:
        if 'n_root'not in bones:raise UnsupportedEffect('缺少角色原点')
        result=np.array(bones['n_root'],copy=True)
    else:raise UnsupportedEffect('非骨架绑定位置')
    # bStG belongs to the native linear/spline update, not PointBinder.
    # CN3c00d0 separates attachment scale; bFTO controls its orientation.
    if not number(data,'bFTO'):
        result[:3,:3]=np.diag(np.linalg.norm(result[:3,:3],axis=0))
    offset=axes(source.get('Pos'),0)
    result[:3,3]+=(result@np.r_[offset,0])[:3]
    # The native callback separates the attachment orientation from scale.
    # bBET keeps the VFX scalar separate, interpolating it before multiplying
    # bone scale; the other branch interpolates the combined scale vector.
    from effect_binding import attachment_scale
    scales=np.linalg.norm(result[:3,:3],axis=0)
    if np.any(scales<1e-8):raise UnsupportedEffect('退化附着点尺度')
    source_scales=scales if number(data,'bTSc')else np.ones(3)
    vfx_enabled=bool(number(data,'bVSc'));separate=bool(number(data,'bBET'))
    if vfx_enabled:
        if bind_points is None or not hasattr(bind_points,'vfx_scale'):raise UnsupportedEffect('缺少角色特效尺度来源')
        vfx=bind_points.vfx_scale()
    else:vfx=1.
    bias=number(data,'bVSb',1.,real=True)
    adjusted=(source_scales if not vfx_enabled and(separate or bias==1)else
        attachment_scale(source_scales,vfx,bias,separate))
    result[:3,:3]=result[:3,:3]/scales*adjusted
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
        'blend':mode,'soft':bool(number(data,'DsSp')),'soft_range':soft_particle_range(effect)if number(data,'DsSp')else None,
        'depth_test':bool(number(data,'DsDt',1)),'depth_write':bool(number(data,'DsDw')),
        'cull':number(data,'CulT'),'depth_offset':number(data,'DpOf',0.,real=True)}


def prepare(effect,bones,frame=30.,*,bind_points=None):
    """Normal equipment scheduler, deterministic frozen source sample.

    Independent preview policy: scheduler0, no state-change triggers, stable
    per-birth random choices rather than a replay of the game's RNG stream.
    Initial particle TRS random ranges are evaluated; complete scheduling,
    dynamic random modes and native update history remain separate work.
    """
    from effect_geometry import template_sample
    result=[];pending=[];visits=0;sampled={}
    if effect.missing:return (),('特效来源缺失',)
    groups={tag:[r for r in effect.source_records if r.tag==tag]for tag in('Schd','TmLn','Bind','Emit','Ptcl','Efct')}
    draw_order=number(fields(r for r in effect.source_records if r.tag=='DwOT'),'DwOT')
    draw_layer=number(fields(r for r in effect.source_records if r.tag=='DwLy'),'DwLy')
    if draw_order not in(0,1,2):return (),('未审阅的特效绘制顺序',)
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
        return result
    def births(child,data,time,identity,max_age):
        # CN3a0611 stores CrTm as an EVENT, compared to the dispatched
        # event at3d2595. Initializer3cd9bf sends1, then0; finalizer3cddf7
        # sends2. The initial event is not an extra source-frame delay.
        # Interval emissions use the authored CrI/CrC. Random choices are stable
        # across redraws, not a replay of the game's RNG or simulation state.
        event=number(child,'CrTm',1);count=number(child,'CrCn',1);probability=number(child,'CrPr',100)
        if event not in(0,1):raise UnsupportedEffect('结束或未知事件发射尚未接入')
        if not 0<=count<=64 or not 0<=probability<=100:raise UnsupportedEffect('发射次数或概率范围')
        once=event==1;start=number(child,'StFr')+number(child,'GenD')
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
                if probability==0:continue
                if probability<100:
                    seed=(effect.sha256+repr((identity,birth,i))).encode()
                    draw=int.from_bytes(hashlib.sha256(seed).digest()[:4],'little')/2**32
                    if draw*100>=probability:continue
                sequence=i if once else int(round((birth-start)/interval))*count+i
                yield time-birth,birth,sequence
    def emit(index,time,bound,inherited,inherited_color,stack=(),inherited_at=None,
             channel_history=None,incoming=None,birth_channels=(),extra_angles=None):
        nonlocal visits
        visits+=1
        if index in stack or len(stack)>12 or visits>256:raise UnsupportedEffect('发射器循环或预算')
        nodes=records(get('Emit',index).data);data=fields(r for r in nodes if r.tag not in('ItPr','ItEm'))
        emitter_type=number(data,'EVT')
        if emitter_type not in(0,3,5):raise UnsupportedEffect('未接入发射器 '+str(emitter_type))
        if not alive(data,time):return
        if number(data,'EfNo',-1)>=0:
            # An attached effector is additional work, not a replacement for
            # the emitter. Keep its independently supported particle children.
            effector=nested(get('Efct',number(data,'EfNo')))
            pending.append('局部点光源尚未接入'if number(effector,'EfVT')==0 else'发射器效果器尚未接入')
        stationary(data,time)
        @lru_cache(maxsize=512)
        def parent_at(at):
            history=inherited if inherited_at is None else inherited_at(at)
            return history@transform(data,at,emitter=True)
        @lru_cache(maxsize=512)
        def channels_at(at):
            ancestors=()if channel_history is None else channel_history(at)
            node=coordinate_node(data,at,incoming or{},ancestors,birth_channels,
                np.zeros(3)if extra_angles is None else extra_angles)
            return (node,)+ancestors
        parent=parent_at(time)
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
                    inherited_matrix=None
                    for age,birth,occurrence in births(child,data,time,(stack,index,container,ordinal),max_age):
                        # CN3cbc88..3cbce7 captures the ordinary parent matrix
                        # separately for EACH birth. PICd3 later consumes that
                        # snapshot (3e3683), whereas PICd2 reads current parent
                        # +28 (3e3774). Sharing a current matrix across births
                        # incorrectly makes AllCreate children follow motion.
                        if number(child,'PICd')==3:
                            inherited_matrix=inherit(child,parent_at(birth),bound)
                        elif inherited_matrix is None:
                            inherited_matrix=inherit(child,parent,bound)
                        injected=np.eye(4);injected[:3,:3]=rotation(injection_angles(child,occurrence),0)
                        matrix=inherited_matrix@injected
                        if emitter_type in(3,5):
                            from effect_emitter import model_injection,sphere_injection
                            inject=model_injection if emitter_type==5 else sphere_injection
                            position,velocity=inject(effect,data,birth,occurrence,(stack,index,container,ordinal,birth,occurrence))
                            injection=np.eye(4);injection[:3,3]=position+velocity*age
                            matrix=matrix@injection
                        tint=parent_color if number(child,'PICo')else np.ones(4)
                        # Keep a source-time transform history, rather than
                        # rotating already emitted, unbound Simple centers by
                        # the current parent. AllCreate uses its own fixed birth
                        # snapshot even when descendants request later history.
                        # Selective inheritance remains pending full Euler/TRS
                        # assembly; never invent its missing historical matrix.
                        def history(at,child=child,birth=birth,occurrence=occurrence,
                                    identity=(stack,index,container,ordinal),current=time,injected=injected):
                            when=birth+at
                            mode=number(child,'PICd')
                            if when!=current and mode not in(2,3):
                                raise UnsupportedEffect('非持续继承的粒子出生变换历史')
                            value=inherit(child,parent_at(birth if mode==3 else when),bound)@injected
                            if emitter_type in(3,5):
                                from effect_emitter import model_injection,sphere_injection
                                inject=model_injection if emitter_type==5 else sphere_injection
                                position,velocity=inject(effect,data,birth,occurrence,(*identity,birth,occurrence))
                                injection=np.eye(4);injection[:3,3]=position+velocity*at
                                value=value@injection
                            return value
                        if tag=='Emit':
                            def child_channel_history(at,birth=birth,channels_at=channels_at):
                                return channels_at(birth+at)
                            emit(target,age,bound,matrix,tint,stack+(index,),history,
                                child_channel_history,child,channels_at(birth),injection_angles(child,occurrence));continue
                        particle=get(tag,target);particle_data=particle_fields(particle)
                        if not alive(particle_data,age):continue
                        # Geometry/curves depend on source and age, not on
                        # which crystal attachment spawned this instance.
                        sample_key=(target,age)
                        if sample_key not in sampled:
                            sampled[sample_key]=template_sample(effect,particle,age)
                        prototype=sampled[sample_key]
                        value={**prototype,'parent':matrix,'color':prototype['color']*tint}
                        seed=(effect.sha256+repr((stack,index,container,ordinal,birth,occurrence))).encode()
                        from effect_transform_random import has_random as random_transform,initialize as initialize_transform,values as transform_values
                        transform_state=initialize_transform(particle,seed+b':transform')if random_transform(particle)else None
                        def local_at(at):
                            coordinates=transform_values(particle,at,transform_state)if transform_state is not None else None
                            return transform(particle_data,at,random_values=coordinates)
                        if transform_state is not None:
                            coordinates=transform_values(particle,age,transform_state)
                            value.update(local=transform(particle_data,age,random_values=coordinates),local_scale=coordinates['Scl'],
                                local_rotation=rotation(coordinates['Rot'],number(particle_data,'RoOT')),
                                transform_random_bytes=transform_state)
                        if value['kind']=='powder'and 'simple_centers'not in value:
                            coordinates=coordinates if transform_state is not None else None
                            parents=channels_at(time)
                            node=coordinate_node(particle_data,age,child,parents,channels_at(birth),
                                injection_angles(child,occurrence),coordinates)
                            chain=(node,)+parents
                            value['powder_angles']=inherited_coordinates(chain,z_only=True)
                            value['powder_scale']=inherited_coordinates(chain,scale=True)*np.linalg.norm(bound[:3,:3],axis=0)
                        if 'simple_centers'in prototype and not prototype['simple_bind_parent']:
                            value['simple_birth_matrices']=np.asarray([
                                history(float(at))@local_at(float(at))
                                for at in prototype['simple_birth_age']])
                        from effect_uv import has_random,initialize,uv_nodes
                        if value['kind']!='powder'and has_random(particle):
                            from effect_geometry import uv_matrix
                            states=initialize(particle,seed)
                            value['uv_matrices']=[uv_matrix(uv,age,model=value['kind']=='model',random_state=state)
                                for uv,state in zip(uv_nodes(particle),states)]
                            value['uv_random_bytes']=states
                        if not len(value['indices']):continue
                        value.update(source_particle=target,source_emitter=index,source_age=age,source_birth=birth,
                            draw_order=draw_order,draw_layer=draw_layer,_shader_source=(effect.sha256,particle))
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
                    bound=binding(get('Bind',number(item,'BdNo',-1)),bones,bind_points)
                    emit(number(item,'EmNo',-1),time,bound,bound,np.ones(4))
                except UnsupportedEffect as error:pending.append(str(error))
    except UnsupportedEffect as error:pending.append(str(error))
    return tuple(result),tuple(sorted(set(pending)))
