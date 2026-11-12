"""Source AVFX model/disc/quad geometry and material channels at fixed time."""
import numpy as np
import struct
from functools import lru_cache
from collections import OrderedDict
from threading import RLock
from effect_sample import fields,nested,number,curve,axes,color,rotation,transform,stationary,particle_fields,soft_particle_range,UnsupportedEffect
from vfx import records


reuse_templates=True
template_budget=8*1024*1024
_templates=OrderedDict()
_template_bytes=0
_template_lock=RLock()


def _template_copy(value,readonly=False):
    if isinstance(value,np.ndarray):
        result=value.copy()
        if readonly:result.flags.writeable=False
        return result
    if isinstance(value,dict):return {k:_template_copy(v,readonly)for k,v in value.items()}
    if isinstance(value,list):return [_template_copy(v,readonly)for v in value]
    if isinstance(value,tuple):return tuple(_template_copy(v,readonly)for v in value)
    return value


def _template_size(value):
    if isinstance(value,np.ndarray):return value.nbytes
    if isinstance(value,dict):return sum(_template_size(v)for v in value.values())
    if isinstance(value,(list,tuple)):return sum(_template_size(v)for v in value)
    return 0


def template_sample(effect,node,time,*,defer_transform=False,defer_uv=False,simple_updates=None,texture_random=None,ordinary_motion=False):
    """Bounded immutable source prototypes, before attachment/birth overrides.

    This never caches resolved parents, camera corners or a finished frame.
    A content SHA identifies the source; every caller owns its mutable arrays.
    """
    global _template_bytes
    if simple_updates is not None or texture_random is not None:
        return sample(effect,node,time,np.eye(4),np.ones(4),simple_updates=simple_updates,texture_random=texture_random,ordinary_motion=ordinary_motion)
    if not reuse_templates or template_budget<=0:return sample(effect,node,time,np.eye(4),np.ones(4),ordinary_motion=ordinary_motion)
    data=particle_fields(node);kind=number(data,'PrVT')
    # Simple and disc geometry have their own time-dependent generators.
    if kind not in(1,5,8,13)or number(data,'bSCt'):return sample(effect,node,time,np.eye(4),np.ones(4),ordinary_motion=ordinary_motion)
    key=(effect.sha256,node,ordinary_motion)
    with _template_lock:
        found=_templates.get(key)
        if found is not None:
            _templates.move_to_end(key)
            source=found[0]
        else:
            try:source=_template_copy(sample(effect,node,0,np.eye(4),np.ones(4),ordinary_motion=ordinary_motion),True)
            except UnsupportedEffect:
                # A curve can be unsupported only at t=0. Keep the original
                # per-time validation instead of rejecting its later values.
                return sample(effect,node,time,np.eye(4),np.ones(4),ordinary_motion=ordinary_motion)
            size=_template_size(source)+len(node.data)
            if size<=template_budget:
                _templates[key]=(source,size);_template_bytes+=size
                while len(_templates)>256 or _template_bytes>template_budget:
                    _,(_,old)=_templates.popitem(last=False);_template_bytes-=old
    if not ordinary_motion:stationary(data,time)
    from effect_uv import uv_nodes
    textures={}
    for name,channel in source['textures'].items():
        fields_=nested(data.get(name))
        if name=='TC1':
            from effect_texture import texture_index
            index=texture_index(effect,fields_,time)
            path=None if index is None else effect.textures[index]
        else:
            if curve(fields_.get('TxN'),time):raise UnsupportedEffect('动画贴图索引')
            path=channel['path']
        textures[name]={**channel,'path':path,'power':curve(fields_.get({'TD':'DPow','TN':'NPow','TP':'POff'}.get(name,'DPow')),time)}
    local_scale=axes(data.get('Scl'),time,1);angles=axes(data.get('Rot'),time)
    value={**source,'parent':np.eye(4),'local':source['local']if defer_transform else transform(data,time),'local_scale':local_scale,
        'local_rotation':source['local_rotation']if defer_transform else rotation(angles,number(data,'RoOT')),'powder_angles':angles,
        'powder_scale':np.ones(3)*local_scale,'color':color(data.get('Col'),time)*np.ones(4),
        'textures':textures,'texture':textures.get('TC1',{}).get('path'),
        'uv_matrices':source['uv_matrices']if defer_uv and kind!=1 else[
            uv_matrix(None,0)if kind==1 else uv_matrix(uv,time,model=kind in(5,13))for uv in uv_nodes(node)],
        'uv_compute':list(source['uv_compute'])}
    if kind==5:
        model=nested(data.get('Data'))
        if model_index(model,time)!=source['model_index']:value.update(model_geometry(effect,data,time))
        value.update(fresnel_power=curve(model.get('FrC'),time,1),
            fresnel_begin=color(model.get('ColB'),time),fresnel_end=color(model.get('ColE'),time),
            fresnel_angles=fresnel_angles(model,time))
    return value


