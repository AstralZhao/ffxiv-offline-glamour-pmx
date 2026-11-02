"""CN Disc topology/edge interpolation, shared by all source equipment."""
import math

import numpy as np

from effect_sample import nested,number,curve,color,UnsupportedEffect


def geometry_values(*,u=5,v=25,parts=1,part=0,angle=2*math.pi,radius=(1,1),width=(1,1),
        inner=(0,0),outer=(0,0),interval=0,scaling=1,world_scale=(1,1,1),
        inner_color=(1,1,1,1),outer_color=(1,1,1,1)):
    """One part, source point counts. Native 3e9f00/3eace0, not fitted geometry."""
    if not(2<=u<=16 and 3<=v<=128 and u*v<=1024 and 1<=parts<=16 and 0<=part<parts):
        raise UnsupportedEffect('圆盘顶点数量或重复结构')
    f=np.float32
    q=np.arange(u,dtype='<f4')/f(u-1);t=np.arange(v,dtype='<f4')/f(v-1)
    lerp=lambda pair: (f(pair[1])-f(pair[0]))*t+f(pair[0])
    r=lerp(radius);w=lerp(width)
    if scaling<1:
        average=f(f(world_scale[0])+f(world_scale[2]))*f(.5)
        if not np.isfinite(average)or average<=1e-8:raise UnsupportedEffect('圆盘宽度尺度退化')
        unscaled=w/average;w=(w-unscaled)*f(scaling)+unscaled
    # Radius is the centre line; Width extends on BOTH sides. PIFU remaps
    # radial positions using q+(q^4-q)*factor while UV/color stay linear.
    radial=(q*q*q*q-q)*f(interval)+q
    # The native compensated-width branch also reverses the radial edge
    # order (3ea200/3ea211 versus 3ea7c6/3ea7d1). Preserve that distinction.
    radii=((r+w)[:,None]-(w*f(2))[:,None]*radial[None,:]if scaling<1 else
        (r-w)[:,None]+(w*f(2))[:,None]*radial[None,:])
    heights=lerp(inner)[:,None]+(lerp(outer)-lerp(inner))[:,None]*radial[None,:]
    theta=t*f(angle)+f(f(part)*f(2*math.pi)/f(parts))
    c=np.asarray([math.cos(float(x))for x in theta],dtype='<f4')
    s=np.asarray([math.sin(float(x))for x in theta],dtype='<f4')
    positions=np.stack((c[:,None]*radii,heights,-s[:,None]*radii),axis=-1).reshape(-1,3)
    a=np.asarray(inner_color,dtype='<f4');b=np.asarray(outer_color,dtype='<f4')
    colors=np.tile((b-a)*q[:,None]+a,(v,1))
    indices=(np.arange(v-1)[:,None]*u+np.arange(u-1)[None,:]).ravel()
    indices=np.column_stack((indices,indices+1,indices+u,indices+u,indices+1,indices+u+1)).ravel().astype('<u4')
    uv=np.stack(np.broadcast_arrays(q[None,:],t[:,None]),axis=-1).reshape(-1,2)
    return {'positions':positions,'colors':colors,'indices':indices,'base_uv':np.tile(uv[:,None,:],(1,4,1))}


def source_parameters(data,time):
    d=nested(data.get('Data'))
    for key in('AngR','HBIR','HEIR','HBOR','HEOR','WBR','WER','RBR','RER'):
        if curve(d.get(key),time):raise UnsupportedEffect('圆盘随机曲线 '+key)
    return dict(u=number(d,'PCnU',5),v=number(d,'PCnV',25),parts=number(d,'PrtC',1),
        angle=curve(d.get('Ang'),time,2*math.pi),radius=tuple(curve(d.get(k),time,1)for k in('RB','RE')),
        width=tuple(curve(d.get(k),time,1)for k in('WB','WE')),
        inner=tuple(curve(d.get(k),time)for k in('HBI','HEI')),
        outer=tuple(curve(d.get(k),time)for k in('HBO','HEO')),
        interval=number(d,'PIFU',0.,real=True),scaling=number(d,'SS',100)/100,
        inner_color=color(d.get('CEI'),time),outer_color=color(d.get('CEO'),time))


def geometry(parameters,world_scale=(1,1,1)):
    pieces=[geometry_values(**parameters,part=i,world_scale=world_scale)for i in range(parameters['parts'])]
    if not pieces:raise UnsupportedEffect('空圆盘重复结构')
    result={k:np.concatenate([piece[k]for piece in pieces])for k in('positions','colors','base_uv')}
    count=parameters['u']*parameters['v']
    result['indices']=np.concatenate([piece['indices']+i*count for i,piece in enumerate(pieces)])
    return {**result,'disc_parameters':parameters}
