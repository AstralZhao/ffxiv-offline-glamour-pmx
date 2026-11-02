"""Source AVFX model/disc/quad geometry and material channels at fixed time."""
import numpy as np
from effect_sample import fields,nested,number,curve,axes,color,transform,stationary,particle_fields,UnsupportedEffect
from vfx import records


def model_geometry(effect,data,time):
    model=nested(data.get('Data'));selection=model.get('MdNo')
    if selection is None or len(selection.data)!=1:raise UnsupportedEffect('模型随机选择列表')
    index=selection.data[0];models=[r for r in effect.source_records if r.tag=='Modl']
    if not 0<=index<len(models):raise ValueError('Source AVFX model index exceeds group')
    source=fields(records(models[index].data));raw=source.get('VDrw');triangles=source.get('VIdx')
    if raw is None or triangles is None or len(raw.data)%36 or len(triangles.data)%6:raise ValueError('Invalid AVFX source model buffers')
    dtype=np.dtype([('position','<f2',4),('normal','u1',4),('tangent','u1',4),('color','u1',4),('uv','<f2',8)])
    vertices=np.frombuffer(raw.data,dtype=dtype);indices=np.frombuffer(triangles.data,dtype='<u2').astype('<u4')
    if not len(vertices)or len(vertices)>65535 or np.any(indices>=len(vertices)):raise ValueError('AVFX source model indices invalid')
    if not np.isfinite(vertices['position']).all()or not np.isfinite(vertices['uv']).all():raise ValueError('Non-finite AVFX model source')
    if np.any(vertices['position'][:,3]!=1):raise UnsupportedEffect('非单位模型齐次坐标')
    if number(model,'FrsT')not in(0,1):raise UnsupportedEffect('模型菲涅尔轴类型 '+str(number(model,'FrsT')))
    if number(model,'bShp')or number(model,'MNRv')or curve(model.get('NoAn'),0):raise UnsupportedEffect('模型变形或动画选择')
    return {'positions':vertices['position'][:,:3].astype(float),'normals':vertices['normal'][:,:3].astype(float)/255,
        'tangents':vertices['tangent'][:,:3].astype(float)/255,'colors':vertices['color'].astype(float)/255,
        'base_uv':vertices['uv'].reshape(-1,4,2).astype(float),'indices':indices,'model_number':index,
        'fresnel_type':number(model,'FrsT'),'fresnel_power':curve(model.get('FrC'),time,1),
        'fresnel_begin':color(model.get('ColB'),time),'fresnel_end':color(model.get('ColE'),time)}


def disc_geometry(data,time):
    d=nested(data.get('Data'));u=number(d,'PCnU',1);v=number(d,'PCnV',24)
    if not 2<=u<=128 or not 3<=v<=256 or number(d,'PrtC',1)!=1:raise UnsupportedEffect('圆盘分段或重复结构')
    angle=curve(d.get('Ang'),time,2*np.pi)
    # Evaluate authored curves once per update, not once per generated vertex.
    radial,t=np.meshgrid(np.linspace(0,1,u+1),np.linspace(0,1,v+1),indexing='ij')
    radius=curve(d.get('RB'),time,1)*(1-t)+curve(d.get('RE'),time,1)*t
    width=curve(d.get('WB'),time,1)*(1-t)+curve(d.get('WE'),time,1)*t
    r=radius-width+width*radial
    height=(curve(d.get('HBI'),time)*(1-t)+curve(d.get('HEI'),time)*t)*(1-radial)+(curve(d.get('HBO'),time)*(1-t)+curve(d.get('HEO'),time)*t)*radial
    positions=np.stack((r*np.cos(t*angle),height,r*np.sin(t*angle)),axis=-1).reshape(-1,3)
    colors=(color(d.get('CEI'),time)*(1-radial[...,None])+color(d.get('CEO'),time)*radial[...,None]).reshape(-1,4)
    a=(np.arange(u)[:,None]*(v+1)+np.arange(v)[None,:]).ravel();b=a+v+1
    indices=np.column_stack((a,b,a+1,a+1,b,b+1)).ravel().astype('<u4')
    uv=np.stack((t,radial),axis=-1).reshape(-1,2)
    return {'positions':positions,'colors':colors,'indices':indices,'base_uv':np.tile(uv[:,None,:],(1,4,1))}


def uv_matrix(node,time):
    d=nested(node)
    if number(d,'CUvT'):raise UnsupportedEffect('屏幕坐标UV')
    scale=axes(d.get('Scl'),time,1,2);offset=axes(d.get('Scr'),time,0,2);angle=curve(d.get('Rot'),time)
    c,s=np.cos(angle),np.sin(angle);rot=np.array(((c,-s),(s,c)))
    matrix=rot@np.diag(scale);translation=rot@(offset-.5)+.5
    return np.column_stack((matrix,np.zeros(2),translation))