def source_model_geometry(effect,index):
    models=[r for r in effect.source_records if r.tag=='Modl']
    if not 0<=index<len(models):raise ValueError('Source AVFX model index exceeds group')
    source=fields(records(models[index].data));raw=source.get('VDrw');triangles=source.get('VIdx')
    if raw is None or triangles is None or len(raw.data)%36 or len(triangles.data)%6:raise ValueError('Invalid AVFX source model buffers')
    dtype=np.dtype([('position','<f2',4),('normal','u1',4),('tangent','u1',4),('color','u1',4),('uv','<f2',8)])
    vertices=np.frombuffer(raw.data,dtype=dtype);indices=np.frombuffer(triangles.data,dtype='<u2').astype('<u4')
    if not len(vertices)or len(vertices)>65535 or np.any(indices>=len(vertices)):raise ValueError('AVFX source model indices invalid')
    if not np.isfinite(vertices['position']).all()or not np.isfinite(vertices['uv']).all():raise ValueError('Non-finite AVFX model source')
    if np.any(vertices['position'][:,3]!=1):raise UnsupportedEffect('非单位模型齐次坐标')
    return {'positions':vertices['position'][:,:3].astype(float),'normals':vertices['normal'][:,:3].astype(float)/255,
        'tangents':vertices['tangent'][:,:3].astype(float)/255,'colors':vertices['color'].astype(float)/255,
        'base_uv':vertices['uv'].reshape(-1,4,2).astype(float),'indices':indices,'model_number':index}


def fresnel_angles(model,time):
    if number(model,'FrsT')not in(2,3):return np.zeros(3,dtype='<f4')
    node=model.get('FrRt');source=nested(node)
    if any(source.get(axis+'R')is not None for axis in'XYZ'):
        raise UnsupportedEffect('模型菲涅尔随机方向尚未接入')
    return axes(node,time).astype('<f4')


def model_geometry(effect,data,time):
    model=nested(data.get('Data'));index=model_index(model,time)
    if number(model,'FrsT')not in(0,1,2,3):raise UnsupportedEffect('模型菲涅尔轴类型 '+str(number(model,'FrsT')))
    if number(model,'bShp'):raise UnsupportedEffect('模型变形尚未接入')
    return {**source_model_geometry(effect,index),'model_index':index,
        'fresnel_type':number(model,'FrsT'),'fresnel_power':curve(model.get('FrC'),time,1),
        'fresnel_angles':fresnel_angles(model,time),
        'fresnel_begin':color(model.get('ColB'),time),'fresnel_end':color(model.get('ColE'),time)}


def model_index(model,time):
    selection=model.get('MdNo')
    if selection is None or not 1<=len(selection.data)<=8:raise UnsupportedEffect('模型选择列表长度尚未核实')
    if number(model,'MNRv')or number(model,'MNRi')>0:
        raise UnsupportedEffect('模型逐次随机选择历史尚未接入')
    # CN3ef799..3ef7bc: curve -> CVTTSS2SI, zero constructor offset,
    # unsigned abs(int32) remainder, then SIGNED source model-list byte.
    value=np.float32(curve(model.get('NoAn'),time))
    if not np.isfinite(value)or not -2**31<=value<2**31:raise UnsupportedEffect('模型选择曲线超出已核实整数范围')
    index=selection.data[abs(int(np.trunc(value)))%len(selection.data)]
    if index>=128:raise UnsupportedEffect('源模型列表空模型尚未接入')
    return index


def light_model_geometry(effect,data):
    # CN3ec500 reads its own signed MNO, not Model's random MdNo list.
    # CN3ec800 selects Apricot family4 and three constant ONE vectors;
    # it does not evaluate ordinary Model's FrC/ColB/ColE curves.
    model=nested(data.get('Data'))
    if set(model)!={'MNO'}:raise UnsupportedEffect('轻量模型数据布局尚未核实')
    index=number(model,'MNO',-1)
    if not 0<=index<128:raise ValueError('LightModel signed model index invalid')
    return {**source_model_geometry(effect,index),'shader_family':'lightmodel','fresnel_type':0}


