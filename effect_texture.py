"""Source first-color texture lists and per-birth selection bytes."""
from functools import lru_cache
import numpy as np
from effect_sample import nested,particle_fields,number,curve,curve_source,UnsupportedEffect


def list_index(choices,value):
    # CN3a6324..3a6334 initializes source+10 to count<<16. After the
    # scalar/random curve getter, 3a65f1 adds CVTTSS2SI(value), performs
    # signed IDIV by count, then loads a SIGNED texture-list byte.
    count=len(choices)
    if not 1<=count<=255:raise UnsupportedEffect('首层纹理列表长度尚未核实')
    value=np.float32(value)
    if not np.isfinite(value)or not -2**31<=value<2**31:
        raise UnsupportedEffect('首层纹理选择超出整数范围')
    total=((count<<16)+int(np.trunc(value))+2**31)%2**32-2**31
    remainder=total%count if total>=0 else-((-total)%count)
    if remainder<0:raise UnsupportedEffect('首层纹理选择负余数尚未接入')
    index=choices[remainder]
    return index if index<128 else index-256


def texture_index(effect,channel,time,packed=0):
    index=number(channel,'TxNo',-1)
    if index<0:
        choices=channel.get('TLst')
        if choices is None or not choices.data:return None
        from effect_uv import addition
        value=np.float32(np.float32(curve(channel.get('TxN'),time))+addition(channel.get('TxNR'),time,packed))
        index=list_index(choices.data,value)
    elif curve(channel.get('TxN'),time):
        raise UnsupportedEffect('非列表动画贴图索引尚未接入')
    return resolved_index(index,len(effect.textures))


def resolved_index(index,count):
    # CN3e849a..3e84fe: null is a real source binding. Retain the
    # enabled shader layer and bind a null SRV; do not substitute a texture.
    if index in(-2,-3,-5):raise UnsupportedEffect('源列表特殊场景纹理尚未接入')
    if not 0<=count<=127:raise UnsupportedEffect('原始纹理表数量超出已核实范围')
    return index if 0<=index<count else None


def static_color_index(effect,channel,time):
    if curve(channel.get('TxN'),time)or channel.get('TLst')is not None:
        raise UnsupportedEffect('附加颜色层动态纹理选择尚未接入')
    # TC2/3/4 parser writes the low byte of TxNo; the particle constructor
    # reads it signed. The default source byte is 0xff, independently of TC1.
    value=number(channel,'TxNo',-1)&255
    return resolved_index(value if value<128 else value-256,len(effect.textures))


@lru_cache(maxsize=512)
def needs_state(node):
    channel=nested(particle_fields(node).get('TC1'))
    if not number(channel,'bEna')or number(channel,'TxNo',-1)>=0:return False
    choices=channel.get('TLst')
    if choices is not None and len(choices.data)>1:return True
    return any(keys and (len(keys)>1 or keys[0][4]!=0)
        for _,keys in(curve_source(channel.get(name))for name in('TxN','TxNR')))


@lru_cache(maxsize=4096)
def initialize(node,seed):
    from effect_simple import SourceRandom
    from effect_uv import random_byte
    source=nested(nested(particle_fields(node).get('TC1')).get('TxNR'))
    mode=number(source,'RanT')
    if mode not in(0,1,2):raise UnsupportedEffect('首层纹理逐次更新随机选择尚未接入')
    # CN3e2155 initializes this byte from TC1+20 once per particle birth.
    # Use an independent stable preview seed, not a native global RNG claim.
    return random_byte(SourceRandom(seed),mode)
