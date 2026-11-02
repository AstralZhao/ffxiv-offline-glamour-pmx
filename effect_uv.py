"""CN particle UV random bytes, kept per birth rather than per template."""
from functools import lru_cache

import numpy as np

from effect_sample import nested,number,curve,axes,UnsupportedEffect
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


def uv_nodes(node):return tuple(n for n in records(node.data)if n.tag=='UvSt')


@lru_cache(maxsize=256)
def has_random(node):
    for uv in uv_nodes(node):
        d=nested(uv)
        for name in('Scl','Scr'):
            if any(nested(d.get(name)).get(axis+'R')is not None for axis in'XY'):return True
        if d.get('RotR')is not None:return True
    return False


@lru_cache(maxsize=128)
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
        data=nested(d.get(name));base=np.asarray(axes(d.get(name),time,default,2),dtype='<f4')
        delta=np.array([addition(data.get(axis+'R'),time,packed[i*2+j])for j,axis in enumerate('XY')],dtype='<f4')
        connect=number(data,'ACTR')
        if connect==1:delta[1]=delta[0]
        elif connect==2:delta[0]=delta[1]
        elif connect!=0:raise UnsupportedEffect('UV随机坐标轴连接类型')
        result.append(base+delta)
    angle=np.float32(curve(d.get('Rot'),time))+addition(d.get('RotR'),time,packed[4])
    return result[0],result[1],float(angle)