def disc_geometry(data,time):
    from effect_disc import geometry,source_parameters
    return geometry(source_parameters(data,time))


def windmill_geometry(data):
    # CN4004d0 splits the ordinary Powder extent into four quads. Each
    # quadrant keeps its own source UV winding; it is not a rotating Quad.
    source=nested(data.get('Data'));mode=number(source,'WUvT')
    if set(source)!={'WUvT'}or mode not in(0,1):
        raise UnsupportedEffect('Windmill UV布局尚未核实')
    positions=np.array(((-.5,.5,0),(0,.5,0),(-.5,0,0),(0,0,0),
        (0,.5,0),(.5,.5,0),(0,0,0),(.5,0,0),
        (-.5,0,0),(0,0,0),(-.5,-.5,0),(0,-.5,0),
        (0,0,0),(.5,0,0),(0,-.5,0),(.5,-.5,0)),dtype='<f4')
    uv=np.array(((0,0),(1,0),(0,1),(1,1),
        *(((1,0),(0,0),(1,1),(0,1))if mode else((0,1),(0,0),(1,1),(1,0))),
        *(((0,1),(1,1),(0,0),(1,0))if mode else((1,0),(1,1),(0,0),(0,1))),
        (1,1),(0,1),(1,0),(0,0)),dtype='<f4')
    return {'positions':positions,'colors':np.ones((16,4)),
        'base_uv':np.tile(uv[:,None,:],(1,4,1)),
        'indices':np.concatenate([np.array((3,1,0,0,2,3),dtype='<u4')+i*4 for i in range(4)]),
        'windmill':True}


def shape_geometry(frame):
    # Original Shape UV writer3e4870 emits TL,TR,BL,BR. Its vertex
    # writer3fe7e0 pairs those with TOP-first coordinates, unlike Model
    # source buffers or Powder's independent camera/atlas corner layout.
    positions=np.array(((-.5,.5,0),(.5,.5,0),(-.5,-.5,0),(.5,-.5,0)))
    if frame==0:positions=positions[:,(2,0,1)]
    elif frame==1:
        positions=positions[:,(0,2,1)];positions[:,2]*=-1
    return {'positions':positions,'colors':np.ones((4,4)),
        'base_uv':np.tile(np.array(((0,0),(1,0),(0,1),(1,1)),dtype='<f4')[:,None,:],(1,4,1)),
        'indices':np.asarray((3,1,0,0,2,3),dtype='<u4')}


@lru_cache(maxsize=8192)
def uv_matrix(node,time,*,model=False,random_state=None):
    d=nested(node)
    if number(d,'CUvT')not in(0,1):raise UnsupportedEffect('UV计算类型')
    if random_state is None:
        from effect_uv import scale_default
        scale=axes(d.get('Scl'),time,scale_default(d.get('Scl')),2);offset=axes(d.get('Scr'),time,0,2);angle=curve(d.get('Rot'),time)
    else:
        from effect_uv import values
        scale,offset,angle=values(node,time,random_state)
    matrix,center=uv_linear(model,scale.dtype.str,scale.tobytes(),struct.pack('<d',float(angle)))
    # Model writes the COLUMNS of its temporary 3x3 into the two PS rows
    # (current CN3e4dcb..3e4e43), after the original matrix multiply.
    # Shape instead emits transformed corner UVs; its centering differs.
    # Scroll is added AFTER rotation, never rotated with the coordinates.
    if model:
        translation=offset+.5
    else:
        translation=offset+.5-center
    result=np.empty((2,4));result[:,:2]=matrix;result[:,2]=0;result[:,3]=translation;result.flags.writeable=False
    return result


@lru_cache(maxsize=4096)
def uv_linear(model,dtype,raw,angle):
    scale=np.frombuffer(raw,dtype=dtype);angle=struct.unpack('<d',angle)[0];c,s=np.cos(angle),np.sin(angle)
    # Model's exported transpose gives the same linear operation as Shape;
    # only the source UV convention/translation above differs. Do not send
    # the temporary rotation-times-scale matrix directly to the shader.
    matrix=np.diag(scale)@np.array(((c,-s),(s,c)))
    center=matrix@np.full(2,.5)
    matrix.flags.writeable=False;center.flags.writeable=False
    return matrix,center


