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
