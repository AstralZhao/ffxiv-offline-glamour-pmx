"""Per-birth CN particle scale/rotation/position random curve bytes.

Uses reviewed signed-byte math with independent stable preview seeds. Does not
claim the game's global RNG state or dynamic (update-time) random modes.
"""
from functools import lru_cache

import numpy as np

from effect_sample import particle_fields,nested,number,axes,constant_axes,curve_source,UnsupportedEffect
from effect_uv import random_byte,addition


@lru_cache(maxsize=256)
def has_random(node):
    d=particle_fields(node)
    return any(nested(d.get(name)).get(axis+'R')is not None for name in('Scl','Rot','Pos')for axis in'XYZ')


@lru_cache(maxsize=4096)
def initialize(node,seed):
    from effect_simple import SourceRandom
    d=particle_fields(node);random=SourceRandom(seed);result=[]
    # CN3e2c96..3e2d34: Scale XYZ, Rotation XYZ, Position XYZ.
    # All nine curve slots are initialized, including unassigned slots.
    for name in('Scl','Rot','Pos'):
        source=nested(d.get(name));packed=[]
        for axis in'XYZ':
            curve=source.get(axis+'R');mode=number(nested(curve),'RanT')
            if curve is not None and mode not in(0,1,2):raise UnsupportedEffect('逐次更新的变换随机曲线尚未接入')
            packed.append(random_byte(random,mode))
        result.append(tuple(packed))
    return tuple(result)


def linked_addition(base,delta,connect):
    base=np.asarray(base,dtype=np.float32);delta=np.asarray(delta,dtype=np.float32).copy()
    links={0:(),1:((1,0),(2,0)),2:((1,0),),3:((2,0),),4:((0,1),(2,1)),
        5:((0,1),),6:((2,1),),7:((0,2),(1,2)),8:((0,2),),9:((1,2),)}
    if connect not in links:raise UnsupportedEffect('变换随机坐标轴连接类型')
    for dst,src in links[connect]:delta[dst]=delta[src]
    return base+delta


def values(node,time,packed):
    if len(packed)!=3 or any(len(v)!=3 for v in packed):raise ValueError('Transform random state needs three XYZ byte sets')
    d=particle_fields(node);result={}
    for (name,default),state in zip((('Scl',1),('Rot',0),('Pos',0)),packed):
        source=d.get(name);at=0 if constant_random_axes(source)else time
        result[name]=axis_values(source,at,default,state).copy()
    return result


@lru_cache(maxsize=4096)
def constant_random_axes(node):
    return constant_axes(node,3)and all(not keys or len(keys)==1
        for _,keys in(curve_source(nested(node).get(axis+'R'))for axis in'XYZ'))


@lru_cache(maxsize=16384)
def axis_values(node,time,default,state):
    source=nested(node);delta=[addition(source.get(axis+'R'),time,value)for axis,value in zip('XYZ',state)]
    result=linked_addition(axes(node,time,default),delta,number(source,'ACTR'))
    result.flags.writeable=False
    return result