def texture_channels(kind):
    # Original capability dispatch and the actual source-parser pointer map.
    # Authored but inactive layer records must not enable extra shader keys.
    if kind in(1,2):return ('TC1',)
    # Current CN3a7451: TC4 +1f0, TN +1f8, TR +200, TD +208, TP +210.
    # Cap2 enables TC4/TP; cap3 additionally enables TN/TR. The historical
    # audit mislabeled these pointers and inferred an incorrect capability.
    if kind in(8,12,13):return ('TC1','TC2','TC3','TC4','TD','TP')
    if kind==5:return ('TC1','TC2','TC3','TC4','TD','TN','TR','TP')
    raise UnsupportedEffect('粒子纹理类型 '+str(kind))


def sample(effect,node,time,parent,parent_color,*,simple_updates=None,texture_random=None,ordinary_motion=False):
    d=particle_fields(node);kind=number(d,'PrVT')
    if not ordinary_motion:stationary(d,time)
    if kind not in(1,2,5,8,12,13):raise UnsupportedEffect('粒子类型 '+str(kind))
    simple=bool(number(d,'bSCt'))
    if simple and kind!=1:raise UnsupportedEffect('非粉尘的简化动画')
    if any(number(d,n)for n in('bNea','bFar')):raise UnsupportedEffect('粒子距离裁剪')
    if kind==1 and number(nested(d.get('Data')),'bLgt'):raise UnsupportedEffect('粉尘光照')
    channels=texture_channels(kind)
    for name in(n for n in channels if n=='TR' or n=='TN' and kind!=5):
        if number(nested(d.get(name)),'bEna'):raise UnsupportedEffect('法线、反射或调色板特效材质')
    if simple:
        from effect_simple import geometry as simple_geometry
        if number(d,'RBDT')!=10:raise UnsupportedEffect('Simple 粒子外层朝向')
        from effect_curve import ClockTimes
        geometry=simple_geometry(effect,d.get('Smpl'),time.unwrapped if isinstance(time,ClockTimes)else time,updates=simple_updates)
    elif kind==1:
        # CN3fd53b..3fd6a9: Powder has its own top-left-first packet,
        # independent of Shape's bottom-left-first quad and UV convention.
        geometry={'positions':np.array(((-.5,.5,0),(.5,.5,0),(-.5,-.5,0),(.5,-.5,0))),
            'colors':np.ones((4,4)),'base_uv':np.tile(np.array(((0,0),(1,0),(0,1),(1,1)),dtype='<f4')[:,None,:],(1,4,1)),
            'indices':np.asarray((3,1,0,0,2,3),dtype='<u4')}
    elif kind==2:geometry=windmill_geometry(d)
    elif kind==5:geometry=model_geometry(effect,d,time)
    elif kind==13:geometry=light_model_geometry(effect,d)
    elif kind==12:geometry=disc_geometry(d,time)
    elif kind==8:geometry=shape_geometry(number(d,'RBDT'))
    else:raise UnsupportedEffect('粒子类型 '+str(kind))
    frame=number(d,'RBDT')
    if frame not in(0,1,2,4,5,6,9,10):raise UnsupportedEffect('粒子朝向类型 '+str(frame))
    if kind==1 and frame==0:geometry['positions']=geometry['positions'][:,(2,0,1)]
    elif kind==1 and frame==1:geometry['positions']=geometry['positions'][:,(0,2,1)]
    uv_nodes=[r for r in records(node.data)if r.tag=='UvSt'];uv_count=number(d,'UvSN',len(uv_nodes))
    if not 0<=uv_count<=4 or uv_count!=len(uv_nodes):raise UnsupportedEffect('UV通道数量')
    uv_compute=[number(nested(uv),'CUvT')for uv in uv_nodes]
    if any(mode not in(0,1)for mode in uv_compute):raise UnsupportedEffect('未接入的UV计算方式')
    if any(uv_compute)and kind in(1,2,13):raise UnsupportedEffect('模型或粉尘屏幕坐标UV')
    # Powder writes fixed quad UV (3fd53b) or Simple atlas UV (3faa60)
    # directly. Its own VS/PS family has no generic UvTransform constant;
    # the Shape/Model UV updater must not transform these a second time.
    matrices=[uv_matrix(None,0)if kind in(1,2)else uv_matrix(uv,time,model=kind in(5,13))for uv in uv_nodes];textures={}
    for name in('TC1','TC2','TC3','TC4','TD','TN','TP'):
        if name not in channels:continue
        channel=nested(d.get(name))
        if not number(channel,'bEna'):continue
        if any(number(channel,n)for n in('bPFC','bUOS')):raise UnsupportedEffect('肖像或离屏特殊贴图')
        screen_copy=bool(number(channel,'bUSC'))
        if screen_copy and name=='TD':raise UnsupportedEffect('扭曲层画面复制')
        if name=='TC1':
            from effect_texture import texture_index
            index=texture_index(effect,channel,time,0 if texture_random is None else texture_random)
        elif name in('TC2','TC3','TC4'):
            from effect_texture import static_color_index
            index=static_color_index(effect,channel,time)
        else:
            if curve(channel.get('TxN'),time):raise UnsupportedEffect('动画贴图索引')
            index=number(channel,'TxNo',-1)
            if index<0:
                choice=channel.get('TLst')
                if choice is None:raise UnsupportedEffect('附加层空纹理绑定尚未接入')
                if len(choice.data)!=1:raise UnsupportedEffect('附加层纹理列表尚未接入')
                index=choice.data[0]
        if index is not None and not 0<=index<len(effect.textures):raise ValueError('Source effect texture index differs')
        uv=number(channel,'UvSN')
        # Model VDrw always carries four source float2 UVs. UvSN counts
        # animated transforms, not available source coordinates. The native
        # PS can sample UV3 directly when only UV0..2 are transformed.
        available_uvs=geometry['base_uv'].shape[1]if kind in(5,13)else uv_count
        if name!='TP'and not 0<=uv<available_uvs:raise ValueError('Source effect UV index differs')
        textures[name]={'path':None if index is None else effect.textures[index],'uv':uv,'color_to_alpha':bool(number(channel,'bC2A')),'screen_copy':screen_copy,
            'filter':number(channel,'TFT',1),'address':(number(channel,'TBUT'),number(channel,'TBVT')),
            'color_mode':number(channel,'TCCT',0),'alpha_mode':number(channel,'TCAT',0),
            'power':curve(channel.get({'TD':'DPow','TN':'NPow','TP':'POff'}.get(name,'DPow')),time),'distort_uv':[bool(number(channel,'bT'+str(i+1)))for i in range(4)]}
        if name=='TP':textures[name]['address']=(number(channel,'TBT'),)*2
        if name!='TD'and(number(channel,'TCCT')not in range(6)or number(channel,'TCAT')not in range(4)):
            raise UnsupportedEffect('贴图混合计算类型')
        if name=='TC1'and(number(channel,'TCCT')not in(0,1)or number(channel,'TCAT')not in(0,1)):
            raise UnsupportedEffect('首层贴图计算类型')
        # All four TD switches are original shader keys. Shape's packed
        # vertex has one shared distortion-enable byte, not four UV flags.
    mode=number(d,'RMT')
    if mode not in range(13):raise UnsupportedEffect('混合模式 '+str(mode))
    return {**geometry,'kind':'powder'if kind in(1,2)else'model'if kind in(5,13)else'disc'if kind==12 else'quad','frame':frame,'local':transform(d,time),
        'parent':parent,'local_scale':axes(d.get('Scl'),time,1),
        'local_rotation':rotation(axes(d.get('Rot'),time),number(d,'RoOT')),
        'powder_angles':axes(d.get('Rot'),time),
        'powder_scale':np.linalg.norm(parent[:3,:3],axis=0)*axes(d.get('Scl'),time,1),
        'coordinate_order':number(d,'CCOT'),'color':color(d.get('Col'),time)*parent_color,'textures':textures,'uv_matrices':matrices,
        'uv_count':uv_count,'uv_compute':uv_compute,'blend':mode,'apply_tone_map':bool(number(d,'bATM')),
        'alpha_channel':not bool(number(nested(d.get('TC1')),'bUSC')),'soft':bool(number(d,'DsSp')),
        'soft_range':soft_particle_range(effect)if number(d,'DsSp')else None,'depth_test':bool(number(d,'DsDt',1)),
        'depth_write':bool(number(d,'DsDw')),'cull':number(d,'CulT'),'depth_offset':number(d,'DpOf',0.,real=True),
        'priority':number(d,'DwPr'),'texture':textures.get('TC1',{}).get('path'),
        'filter':textures.get('TC1',{}).get('filter',1),'address':textures.get('TC1',{}).get('address',(1,1))}
