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


reuse_static_parents=True
# Reviewed type-specific prewarm and transactional ordinary motion. The
# unsupported inheritance/parent/rate branches remain explicit below.
enable_ordinary_motion_history=True
enable_velocity_rotation_history=True
# Closed mode0/1 prefix, birth rules and bounded root parameter snapshots.
# Nested dynamic ancestors and variable generator phases remain explicit.
enable_selective_motion_history=True


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
    if node is None:return np.full(3,default)if rgb else default
    if node is not None:
        source,keys=curve_source(node)
        from effect_curve import ClockTimes
        if isinstance(time,ClockTimes):
            time=time.unwrapped if not rgb and number(source,'BvPo')==2 else time.wrapped
        if keys:
            if len(keys)==1:time=keys[0][0]
            elif rgb or all(0<=k[0]<=0x3fff for k in keys):
                if time<keys[0][0]and number(source,'BvPr')in(0,-1):time=keys[0][0]
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
    data=nested(node);rgb=curve(data.get('RGB'),time,1,rgb=True);alpha=curve(data.get('A'),time,1)
    # Missing RGB/A keys are valid constant-one channels. Their integer
    # defaults must not turn the color packet into an integer array before
    # applying authored fractional scale/brightness curves.
    value=np.empty(4,dtype=np.result_type(rgb,alpha,np.float32));value[:3]=rgb;value[3]=alpha
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


def injection_angles(child,occurrence,*,accepted_ordinal=None):
    # CN3cc9e8 clears per-link signed16 counters. 3d26a0 uses the OLD
    # counter times BIA XYZ, then increments it. BIA is not a fixed rotation.
    source=np.array([number(child,'BIA'+a,0.,real=True)for a in'XYZ'],dtype=np.float32)
    if np.any(source)and number(child,'CrPr',100)!=100 and accepted_ordinal is None:
        raise UnsupportedEffect('带随机淘汰的累计注入角度计数尚未接入')
    ordinal=(int(occurrence if accepted_ordinal is None else accepted_ordinal)+32768)%65536-32768
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


def child_requested_life(child,random_word):
    # Original3cf765..3cf7e1: inclusive signed integer range, using low16
    # of the ordinary xorshift word. Preserve signed32 multiply/divide.
    value=number(child,'OvrV');spread=number(child,'OvrR')
    if not 0<=spread<=0x7fffffff:raise ValueError('粒子指定寿命随机范围不正确')
    signed=lambda n:((n+0x80000000)&0xffffffff)-0x80000000
    product=signed(signed(spread*2+1)*(random_word&65535))
    offset=(product+(65535 if product<0 else 0))>>16
    return signed(signed(offset+value)-spread)


def child_clock_rate(child,data,*,random_word=None,maximum_lifetime=False):
    """Ordinary particle source-time rate, before birth-window pruning.

    CN3cf73b..3cf816 keeps the authored lifetime and sets speed to
    authored_life / requested_life. Curves use that scaled source clock.
    """
    if not number(child,'bOvr'):return 1.
    if number(child,'IPbL'):
        raise UnsupportedEffect('继承的粒子寿命尚未接入')
    if number(data,'bSCt')or 'PrVT'not in data:
        raise UnsupportedEffect('简化粒子或发射器的指定寿命尚未接入')
    life=nested(data.get('Life'))
    if number(life,'ValR',0.,real=True):raise UnsupportedEffect('随机粒子的指定寿命尚未接入')
    original=np.float32(number(life,'Val',-1.,real=True));requested=number(child,'OvrV');spread=number(child,'OvrR')
    if spread:
        # Retention uses an upper bound only; the actual clock is independently
        # selected once for each birth below. Nonpositive requests use rate1.
        if maximum_lifetime:
            if not 0<spread<=16383 or not -0x40000000<requested<0x40000000:
                raise UnsupportedEffect('超大随机指定寿命的保留范围尚未接入')
            requested=max(requested+spread,float(original)if requested-spread<=0 else 0.)
        elif random_word is None:raise UnsupportedEffect('随机指定寿命需要出生实例')
        else:requested=child_requested_life(child,random_word)
    return float(np.float32(original/np.float32(requested)))if original>0 and requested>0 else 1.


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


def binding(node,bones,bind_points=None,*,components=False):
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
    auxiliary=np.eye(4,dtype='<f4')
    if number(data,'bFTO'):
        # Native Point3c0520 separates normalized orientation into+280.
        # It adds original constant2135568 (float32 epsilon) to lengths.
        from effect_coordinates import point_auxiliary
        auxiliary[:3,:3]=point_auxiliary(result,True)[0][:3,:3]
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
    return (result,auxiliary)if components else result


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
    from effect_geometry import shape_geometry
    # Legacy implicit quad indices use a clockwise corner sequence.
    base=shape_geometry(frame)['positions'][[0,1,3,2]]
    return {'kind':'quad','frame':frame,'local':local,'parent':parent,'positions':base,'uv':uv,
        'color':color(data.get('Col'),time)*parent_color,'texture':effect.textures[index],
        'color_to_alpha':bool(number(texture,'bC2A')),'filter':number(texture,'TFT'),
        'address':(number(texture,'TBUT'),number(texture,'TBVT')),
        'blend':mode,'soft':bool(number(data,'DsSp')),'soft_range':soft_particle_range(effect)if number(data,'DsSp')else None,
        'depth_test':bool(number(data,'DsDt',1)),'depth_write':bool(number(data,'DsDw')),
        'cull':number(data,'CulT'),'depth_offset':number(data,'DpOf',0.,real=True)}


