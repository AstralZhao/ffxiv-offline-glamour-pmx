"""Original AVFX emission vertices and normals for model emitters."""
import math

import numpy as np

from effect_sample import nested,number,curve,rotation,UnsupportedEffect
from effect_simple import emission_points,SourceRandom


def model_injection(effect,data,time,ordinal,identity):
    source=nested(data.get('Data'));method=number(source,'GeMT')
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
    return position,direction*speed


def sphere_injection(effect,data,time,ordinal,identity):
    source=nested(data.get('Data'));method=number(source,'GeMT');u=number(source,'DivX');v=number(source,'DivY')
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
    return direction*curve(source.get('Rads'),time),direction*curve(source.get('IjS'),time)
