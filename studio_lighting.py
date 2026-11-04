"""Independent photography-studio choices using the reviewed mode0 light math.

These are authored studio conditions, not recreated game maps or weather.
Defaults retain the original fixed wardrobe inputs byte-for-byte.
"""
import math

BACKGROUNDS={'dark':('深色',(.035,.045,.065,0)),
    'gray':('中性灰',(.30,.30,.30,0)),
    'light':('浅灰',(.72,.72,.72,0)),
    'warm':('暖米色',(.50,.44,.36,0)),
    'blue':('雾蓝',(.25,.34,.43,0))}
LIGHTS={'original':'默认打光','soft':'柔和正面','side':'侧面塑形','warm':'暖光','cool':'冷光'}
DEFAULT={'background':'dark','lighting':'original','brightness':100}


def options(value=None):
    if value is None:return dict(DEFAULT)
    if not isinstance(value,dict)or set(value)-set(DEFAULT):raise ValueError('摄影棚设置格式不正确')
    result={**DEFAULT,**value}
    if result['background']not in BACKGROUNDS or result['lighting']not in LIGHTS:raise ValueError('未知的摄影棚背景或打光方式')
    if type(result['brightness'])is not int or not 25<=result['brightness']<=200:raise ValueError('摄影棚亮度必须在25%至200%之间')
    return result


def key(value=None):return tuple(options(value).values())


def light_inputs(value,ambient,camera):
    value=options(value);style=value['lighting'];gain=value['brightness']/100
    if style=='original'and gain==1:return ambient,tuple(camera)
    import json
    import numpy as np
    from cn_offscreen_lighting import mode0_ambient,PROFILE_PATH
    # Source source_lighting() has already validated this profile and its
    # constants against the installed CN executable before this function runs.
    profile=json.loads(PROFILE_PATH.read_bytes())
    def floats(name):return np.frombuffer(bytes.fromhex(profile['data'][name]['float32_hex']),dtype='<f4').copy()
    direction=floats('direction');fraction=float(floats('directional_fraction')[0]);tint=np.ones(3,dtype='<f4')
    if style=='soft':direction=np.asarray((0.,.5,math.sqrt(.75)),dtype='<f4');fraction=.25
    elif style=='side':direction=np.asarray((-.8,.48,.36),dtype='<f4');fraction=.85
    elif style=='warm':tint=np.asarray((1.10,1.,.82),dtype='<f4')
    elif style=='cool':tint=np.asarray((.84,.96,1.12),dtype='<f4')
    payload=mode0_ambient(fraction,direction,floats('color')*tint*np.float32(gain),floats('basis'),profile['reflection_offset']['value'])
    direct=np.asarray(camera,dtype='<f4').copy();direct[:3]*=tint*np.float32(gain);direct[3]*=np.float32(gain)
    return payload,tuple(direct)
