"""CN particle UV random bytes, kept per birth rather than per template."""
from functools import lru_cache

import numpy as np

from effect_sample import nested,number,curve,curve_source,axes,constant_axes,UnsupportedEffect
from vfx import records


def random_byte(random,mode):
    # CN398218/398290: xorshift128, low16, float32, truncate to signed byte.
    # 0 is symmetric, 1 positive, 2 negative. Modes 3..5 resample on update.
    if mode not in(0,1,2):return 0
    value=np.float32(random.word()&65535)
    if mode==0:value=np.float32(value+value)
    value=np.float32(value/np.float32(65535))
    if mode!=1:value=np.float32(value-np.float32(1))
    return int(np.trunc(np.float32(value*np.float32(100))))


@lru_cache(maxsize=256)
def uv_nodes(node):return tuple(n for n in records(node.data)if n.tag=='UvSt')


@lru_cache(maxsize=256)
def has_random(node):
    for uv in uv_nodes(node):
        d=nested(uv)
        for name in('Scl','Scr'):
            if any(nested(d.get(name)).get(axis+'R')is not None for axis in'XY'):return True
        if d.get('RotR')is not None:return True
    return False


@lru_cache(maxsize=4096)
def initialize(node,seed):
    from effect_simple import SourceRandom
    random=SourceRandom(seed);result=[]
    for uv in uv_nodes(node):
        d=nested(uv);scale=nested(d.get('Scl'));scroll=nested(d.get('Scr'))
        # CN3e5191..3e51cd always initializes all five source curve slots,
        # including unassigned curves. Do not correlate different UV layers.
        curves=[scale.get('XR'),scale.get('YR'),scroll.get('XR'),scroll.get('YR'),d.get('RotR')]
        result.append(tuple(random_byte(random,number(nested(n),'RanT'))for n in curves))
    return tuple(result)


def addition(node,time,packed):
    _,keys=curve_source(node)
    if not keys or len(keys)==1:time=0
    return addition_value(node,time,packed)


@lru_cache(maxsize=16384)
def addition_value(node,time,packed):
    d=nested(node);mode=number(d,'RanT')
    if mode not in(0,1,2):
        if node is not None:raise UnsupportedEffect('UV逐次更新随机曲线尚未接入')
        return np.float32(0)
    # CN3a5410/3a55d7 and39840e: multiply by .01 BEFORE amplitude.
    return np.float32(np.float32(packed)*np.float32(.01))*np.float32(curve(node,time))


def values(node,time,packed):
    if len(packed)!=5:raise ValueError('UV random state needs five signed bytes')
    d=nested(node);result=[]
    for i,(name,default)in enumerate((('Scl',1),('Scr',0))):
        child=d.get(name);at=0 if constant_random_axes(child)else time
        result.append(axis_values(child,at,default,packed[i*2:i*2+2]).copy())
    angle=np.float32(curve(d.get('Rot'),time))+addition(d.get('RotR'),time,packed[4])
    return result[0],result[1],float(angle)


@lru_cache(maxsize=1024)
def constant_set(node):
    data=nested(node)
    return all(constant_random_axes(data.get(name))for name in('Scl','Scr'))and all(
        not keys or len(keys)==1 for _,keys in(curve_source(data.get(name))for name in('Rot','RotR')))


def sample_time(node,time):return 0 if constant_set(node)else time


@lru_cache(maxsize=4096)
def constant_random_axes(node):
    return constant_axes(node,2)and all(not keys or len(keys)==1
        for _,keys in(curve_source(nested(node).get(axis+'R'))for axis in'XY'))


@lru_cache(maxsize=16384)
def axis_values(node,time,default,packed):
    data=nested(node);base=np.asarray(axes(node,time,default,2),dtype='<f4')
    delta=np.array([addition(data.get(axis+'R'),time,value)for axis,value in zip('XY',packed)],dtype='<f4')
    connect=number(data,'ACTR')
    if connect==1:delta[1]=delta[0]
    elif connect==2:delta[0]=delta[1]
    elif connect!=0:raise UnsupportedEffect('UV随机坐标轴连接类型')
    value=base+delta;value.flags.writeable=False
    return value