def prepare(effect,bones,frame=30.,*,bind_points=None,simple_history=None,timeline_entries=None,timeline_events=None,emitter_history=None):
    """Normal equipment scheduler, deterministic frozen source sample.

    Independent preview policy: scheduler0, no state-change triggers, stable
    per-birth random choices rather than a replay of the game's RNG stream.
    Initial particle TRS random ranges are evaluated; complete scheduling,
    dynamic random modes and native update history remain separate work.
    """
    from effect_geometry import template_sample,uv_matrix
    from effect_emitter import model_injection,model_normal_point,sphere_injection,cylinder_injection,cylinder_motion_birth,constant_particle_travel,cylinder_saved_index,cylinder_live_position
    from effect_transform_random import has_random as random_transform,initialize as initialize_transform,values as transform_values,resolved as resolved_transform
    from effect_uv import has_random,initialize,uv_nodes,sample_time
    from effect_texture import needs_state as texture_needs_state,initialize as initialize_texture
    from effect_coordinates import selective as selective_matrix
    from effect_emitter_history import EmitterHistory
    from effect_curve import ClockTimes
    from effect_particle_clock import retention as particle_retention,clock as particle_clock
    from effect_mode8 import parent_frame,point_origin,local_matrix as mode8_local
    from effect_coordinates import multiply as multiply_coordinates
    from effect_particle_motion import MotionHistory,needed as motion_needed,coordinates as motion_coordinates,rotated as velocity_rotated,selective_coordinates as selective_motion_coordinates
    if emitter_history is None:emitter_history=EmitterHistory();emitter_history.begin()
    motion_history=getattr(getattr(simple_history,'__self__',None),'motion',None)
    if motion_history is None:motion_history=MotionHistory();motion_history.begin()
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
        if number(child,'PrLk',-1)>=0 or number(child,'IPbV')or number(child,'IPbL'):
            raise UnsupportedEffect('链接、速度或寿命继承')
        if mode==8:result=parent_frame(parent).copy()
        elif mode in(2,3):result=parent.copy()
        elif mode in(0,1):
            result=bound.copy();result[:3,3]=parent[:3,3]
            if mode==1:
                relative=np.linalg.solve(bound[:3,:3],parent[:3,:3]);scales=np.linalg.norm(relative,axis=0)
                if np.any(scales<1e-8):raise ValueError('Singular AVFX inherited transform')
                basis=relative/scales if number(child,'ICbR')else np.eye(3)
                if number(child,'ICbS'):basis=basis@np.diag(scales)
                result[:3,:3]=bound[:3,:3]@basis
        else:raise UnsupportedEffect('继承模式 '+str(mode))
        return result
    def births(child,data,time,identity,max_age,emission_end=-1.,loop_state=None,instance=()):
        # CN3a0611 stores CrTm as an EVENT, compared to the dispatched
        # event at3d2595. Initializer3cd9bf sends1, then0; finalizer3cddf7
        # sends2. The initial event is not an extra source-frame delay.
        # Interval emissions use the authored CrI/CrC. Random choices are stable
        # across redraws, not a replay of the game's RNG or simulation state.
        event=number(child,'CrTm',1);count=number(child,'CrCn',1);probability=number(child,'CrPr',100)
        if event not in(0,1):raise UnsupportedEffect('结束或未知事件发射尚未接入')
        if not 0<=count<=64 or not 0<=probability<=100:raise UnsupportedEffect('发射次数或概率范围')
        once=event==1;start=0.
        delay=number(child,'GenD')
        if once and delay:
            if delay<0:raise UnsupportedEffect('负生成延迟历史尚未接入')
            staggered=bool(number(child,'bGD'))
            # Original initializer skips this deferred helper when CrCn<=1
            # and bGD is enabled; direct initial-event gate also rejects it.
            if staggered and count<=1:return
            delayed=emitter_history.delayed(('delay',identity,instance),time,count,delay,staggered,max_age)
            selected=tuple((e.time,e.count,e.first)for e in delayed.events)
            time=delayed.elapsed
        else:delayed=None
        if emission_end>=0 and start>emission_end:return
        local_time=min(time,emission_end)if emission_end>=0 else time
        interval=curve(data.get('CrI'),local_time,1)
        if not once and loop_state is None and interval<=0:raise UnsupportedEffect('非正发射间隔')
        count=count if once else max(1,int(curve(data.get('CrC'),local_time,1)))
        if delayed is not None:pass
        elif loop_state is not None:
            selected=((0.,count,0,ClockTimes(0.,0.)),)if once else tuple(
                (e.time,e.count,e.first,e.parameter_clock)for e in loop_state.events)
        elif once:times=[start]
        else:
            if max_age<0 and emission_end<0:raise UnsupportedEffect('无限寿命的重复发射尚未接入')
            first=0 if max_age<0 else max(0,math.floor((time-max_age-start)/interval)+1)
            last=math.floor(((min(time,emission_end)if emission_end>=0 else time)-start)/interval)
            if last-first>512:raise UnsupportedEffect('发射预算')
            times=[start+i*interval for i in range(first,last+1)]
        if loop_state is None and delayed is None:
            if len(times)*count>512:raise UnsupportedEffect('发射预算')
            selected=tuple((birth,count,0 if once else int(round((birth-start)/interval))*count)for birth in times)
        counted=probability not in(0,100)and any(number(child,'BIA'+a,0.,real=True)for a in'XYZ')
        if counted and(loop_state is not None or delayed is not None or not once and any(
            number(nested(data.get(n)),'RanT')or len(curve_source(data.get(n))[1]or())>1 for n in('CrI','CrC'))):
            raise UnsupportedEffect('变动或延迟发射的随机累计注入计数尚未接入')
        def accepted(birth,i):
            seed=(effect.sha256+repr((identity,birth,i))).encode()
            draw=int.from_bytes(hashlib.sha256(seed).digest()[:4],'little')/2**32
            return draw*100<probability
        def trial_accepted(sequence):
            return accepted(start if once else start+(sequence//count)*interval,sequence%count)
        if counted:emitter_history.retain_injection(('BIA',identity,instance))
        for emission in selected:
            birth,count,first=emission[:3]
            parameter_clock=emission[3]if len(emission)>3 else None
            for i in range(count):
                if time<birth:continue
                if probability==0:continue
                if probability<100:
                    if not accepted(birth,i):continue
                sequence=first+i
                angle_ordinal=emitter_history.injection_ordinal(('BIA',identity,instance),sequence,trial_accepted)if counted else None
                yield time-birth,birth,sequence,parameter_clock,angle_ordinal
    def emit(index,time,bound,inherited,inherited_color,stack=(),inherited_at=None,
             channel_history=None,incoming=None,birth_channels=(),extra_angles=None,auxiliary=None,instance=(),parent_phase_supported=False,inherited_static=False):
        nonlocal visits
        visits+=1
        if index in stack or len(stack)>12 or visits>256:raise UnsupportedEffect('发射器循环或预算')
        nodes=records(get('Emit',index).data);data=fields(r for r in nodes if r.tag not in('ItPr','ItEm'))
        emitter_type=number(data,'EVT')
        if emitter_type not in(0,3,4,5):raise UnsupportedEffect('未接入发射器 '+str(emitter_type))
        # Native end stops this emitter's clock/emission, not its children.
        # 3b15d0 still traverses children; removal at3b1630 requires the
        # CHILD's ended flag AND an empty child list. Keep child age running.
        life=nested(data.get('Life'));emission_end=number(life,'Val',-1.,real=True)
        if number(life,'ValR',0.,real=True):raise UnsupportedEffect('发射器随机寿命尚未接入')
        loop_end=number(data,'LpEd')
        particles,emitters=emitter_items(nodes,data)
        loop_state=None
        # Root Point has no time-varying generator payload. A Cylinder with
        # constant source parameters also needs only its recorded TRS phase.
        # Nested parents, Simple subbirths and other generators require their
        # own matrix/payload history and remain behind their existing guards.
        generator_data=nested(data.get('Data'))
        fixed_cylinder_parameters=emitter_type==4 and all(
            not number(nested(generator_data.get(name)),'RanT')and len(curve_source(generator_data.get(name))[1]or())<=1
            for name in('Len','Rad','IjS','IjSR','AnX','AnY','AnZ','AnXR','AnYR','AnZR'))
        root_parameter_snapshots=(incoming is None and inherited_at is None and number(data,'RBDT',2)==2
            and(emitter_type==0 or fixed_cylinder_parameters)
            and not emitters and all(number(c,'PICd')in(0,1,2,3)
                and not number(particle_fields(get('Ptcl',number(c,'TgtB',-1))),'bSCt')
                for c in particles if number(c,'bEnb',1)))
        def constant_curve(name,source=data,default=0):
            node=source.get(name)
            return not number(nested(node),'RanT')and len(curve_source(node)[1]or())<=1 and curve(node,0,default)==default
        # A Model's fixed mesh point can carry a child emitter without
        # pretending to support its unreviewed nonzero birth direction.
        fixed_model_carrier=(emitter_type==5 and number(generator_data,'GeMT')in(2,3)
            and all(constant_curve(n,generator_data)for n in('IjS','IjSR','AnX','AnY','AnZ','AnXR','AnYR','AnZR')))
        native_loop=bool(loop_end and(emission_end<0 or emission_end>=loop_end))
        phase_supported=(number(data,'RBDT',2)==2 and(emitter_type==0 or fixed_model_carrier or fixed_cylinder_parameters)
            and all(constant_curve(n)for n in('Gra','VRX','VRY','VRZ'))
            and all(number(c,'PICd')in(0,1,2,3)for c in(*particles,*emitters)if number(c,'bEnb',1))
            and(emitter_type==0 or all(number(c,'PICd')in(2,3)for c in emitters if number(c,'bEnb',1)))
            and(native_loop or all(constant_axes(data.get(n),3)for n in('Scl','Rot','Pos')))
            and(incoming is None and inherited_at is None or parent_phase_supported and incoming is not None
                and number(incoming,'PICd')in(0,1,2,3)and child_clock_rate(incoming,data)==1
                and not any(number(incoming,n)for n in('StFr','GenD','LoDr')))
            and(native_loop or all(number(c,'CrTm',1)==1 for c in(*particles,*emitters)if number(c,'bEnb',1))))
        if loop_end and(emission_end<0 or emission_end>=loop_end):
            if incoming is not None and(child_clock_rate(incoming,data)!=1 or number(incoming,'StFr')):
                raise UnsupportedEffect('循环子发射器预推进或倍率历史尚未接入')
            # The source parameter phase is distinct from emission. Until
            # varying transform/ARs history is closed, keep that branch
            # explicit rather than deriving births from modulo alone.
            if not all(constant_axes(data.get(n),3)for n in('Scl','Rot','Pos'))and not root_parameter_snapshots and not phase_supported and not(
                emitter_type in(0,5)and not emitters and all(
                    number(c,'PICd')==2 and number(c,'CrTm',1)==1 for c in particles if number(c,'bEnb',1))):
                raise UnsupportedEffect('循环发射器变动坐标阶段历史尚未接入')
            limits=[]
            for child in particles:
                if not number(child,'bEnb',1)or number(child,'CrTm',1)!=0:continue
                target_data=particle_fields(get('Ptcl',number(child,'TgtB',-1)))
                limit=particle_retention(target_data)
                limits.append(limit/child_clock_rate(child,target_data,maximum_lifetime=True)if limit>=0 else -1.)
            window=-1. if emitters or any(v<0 for v in limits)else max(limits,default=0.)
            loop_state=emitter_history.sample((stack,index,instance),data,time,window)
            time=loop_state.elapsed;emission_end=-1.
        emitter_time=min(time,emission_end)if emission_end>=0 else time
        if loop_state is not None:emitter_time=loop_state.evaluated
        if number(data,'EfNo',-1)>=0:
            # An attached effector is additional work, not a replacement for
            # the emitter. Keep its independently supported particle children.
            effector=nested(get('Efct',number(data,'EfNo')))
            pending.append('局部点光源尚未接入'if number(effector,'EfVT')==0 else'发射器效果器尚未接入')
        stationary(data,emitter_time)
        def source_time(at,parameter_clock=None):
            if parameter_clock is not None:return parameter_clock
            if loop_state is None:return min(at,emission_end)if emission_end>=0 else at
            if at==time:return loop_state.evaluated
            total=np.float32(at);cycle=total;start=number(data,'LpSt')
            while cycle>=loop_end:cycle=np.float32(cycle-np.float32(loop_end-start))
            return ClockTimes(float(cycle),float(total))
        custom=incoming is not None and number(incoming,'PICd')in(0,1)
        fixed_parent=(inherited@transform(data,0,emitter=True))if not custom and reuse_static_parents and(inherited_at is None or inherited_static)and all(
            constant_axes(data.get(name),3)for name in('Scl','Rot','Pos'))else None
        @lru_cache(maxsize=512)
        def parent_at(at,parameter_clock=None):
            if fixed_parent is not None:return fixed_parent
            local_at=source_time(at,parameter_clock)
            history=inherited if inherited_at is None else inherited_at(at)
            if custom:
                chain=channels_at(at,parameter_clock);angles=inherited_coordinates(chain);scales=inherited_coordinates(chain,scale=True)
                return selective_matrix(scales,rotation(angles,number(data,'ROT')),axes(data.get('Pos'),local_at),number(data,'CCOT'),
                    auxiliary if number(incoming,'PICd')==1 and number(incoming,'ICbB')else None,history[:3,3])
            return history@transform(data,local_at,emitter=True)
        @lru_cache(maxsize=512)
        def channels_at(at,parameter_clock=None):
            ancestors=()if channel_history is None else channel_history(at)
            local_at=source_time(at,parameter_clock)
            node=coordinate_node(data,local_at,incoming or{},ancestors,birth_channels,
                np.zeros(3)if extra_angles is None else extra_angles)
            return (node,)+ancestors
        parent=parent_at(time)
        parent_color=inherited_color*color(data.get('Col'),emitter_time)
        for container,children,tag in(('ItPr',particles,'Ptcl'),('ItEm',emitters,'Emit')):
            for ordinal,child in enumerate(children):
                if not number(child,'bEnb',1):continue
                try:
                    target=number(child,'TgtB',-1)
                    target_node=get(tag,target)
                    target_data=particle_fields(target_node)if tag=='Ptcl'else fields(r for r in records(target_node.data)if r.tag not in('ItPr','ItEm'))
                    max_age=(particle_retention(target_data)if tag=='Ptcl'else number(nested(target_data.get('Life')),'Val',-1.,real=True))
                    clock_rate=child_clock_rate(child,target_data,maximum_lifetime=True)
                    if max_age>=0:max_age/=clock_rate
                    inheritance_mode=number(child,'PICd');simple=bool(number(target_data,'bSCt'))
                    if inheritance_mode==8:
                        if (emitter_type!=0 or tag!='Ptcl' or number(target_data,'PrVT')!=1 or not simple
                            or number(nested(target_data.get('Smpl')),'bBnP') or number(child,'ICSK',0.,real=True)!=0):
                            raise UnsupportedEffect('该模式8类型或非零距离继承历史尚未接入')
                        if simple_history is None:raise UnsupportedEffect('模式8需要明确的Simple逐次更新历史')
                    if emitter_type==4:
                        selective_cylinder=(enable_selective_motion_history and tag=='Ptcl'and not simple
                            and(fixed_parent is not None or root_parameter_snapshots and loop_state is not None)
                            and inheritance_mode in(0,1)and not(inheritance_mode==1 and number(child,'ICbP')and not fixed_cylinder_parameters)
                            and clock_rate==1 and not number(child,'StFr'))
                        if inheritance_mode not in(2,3)and not selective_cylinder:raise UnsupportedEffect('圆柱选择继承创建坐标尚未接入')
                        if number(nested(data.get('Data')),'GeMT')in(1,3)and sum(
                            bool(number(c,'bEnb',1))for c in(*particles,*emitters))!=1:
                            raise UnsupportedEffect('圆柱顺序发射跨子项计数尚未接入')
                    transform_random=tag=='Ptcl'and random_transform(target_node)
                    uv_random=tag=='Ptcl'and has_random(target_node)
                    if tag=='Emit'and number(child,'LoDr')!=0:
                        raise UnsupportedEffect('子发射器出生方向矩阵尚未接入')
                    inherited_matrix=None
                    warmup=max(0,number(child,'StFr'))
                    if warmup and(simple or tag=='Emit'or emitter_type!=0):
                        raise UnsupportedEffect('该子项的出生预推进状态尚未接入')
                    for elapsed,birth,occurrence,birth_parameter_clock,angle_ordinal in births(child,data,time,(stack,index,container,ordinal),max_age,emission_end,loop_state,instance):
                        birth_angles=injection_angles(child,occurrence,accepted_ordinal=angle_ordinal)
                        if number(child,'bOvr')and number(child,'OvrR'):
                            from effect_simple import SourceRandom
                            birth_seed=(effect.sha256+repr((stack,index,container,ordinal,birth,occurrence))).encode()
                            clock_rate=child_clock_rate(child,target_data,random_word=SourceRandom(birth_seed+b':lifetime').word())
                        age=float(np.float32(np.float32(elapsed+warmup)*np.float32(clock_rate)))if clock_rate!=1 or warmup else elapsed
                        born_parent=None;born_channels=None
                        if phase_supported:
                            key=('birth',stack,index,container,ordinal,birth,occurrence,instance)
                            snapshot=emitter_history.birth_snapshot(key,age,
                                lambda:(parent_at(birth,birth_parameter_clock),channels_at(birth,birth_parameter_clock)))
                            born_parent=snapshot.parent();born_channels=snapshot.channels()
                        @lru_cache(maxsize=1)
                        def birth_parent_matrix(saved=born_parent,birth=birth,clock=birth_parameter_clock):
                            return saved if saved is not None else parent_at(birth,clock)
                        @lru_cache(maxsize=1)
                        def birth_parent_channels(saved=born_channels,birth=birth,clock=birth_parameter_clock):
                            return saved if saved is not None else channels_at(birth,clock)
                        # CN3cbc88..3cbce7 captures the ordinary parent matrix
                        # separately for EACH birth. PICd3 later consumes that
                        # snapshot (3e3683), whereas PICd2 reads current parent
                        # +28 (3e3774). Sharing a current matrix across births
                        # incorrectly makes AllCreate children follow motion.
                        if (inheritance_mode in(0,3)or inheritance_mode==1 and not number(child,'ICbP'))and fixed_parent is None:
                            inherited_matrix=inherit(child,birth_parent_matrix(),bound)
                        elif inherited_matrix is None:
                            inherited_matrix=inherit(child,parent,bound)
                        injected=np.eye(4);injected[:3,:3]=rotation(birth_angles,0)
                        # Mode8 adds the birth's BIA to its OWN Euler channels;
                        # it is not a separate rotation of the parent's basis.
                        origin=point_origin(parent_at(birth))if inheritance_mode==8 else None
                        if inheritance_mode==8:injected=np.eye(4)
                        matrix=inherited_matrix@injected
                        emitted_position=np.zeros(3,dtype='<f4');velocity=np.zeros(3,dtype='<f4')
                        ordinary_motion=False
                        if emitter_type in(3,4,5):
                            inject={3:sphere_injection,4:cylinder_injection,5:model_injection}[emitter_type]
                            parameters={'basis':birth_parent_matrix()[:3,:3]}if emitter_type==4 and(
                                fixed_parent is not None or root_parameter_snapshots and loop_state is not None)else{}
                            position,velocity=inject(effect,data,birth,occurrence,(stack,index,container,ordinal,birth,occurrence),**parameters)
                            emitted_position=position
                            ordinary_motion=enable_ordinary_motion_history and tag=='Ptcl'and not simple and(motion_needed(target_data,velocity)or
                                enable_selective_motion_history and(emitter_type==4 and inheritance_mode in(0,1)or(number(child,'LoDr')&255)==1))
                            travel=np.zeros(3)if ordinary_motion else constant_particle_travel(target_data,age,velocity)if tag=='Ptcl'and not simple else velocity*age
                            if travel is None:
                                if emitter_type==4:raise UnsupportedEffect('圆柱变动位移系数更新历史尚未接入')
                                pending.append('变动粒子位移系数的更新历史尚未接入');travel=velocity*age
                            injection=np.eye(4);injection[:3,3]=position+travel
                            # Cylinder's native generator returns the emitter
                            # point before the child's BIA orientation is used.
                            matrix=inherited_matrix@injection@injected if emitter_type==4 else matrix@injection
                        tint=parent_color if number(child,'PICo')else np.ones(4)
                        # Keep a source-time transform history, rather than
                        # rotating already emitted, unbound Simple centers by
                        # the current parent. AllCreate uses its own fixed birth
                        # snapshot even when descendants request later history.
                        # Selective point links use this carrier's origin only;
                        # their basis is assembled from explicit channels below.
                        def history(at,child=child,birth=birth,occurrence=occurrence,
                                    identity=(stack,index,container,ordinal),current=time,injected=injected,target_data=target_data,tag=tag,
                                    clock_rate=clock_rate,warmup=warmup,birth_parameter_clock=birth_parameter_clock,birth_parent_matrix=birth_parent_matrix):
                            when=birth+at/clock_rate-warmup
                            mode=number(child,'PICd')
                            if when!=current and mode not in(2,3)and emitter_type!=0:
                                raise UnsupportedEffect('非持续继承的粒子出生变换历史')
                            capture=mode in(0,3)or mode==1 and not number(child,'ICbP')
                            carrier=inherit(child,birth_parent_matrix()if capture else parent_at(when),bound)
                            value=carrier@injected
                            if emitter_type in(3,4,5):
                                inject={3:sphere_injection,4:cylinder_injection,5:model_injection}[emitter_type]
                                parameters={'basis':carrier[:3,:3]}if emitter_type==4 and fixed_parent is not None else{}
                                position,velocity=inject(effect,data,birth,occurrence,(*identity,birth,occurrence),**parameters)
                                travel=constant_particle_travel(target_data,at,velocity)if tag=='Ptcl'and not number(target_data,'bSCt')else velocity*at
                                if travel is None and emitter_type==4:raise UnsupportedEffect('圆柱变动位移系数更新历史尚未接入')
                                injection=np.eye(4);injection[:3,3]=position+(velocity*at if travel is None else travel)
                                value=carrier@injection@injected if emitter_type==4 else value@injection
                            return value
                        if tag=='Emit':
                            def child_channel_history(at,birth=birth,channels_at=channels_at):
                                return channels_at(birth+at)
                            emit(target,age,bound,matrix,tint,stack+(index,),history,
                                child_channel_history,child,birth_parent_channels(),birth_angles,auxiliary,
                                instance+(container,ordinal,birth,occurrence),parent_phase_supported=phase_supported,
                                inherited_static=emitter_type==0 and fixed_parent is not None);continue
                        particle=target_node;particle_data=target_data
                        ordinary_motion=ordinary_motion or enable_ordinary_motion_history and not simple and(motion_needed(particle_data,velocity)or
                            enable_selective_motion_history and(number(child,'LoDr')&255)==1)
                        motion_state=None
                        velocity_birth=None
                        motion_basis=None
                        if ordinary_motion:
                            selective_model=(emitter_type==5 and fixed_parent is not None and fixed_model_carrier
                                and number(generator_data,'GeMT')==3 and tag=='Ptcl'and not simple
                                and number(child,'CrPr',100)==100
                                and sum(bool(number(c,'bEnb',1))for c in(*particles,*emitters))==1)
                            selective_motion=(enable_selective_motion_history and inheritance_mode in(0,1)and(emitter_type in(0,4)or selective_model)
                                and not(emitter_type==4 and inheritance_mode==1 and number(child,'ICbP')and not fixed_cylinder_parameters))
                            snapshot_parent=((root_parameter_snapshots or phase_supported)and loop_state is not None
                                and(inheritance_mode in(2,3)or selective_motion)and birth_parameter_clock is not None)
                            # A fixed parent needs no external history rescale;
                            # its child replays at the selected source-clock age.
                            if (inheritance_mode not in(2,3)and not selective_motion)or(fixed_parent is None and not snapshot_parent)or(clock_rate!=1 and fixed_parent is None)or warmup:
                                raise UnsupportedEffect('该继承或父变换的普通粒子运动阶段历史尚未接入')
                            if emitter_type==0:emitted_position=birth_parent_matrix()[:3,3].copy()if selective_motion else point_origin(birth_parent_matrix())
                            local_direction=((number(child,'LoDr')+128)&255)-128
                            if enable_velocity_rotation_history and(selective_motion or velocity_rotated(particle_data)or local_direction==1):
                                if emitter_type==0:
                                    velocity_birth={'direction':np.array((0,0,1),dtype='<f4'),'local_direction':local_direction}
                                elif emitter_type==4:
                                    emitted_position,velocity,direction=cylinder_motion_birth(effect,data,birth,occurrence,
                                        (stack,index,container,ordinal,birth,occurrence),birth_parent_matrix(),inheritance_mode)
                                    velocity_birth={'direction':direction,'local_direction':local_direction}
                                elif selective_model:
                                    emitted_position,velocity,direction=model_normal_point(effect,data,occurrence,birth_parent_matrix())
                                    velocity_birth={'direction':direction,'local_direction':local_direction}
                                else:raise UnsupportedEffect('该发射器的普通粒子出生方向尚未接入')
                                if selective_motion:velocity_birth['birth_parent']=birth_parent_matrix()
                                from effect_velocity_rotation import construct
                                motion_basis=construct(velocity,velocity_birth['direction'],local_direction,velocity_birth.get('birth_parent'))[1]
                            identity=(stack,index,container,ordinal,birth,occurrence,instance,particle)
                            motion_state=motion_history.sample(identity,particle_data,age,velocity,**(velocity_birth or{}))
                            if motion_state.ended:continue
                        sample_age=particle_clock(particle_data,age)
                        if motion_state is not None:sample_age=ClockTimes(motion_state.wrapped,motion_state.unwrapped)
                        if not alive(particle_data,sample_age.wrapped if isinstance(sample_age,ClockTimes)else sample_age):continue
                        seed=(effect.sha256+repr((stack,index,container,ordinal,birth,occurrence))).encode()
                        transform_state=initialize_transform(particle,seed+b':transform')if transform_random else None
                        def local_at(at):
                            at=particle_clock(particle_data,at)
                            coordinates=transform_values(particle,at,transform_state)if transform_state is not None else None
                            if inheritance_mode==8:
                                coordinate=lambda name,default=0:axes(particle_data.get(name),at,default)if coordinates is None else coordinates[name]
                                return mode8_local(coordinate('Scl',1),coordinate('Rot'),coordinate('Pos'),
                                    birth_angles,origin,number(particle_data,'CCOT'),number(particle_data,'RoOT'))
                            return transform(particle_data,at,random_values=coordinates)
                        def complete_at(at):
                            return multiply_coordinates(local_at(at),history(at))if inheritance_mode==8 else history(at)@local_at(at)
                        # Geometry/curves depend on source and age, not on
                        # which crystal attachment spawned this instance.
                        simple_updates=None
                        if simple and simple_history is not None:
                            identity=(stack,index,container,ordinal,birth,occurrence,instance)
                            simple_updates=simple_history(identity,particle,age,complete_at)
                            if simple_updates is None:raise UnsupportedEffect('缺少明确的Simple逐次更新历史')
                        texture_random=initialize_texture(particle,seed+b':texture')if texture_needs_state(particle)else None
                        sample_key=(target,sample_age,texture_random,ordinary_motion)if simple_updates is None else(target,sample_age,identity,texture_random)
                        if sample_key not in sampled:
                            sampled[sample_key]=template_sample(effect,particle,sample_age,
                                defer_transform=transform_random,defer_uv=uv_random,simple_updates=simple_updates,texture_random=texture_random,
                                ordinary_motion=ordinary_motion)
                        prototype=sampled[sample_key]
                        value={**prototype,'parent':matrix,'color':prototype['color']*tint}
                        if inheritance_mode==8:value['coordinate_matrix']=complete_at(age)
                        if transform_state is not None:
                            coordinates,local,rotate=resolved_transform(particle,sample_age,transform_state)
                            value.update(local=local.copy(),local_scale=coordinates['Scl'].copy(),
                                local_rotation=rotate.copy(),
                                transform_random_bytes=transform_state)
                        if not simple and inheritance_mode in(2,3)and motion_state is None and np.any(birth_angles):
                            # Ordinary BIA is added to the particle's Euler
                            # channels before RoOT/TRS, not a separate parent
                            # matrix rotation (these do not commute).
                            own=transform_values(particle,sample_age,transform_state)if transform_state is not None else{
                                name:axes(particle_data.get(name),sample_age,default)for name,default in(('Scl',1),('Rot',0),('Pos',0))}
                            from effect_particle_motion import Motion
                            value['coordinate_matrix']=motion_coordinates(Motion(),own['Scl'],own['Rot'],own['Pos'],
                                birth_angles,emitted_position,inherited_matrix,number(particle_data,'CCOT'),number(particle_data,'RoOT'))
                            value['coordinate_billboard_rotation']=rotation(np.float32(np.asarray(own['Rot'],dtype='<f4')+birth_angles),number(particle_data,'RoOT'))
                            value['coordinate_scale']=np.linalg.norm(inherited_matrix[:3,:3],axis=0)*own['Scl']
                        coordinate_age=motion_state.evaluated if motion_state is not None else sample_age
                        if motion_state is not None:
                            coordinates=transform_values(particle,coordinate_age,transform_state)if transform_state is not None else{
                                name:axes(particle_data.get(name),coordinate_age,default)for name,default in(('Scl',1),('Rot',0),('Pos',0))}
                            extra=birth_angles
                            value.update(parent=inherited_matrix,local_scale=coordinates['Scl'].copy(),
                                local_rotation=rotation(coordinates['Rot'],number(particle_data,'RoOT')),
                                coordinate_billboard_rotation=rotation(np.float32(np.asarray(coordinates['Rot'],dtype='<f4')+extra),number(particle_data,'RoOT')),
                                ordinary_motion_policy='explicit-native-update-history')
                            if inheritance_mode in(2,3):
                                value['coordinate_matrix']=motion_coordinates(motion_state,coordinates['Scl'],coordinates['Rot'],coordinates['Pos'],
                                    extra,emitted_position,inherited_matrix,number(particle_data,'CCOT'),number(particle_data,'RoOT'))
                            if velocity_birth is not None:
                                value['velocity_rotation_policy']='native-generator-birth-and-update'
                                value['velocity_rotation_nonzero']=bool(np.any(velocity)and velocity_rotated(particle_data))
                        if value['kind']=='powder'and 'simple_centers'not in value:
                            coordinates=coordinates if transform_state is not None else None
                            parents=channels_at(time)
                            node=coordinate_node(particle_data,coordinate_age,child,parents,birth_parent_channels(),
                                birth_angles,coordinates)
                            chain=(node,)+parents
                            value['powder_angles']=inherited_coordinates(chain,z_only=True)
                            value['powder_scale']=inherited_coordinates(chain,scale=True)*np.linalg.norm(bound[:3,:3],axis=0)
                        if not simple and inheritance_mode in(0,1):
                            if inheritance_mode==1 and emitter_type!=0 and motion_state is None:
                                raise UnsupportedEffect('非点发射器的选择继承创建坐标')
                            coordinates=transform_values(particle,coordinate_age,transform_state)if transform_state is not None else None
                            parents=channels_at(time)
                            node=coordinate_node(particle_data,coordinate_age,child,parents,birth_parent_channels(),birth_angles,coordinates)
                            chain=(node,)+parents;scales=inherited_coordinates(chain,scale=True)
                            angles=inherited_coordinates(chain);pos=axes(particle_data.get('Pos'),coordinate_age)if coordinates is None else coordinates['Pos']
                            origin=(parent_at(time)[:3,3]if inheritance_mode==1 and number(child,'ICbP')else
                                birth_parent_matrix()[:3,3]if emitter_type==0 else matrix[:3,3])
                            if inheritance_mode==1 and number(child,'ICbP')and emitter_type==4:
                                saved_index=cylinder_saved_index(effect,data,occurrence,(stack,index,container,ordinal,birth,occurrence))
                                live_clock=ClockTimes(loop_state.wrapped,loop_state.unwrapped)if loop_state is not None else emitter_time
                                origin=cylinder_live_position(data,live_clock,saved_index,parent_at(time))
                                value['cylinder_live_position_index']=saved_index
                            elif inheritance_mode==1 and number(child,'ICbP')and emitter_type==5 and motion_state is not None:
                                origin=model_normal_point(effect,data,occurrence,parent_at(time))[0]
                            if motion_state is not None:
                                if not(inheritance_mode==1 and number(child,'ICbP')):origin=emitted_position
                                binder_auxiliary=auxiliary if inheritance_mode==1 and number(child,'ICbB')else None
                                value['coordinate_matrix']=selective_motion_coordinates(motion_state,scales,
                                    rotation(angles,number(particle_data,'RoOT')),pos,origin,number(particle_data,'CCOT'),motion_basis,local_direction,binder_auxiliary)
                                value['selective_motion_policy']='native-mode01-static-parent'if fixed_parent is not None else'native-mode01-root-emission-snapshots'
                                value['selective_binder_motion']=binder_auxiliary is not None
                            else:
                                value['coordinate_matrix']=selective_matrix(scales,rotation(angles,number(particle_data,'RoOT')),pos,
                                    number(particle_data,'CCOT'),auxiliary if inheritance_mode==1 and number(child,'ICbB')else None,origin)
                            value['coordinate_scale']=scales
                            value['coordinate_billboard_rotation']=rotation(inherited_coordinates(chain,z_only=True),number(particle_data,'RoOT'))
                        if 'simple_centers'in prototype and not prototype['simple_bind_parent']and not prototype.get('simple_centers_world'):
                            value['simple_birth_matrices']=np.asarray([
                                complete_at(float(at))
                                for at in prototype['simple_birth_age']])
                        if value['kind']!='powder'and uv_random:
                            states=initialize(particle,seed)
                            value['uv_matrices']=[uv_matrix(uv,sample_time(uv,sample_age),model=value['kind']=='model',random_state=state)
                                for uv,state in zip(uv_nodes(particle),states)]
                            value['uv_random_bytes']=states
                        if not len(value['indices']):continue
                        value.update(source_particle=target,source_emitter=index,source_emitter_type=emitter_type,
                            source_inheritance_mode=inheritance_mode,
                            source_emitter_ancestors=tuple(i for i in stack if type(i)is int),source_age=age,source_birth=birth,
                            draw_order=draw_order,draw_layer=draw_layer,_shader_source=(effect.sha256,particle))
                        if loop_state is not None and birth_parameter_clock is not None:
                            value['source_birth_parameter_clock']=(birth_parameter_clock.wrapped,birth_parameter_clock.unwrapped)
                        if phase_supported:value['ancestor_birth_snapshot']=True
                        if len(result)>=512:raise UnsupportedEffect('粒子预算')
                        result.append(value)
                except UnsupportedEffect as error:pending.append(str(error))
    try:
        root=fields(r for r in effect.source_records if r.tag.startswith('Rv'))
        for name in('RvPx','RvPy','RvPz','RvRx','RvRy','RvRz'):
            if number(root,name,0.,real=True)!=0:raise UnsupportedEffect('全局特效位置或旋转修正')
        for name in('RvSx','RvSy','RvSz','RvR','RvG','RvB'):
            if number(root,name,1.,real=True)!=1:raise UnsupportedEffect('全局特效缩放或颜色修正')
        if timeline_events is not None:
            for event in timeline_events:
                timeline=records(get('TmLn',event.timeline).data)
                top=fields(r for r in timeline if r.tag not in('Item','Clip'))
                row=items(timeline,'Item','bEna',number(top,'TICn'))[event.item]
                try:
                    if number(row,'EfNo',-1)>=0:raise UnsupportedEffect('后处理效果器')
                    if event.binder<0:raise UnsupportedEffect('无附着点的武器时间线父变换尚未接入')
                    bound,auxiliary=binding(get('Bind',event.binder),bones,bind_points,components=True)
                    # Distinct loop/trigger instances retain distinct birth
                    # history, even if they share the same source emitter.
                    emit(number(row,'EmNo',-1),frame-event.start,bound,bound,np.ones(4),
                         stack=(event.identity,),auxiliary=auxiliary)
                except UnsupportedEffect as error:pending.append(str(error))
            return tuple(result),tuple(sorted(set(pending)))
        scheduler=records(get('Schd',0).data);state=fields([r for r in scheduler if r.tag not in('Item','Trgr')])
        scheduled=items(scheduler,'Item','bEna',number(state,'ItCn'))
        selected=(tuple((number(entry,'TlNo',-1),number(entry,'StTm'))for entry in scheduled
                    if number(entry,'bEna',1))if timeline_entries is None else timeline_entries)
        for timeline_index,start in selected:
            timeline_time=frame-start
            if timeline_time<0:continue
            timeline=records(get('TmLn',timeline_index).data)
            top=fields([r for r in timeline if r.tag not in('Item','Clip')])
            if number(top,'CpCn')or number(top,'LpEd'):raise UnsupportedEffect('时间轴片段或循环')
            for item in items(timeline,'Item','bEna',number(top,'TICn')):
                if not number(item,'bEna',1):continue
                time=timeline_time-number(item,'StTm');end=number(item,'EdTm',-1)
                if time<0 or(end>=0 and timeline_time>=end):continue
                try:
                    if number(item,'EfNo',-1)>=0:raise UnsupportedEffect('后处理效果器')
                    bound,auxiliary=binding(get('Bind',number(item,'BdNo',-1)),bones,bind_points,components=True)
                    emit(number(item,'EmNo',-1),time,bound,bound,np.ones(4),auxiliary=auxiliary)
                except UnsupportedEffect as error:pending.append(str(error))
    except UnsupportedEffect as error:pending.append(str(error))
    return tuple(result),tuple(sorted(set(pending)))
