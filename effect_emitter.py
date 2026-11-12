"""Original AVFX emission vertices and normals for model emitters."""
import math
from functools import lru_cache

import numpy as np

from effect_sample import nested,number,curve,curve_source,rotation,UnsupportedEffect
from effect_simple import emission_points,SourceRandom


def motion_step(travel,velocity,delta,resistance):
    """CN3e72e4..3e7335 then3e7296..3e72d2, explicit delta/ARs.

    ARs scales this step's displacement; it does not decay the initial
    velocity or multiply the accumulated position a second time.
    """
    f=np.float32
    increment=f(np.asarray(velocity,dtype='<f4')*f(delta))
    increment=f(increment*f(resistance))
    return f(np.asarray(travel,dtype='<f4')+increment)


def constant_particle_travel(data,age,velocity):
    """Constant source ARs only; variable update history is explicit pending.

    A direct frozen sample supplies age as one explicit elapsed interval.
    This fixes the omitted coefficient, not native multi-step float history.
    """
    if not np.any(velocity):return np.zeros(3,dtype='<f4')
    node=data.get('ARs');_,keys=curve_source(node)
    if keys and len(keys)>1:return None
    return motion_step(np.zeros(3,dtype='<f4'),velocity,age,curve(node,age,1))


def model_injection(effect,data,time,ordinal,identity):
    return model_birth(effect,data.get('Data'),time,ordinal,identity)


def model_normal_point(effect,data,ordinal,parent):
    """Fixed ordered normal Model leaf, original3dc62a..3dc998.

    Zero source speed/rotation, one active child link; saved VNum index is
    used again by mode1 ICbP. Other Model generator branches stay guarded.
    """
    from effect_velocity_rotation import linear
    source=nested(data.get('Data'))
    if number(source,'GeMT')!=3:raise UnsupportedEffect('模型顺序法向取点类型尚未接入')
    positions,normals=emission_points(effect,number(source,'MdNo',-1));index=ordinal%len(positions)
    parent=np.asarray(parent,dtype='<f4');position=np.float32(linear(parent[:3,:3],positions[index])+parent[:3,3])
    direction=linear(parent[:3,:3],normals[index]);f=np.float32
    length=f(np.sqrt(f(f(direction[1]*direction[1]+direction[0]*direction[0])+direction[2]*direction[2])))
    if not np.isfinite(length)or length<=0:raise UnsupportedEffect('模型发射零法向')
    return position,np.zeros(3,dtype='<f4'),f(direction*f(f(1)/length))


@lru_cache(maxsize=4096)
def model_birth(effect,node,time,ordinal,identity):
    source=nested(node);method=number(source,'GeMT')
    if method not in(0,1,2,3,4,5,6,7):raise UnsupportedEffect('模型发射方式 '+str(method))
    positions,normals=emission_points(effect,number(source,'MdNo',-1))
    # CN3db820 maps 0/4 and 1/5 to radial directions, 2/6 and 3/7
    # to the source normal. The paired methods use random / ordered VNum.
    if method%2:
        index=ordinal%len(positions)
    else:
        random=SourceRandom((effect.sha256+repr(identity)).encode());index=random.integer(len(positions))
    angles=np.array([curve(source.get('An'+a),time)for a in'XYZ'])
    if any(curve(source.get('An'+a+'R'),time)for a in'XYZ')or curve(source.get('IjSR'),time):
        raise UnsupportedEffect('模型发射随机角度或速度')
    rotate=rotation(angles,number(source,'ROT'));position=rotate@positions[index]
    direction=position.copy()if method%4<2 else rotate@normals[index]
    length=np.linalg.norm(direction)
    if not math.isfinite(length)or length<1e-8:raise UnsupportedEffect('模型发射零方向')
    direction/=length;speed=curve(source.get('IjS'),time)
    velocity=direction*speed;position.flags.writeable=False;velocity.flags.writeable=False
    return position,velocity


def sphere_injection(effect,data,time,ordinal,identity):
    return sphere_birth(effect,data.get('Data'),time,ordinal,identity)