def sample(effect,node,time,parent,parent_color):
    d=particle_fields(node);kind=number(d,'PrVT');stationary(d,time)
    if kind not in(5,8,12):raise UnsupportedEffect('粒子类型 '+str(kind))
    if number(d,'bSCt')or any(number(d,n)for n in('bNea','bFar')):raise UnsupportedEffect('粒子简化动画或距离裁剪')
    for name in('TN','TR','TP'):
        if number(nested(d.get(name)),'bEna'):raise UnsupportedEffect('法线、反射或调色板特效材质')
    if kind==5:geometry=model_geometry(effect,d,time)
    elif kind==12:geometry=disc_geometry(d,time)
    else:
        geometry={'positions':np.array(((-.5,-.5,0),(.5,-.5,0),(.5,.5,0),(-.5,.5,0))),
            'colors':np.ones((4,4)),'base_uv':np.tile(np.array(((0,0),(1,0),(1,1),(0,1)))[:,None,:],(1,4,1)),
            'indices':np.asarray((0,1,2,0,2,3),dtype='<u4')}
    frame=number(d,'RBDT')
    if frame not in(0,1,2,4,5,6):raise UnsupportedEffect('粒子朝向类型 '+str(frame))
    if kind==8 and frame==0:geometry['positions']=geometry['positions'][:,(2,0,1)]
    elif kind==8 and frame==1:geometry['positions']=geometry['positions'][:,(0,2,1)]
    uv_nodes=[r for r in records(node.data)if r.tag=='UvSt'];uv_count=number(d,'UvSN',len(uv_nodes))
    if not 0<=uv_count<=4 or uv_count!=len(uv_nodes):raise UnsupportedEffect('UV通道数量')
    matrices=[uv_matrix(uv,time)for uv in uv_nodes];textures={}
    for name in('TC1','TC2','TC3','TC4','TD'):
        channel=nested(d.get(name))
        if not number(channel,'bEna'):continue
        if any(number(channel,n)for n in('bUSC','bPFC','bUOS')):raise UnsupportedEffect('画面或肖像贴图')
        if curve(channel.get('TxN'),time):raise UnsupportedEffect('动画贴图索引')
        index=number(channel,'TxNo',-1)
        if index<0:
            choice=channel.get('TLst')
            if choice is None or len(choice.data)!=1:raise UnsupportedEffect('多贴图随机选择')
            index=choice.data[0]
        if not 0<=index<len(effect.textures):raise ValueError('Source effect texture index differs')
        uv=number(channel,'UvSN')
        if not 0<=uv<uv_count:raise ValueError('Source effect UV index differs')
        textures[name]={'path':effect.textures[index],'uv':uv,'color_to_alpha':bool(number(channel,'bC2A')),
            'filter':number(channel,'TFT',1),'address':(number(channel,'TBUT'),number(channel,'TBVT')),
            'color_mode':number(channel,'TCCT',0),'alpha_mode':number(channel,'TCAT',0),
            'power':curve(channel.get('DPow'),time),'distort_uv':[bool(number(channel,'bT'+str(i+1)))for i in range(4)]}
        if name!='TD'and(number(channel,'TCCT')not in range(5)or number(channel,'TCAT')not in range(4)):
            raise UnsupportedEffect('贴图混合计算类型')
        if name=='TC1'and(number(channel,'TCCT')not in(0,1)or number(channel,'TCAT')not in(0,1)):
            raise UnsupportedEffect('首层贴图计算类型')
        if name=='TD'and number(channel,'bT4')and kind!=5:raise UnsupportedEffect('平面粒子第四UV扭曲')
    mode=number(d,'RMT')
    if mode not in(0,1,2,3,4,8):raise UnsupportedEffect('混合模式 '+str(mode))
    return {**geometry,'kind':'model'if kind==5 else'disc'if kind==12 else'quad','frame':frame,'local':transform(d,time),
        'parent':parent,'color':color(d.get('Col'),time)*parent_color,'textures':textures,'uv_matrices':matrices,
        'uv_count':uv_count,'blend':mode,'soft':bool(number(d,'DsSp')),'depth_test':bool(number(d,'DsDt',1)),
        'depth_write':bool(number(d,'DsDw')),'cull':number(d,'CulT'),'depth_offset':number(d,'DpOf',0.,real=True),
        'priority':number(d,'DwPr'),'texture':textures.get('TC1',{}).get('path'),
        'filter':textures.get('TC1',{}).get('filter',1),'address':textures.get('TC1',{}).get('address',(1,1))}