def cylinder_grid(u,v,index,length,radius):
    """CN3d9d1e grid before source angle/world transforms (normal methods)."""
    f=np.float32
    if not 1<=u<=127 or not 0<=v<=127 or not 0<=index<u*(v+1):
        raise UnsupportedEffect('圆柱发射分段或序号尚未接入')
    length=f(length);radius=f(radius)
    if not np.isfinite((length,radius)).all()or radius<=0:
        raise UnsupportedEffect('圆柱发射非正半径尚未接入')
    phase=f(f(f(index%u)*f(2*math.pi))/f(u))
    sine=f(math.sin(float(phase)));cosine=f(math.cos(float(phase)))
    height=f(0)if v==0 else f(f(f(f(f(index//u)*length)/f(v))-f(length*f(.5)))/radius)
    return np.array((f(sine*radius),f(height*radius),f(cosine*radius)),dtype='<f4')


def cylinder_live_grid(u,v,index,length,radius):
    """Cylinder +C8 uses signed IDIV on its saved int16 index, including -1.

    Radial methods deliberately save -1 rather than their selected target.
    Signed remainder must not become Python's positive modulo here.
    """
    if index!=-1:return cylinder_grid(u,v,index,length,radius)
    if not 1<=u<=127 or not 0<=v<=127 or radius<=0:raise UnsupportedEffect('圆柱当前位置参数尚未接入')
    f=np.float32;length=f(length);radius=f(radius)
    if not np.isfinite((length,radius)).all():raise ValueError('Invalid live cylinder dimensions')
    quotient=int(-1/u);remainder=-1-quotient*u
    phase=f(f(f(remainder)*f(2*math.pi))/f(u))
    height=f(0)if v==0 else f(f(f(f(f(quotient)*length)/f(v))-f(length*f(.5)))/radius)
    return np.array((f(f(math.sin(float(phase)))*radius),f(height*radius),f(f(math.cos(float(phase)))*radius)),dtype='<f4')


def cylinder_saved_index(effect,data,ordinal,identity):
    source=nested(data.get('Data'));method=number(source,'GeMT')
    if method not in(0,1,2,3):raise UnsupportedEffect('圆柱发射方式 '+str(method))
    if method<2:return -1
    count=number(source,'DivX')*(number(source,'DivY')+1)
    if count<=0:raise UnsupportedEffect('圆柱发射分段尚未接入')
    return ordinal%count if method%2 else SourceRandom((effect.sha256+repr(identity)).encode()).integer(count)


def cylinder_live_position(data,time,index,parent):
    """Actual ICbP anchor, independent of generator's radial-center output."""
    from effect_velocity_rotation import linear
    source=nested(data.get('Data'));u=number(source,'DivX');v=number(source,'DivY')
    parent=np.asarray(parent,dtype='<f4')
    if u==0:return parent[:3,3].copy()
    point=cylinder_live_grid(u,v,index,curve(source.get('Len'),time),curve(source.get('Rad'),time))
    rotate=rotation(np.array([curve(source.get('An'+a),time)for a in'XYZ']),number(source,'ROT'))
    return np.float32(linear(parent[:3,:3],linear(rotate,point))+parent[:3,3])


def cylinder_velocity(u,index,rotate,basis,speed,*,radial_point=None):
    """Native normal-method velocity; caller owns the actual emitter basis."""
    f=np.float32;phase=f(f(f(index%u)*f(2*math.pi))/f(u))
    local=np.array((f(math.sin(float(phase))),0,f(math.cos(float(phase)))),dtype='<f4')if radial_point is None else np.asarray(radial_point,dtype='<f4')
    direction=np.asarray(rotate,dtype='<f4')@local
    world=np.asarray(basis,dtype='<f4')@direction
    squared=f(f(f(world[1]*world[1])+f(world[0]*world[0]))+f(world[2]*world[2]))
    if not np.isfinite(squared)or squared<=0:raise UnsupportedEffect('圆柱发射零方向尚未接入')
    # Return the emitter-local velocity whose final basis application yields
    # the native normalized world direction, including nonuniform scales.
    return f(direction*f(f(speed)/f(np.sqrt(squared))))


def cylinder_injection(effect,data,time,ordinal,identity,*,basis=None):
    packed=None if basis is None else np.asarray(basis,dtype='<f4').tobytes()
    return cylinder_birth(effect,data.get('Data'),time,ordinal,identity,number(data,'bAD'),packed)


def cylinder_motion_birth(effect,data,time,ordinal,identity,parent,mode):
    """Reviewed methods0..3 outputs including the independent third vector."""
    from effect_velocity_rotation import cylinder_packet,in_child_space
    source=nested(data.get('Data'));method=number(source,'GeMT')
    # Existing route enforces supported methods, dimensions, any-direction
    # and random-parameter boundaries before producing these extra outputs.
    cylinder_injection(effect,data,time,ordinal,identity,basis=np.asarray(parent)[:3,:3])
    u=number(source,'DivX');v=number(source,'DivY');count=u*(v+1)
    index=ordinal%count if method%2 else SourceRandom((effect.sha256+repr(identity)).encode()).integer(count)
    grid=cylinder_grid(u,v,index,curve(source.get('Len'),time),curve(source.get('Rad'),time))
    f=np.float32;phase=f(f(f(index%u)*f(2*math.pi))/f(u))
    normal=np.array((f(math.sin(float(phase))),0,f(math.cos(float(phase)))),dtype='<f4')
    rotate=rotation(np.array([curve(source.get('An'+a),time)for a in'XYZ']),number(source,'ROT'))
    return in_child_space(cylinder_packet(grid,normal,rotate,parent,curve(source.get('IjS'),time),method<2),parent,mode)


@lru_cache(maxsize=4096)
def cylinder_birth(effect,node,time,ordinal,identity,any_direction,basis):
    source=nested(node);method=number(source,'GeMT');u=number(source,'DivX');v=number(source,'DivY')
    if method not in(0,1,2,3):raise UnsupportedEffect('圆柱发射方式 '+str(method))
    if any_direction:raise UnsupportedEffect('圆柱任意方向发射尚未接入')
    if any(curve(source.get('An'+a+'R'),time)for a in'XYZ')or curve(source.get('IjSR'),time):
        raise UnsupportedEffect('圆柱发射随机角度或速度尚未接入')
    speed=curve(source.get('IjS'),time)
    if speed and basis is None:raise UnsupportedEffect('圆柱非零注入速度缺少已核实父变换')
    if not 1<=u<=127 or not 0<=v<=127:raise UnsupportedEffect('圆柱发射分段尚未接入')
    count=u*(v+1)
    index=ordinal%count if method%2 else SourceRandom((effect.sha256+repr(identity)).encode()).integer(count)
    grid=cylinder_grid(u,v,index,curve(source.get('Len'),time),curve(source.get('Rad'),time))
    rotate=rotation(np.array([curve(source.get('An'+a),time)for a in'XYZ']),number(source,'ROT'))
    position=rotate@grid if method>=2 else np.zeros(3,dtype='<f4')
    velocity=cylinder_velocity(u,index,rotate,np.frombuffer(basis,dtype='<f4').reshape(3,3),speed,
        radial_point=grid if method<2 else None)if speed else np.zeros(3,dtype='<f4')
    position.flags.writeable=False;velocity.flags.writeable=False
    return position,velocity


@lru_cache(maxsize=4096)
def sphere_birth(effect,node,time,ordinal,identity):
    source=nested(node);method=number(source,'GeMT');u=number(source,'DivX');v=number(source,'DivY')
    if method not in(2,3,6,7):raise UnsupportedEffect('球形径向目标发射方式 '+str(method))
    if not 1<=u<=127 or not 2<=v<=127:raise UnsupportedEffect('球形发射分段')
    # CN3dda40 defines a source grid, Rx(latitude) then Ry(longitude),
    # starting at the original (0,1,0) axis. Native random normal branches
    # 3dff10/3e0fe0 and ordered 3e0790/3e1870 select these grid indices.
    lower=u if method>=6 else u-1;upper=u*v
    if method%2:index=lower+ordinal%(upper-lower+(0 if method>=6 else 1))
    else:index=lower+SourceRandom((effect.sha256+repr(identity)).encode()).integer(upper-lower+1)
    latitude=(index//u)*math.pi/v;longitude=(index%u)*2*math.pi/u
    direction=np.array((math.sin(latitude)*math.sin(longitude),math.cos(latitude),math.sin(latitude)*math.cos(longitude)))
    if any(curve(source.get('An'+a+'R'),time)for a in'XYZ')or curve(source.get('IjSR'),time):
        raise UnsupportedEffect('球形发射随机角度或速度')
    direction=rotation(np.array([curve(source.get('An'+a),time)for a in'XYZ']),number(source,'ROT'))@direction
    position=direction*curve(source.get('Rads'),time);velocity=direction*curve(source.get('IjS'),time)
    position.flags.writeable=False;velocity.flags.writeable=False
    return position,velocity
