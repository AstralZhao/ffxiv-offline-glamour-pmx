"""Independent photography-studio choices using the reviewed mode0 light math.

These are authored studio conditions, not recreated game maps or weather.
Defaults retain the original fixed wardrobe inputs byte-for-byte.
"""
import math,struct

BACKGROUNDS={'dark':('深色',(.035,.045,.065,0)),
    'gray':('中性灰',(.30,.30,.30,0)),
    'light':('浅灰',(.72,.72,.72,0)),
    'warm':('暖米色',(.50,.44,.36,0)),
    'blue':('雾蓝',(.25,.34,.43,0)),
    'black':('纯黑',(0.,0.,0.,0)),
    'white':('纯白',(1.,1.,1.,0)),
    'charcoal':('炭灰',(.12,.12,.13,0)),
    'cream':('奶油白',(.90,.85,.74,0)),
    'khaki':('浅卡其',(.64,.57,.46,0)),
    'cocoa':('可可棕',(.29,.20,.16,0)),
    'rose':('灰粉',(.66,.47,.49,0)),
    'lavender':('淡紫',(.60,.54,.69,0)),
    'sage':('鼠尾草绿',(.43,.51,.41,0)),
    'navy':('藏蓝',(.06,.11,.20,0)),
    'burgundy':('酒红',(.30,.08,.14,0))}
LIGHTS={'original':'默认打光','soft':'柔和正面','side':'侧面塑形','warm':'暖光','cool':'冷光'}
LIGHT_DEFAULTS=((False,1,255,255,255,0.,1.,3.),(False,1,255,255,255,-2.,1.,1.),(False,1,255,255,255,2.,1.,-1.))
# brightness retains its saved name but now controls ambient only. Legacy
# settings without fill_brightness inherit their old combined brightness.
DEFAULT={'background':'dark','brightness':100,'antialiasing':'off',
    'fill_brightness':100,'ambient_color':(255,255,255),'fill_color':(255,255,255),'lights':LIGHT_DEFAULTS,
    'ambient_direction':struct.unpack('<3f',bytes.fromhex('c3d3133f'*3)),'ambient_directionality':66.7,'ambient_space':'world'}
LIGHT_TYPES={1:('类型 1 · 小范围',2.,4.),2:('类型 2 · 中范围',4.,7.),3:('类型 3 · 大范围',6.,12.)}
LIGHT_PRESETS={
    'original':dict(DEFAULT),
    'soft':{**DEFAULT,'ambient_direction':(0.,.5,math.sqrt(.75)),'ambient_directionality':25.},
    'side':{**DEFAULT,'ambient_direction':(-.8,.48,.36),'ambient_directionality':85.},
    'warm':{**DEFAULT,'brightness':110,'fill_brightness':110,'ambient_color':(255,232,190),'fill_color':(255,232,190)},
    'cool':{**DEFAULT,'brightness':112,'fill_brightness':112,'ambient_color':(191,219,255),'fill_color':(191,219,255)}}
LIGHT_FIELDS=tuple(k for k in DEFAULT if k not in ('background','antialiasing'))


def f32(value):return struct.unpack('<f',struct.pack('<f',value))[0]


def options(value=None):
    if value is None:return dict(DEFAULT)
    if not isinstance(value,dict)or set(value)-set(DEFAULT)-{'lighting'}:raise ValueError('摄影棚设置格式不正确')
    legacy_style=value.get('lighting','original')
    if legacy_style not in LIGHTS:raise ValueError('未知的摄影棚打光方式')
    result={**DEFAULT,**{k:v for k,v in value.items()if k!='lighting'}}
    if 'fill_brightness'not in value:result['fill_brightness']=result['brightness']
    if result['background']not in BACKGROUNDS:raise ValueError('未知的摄影棚背景')
    if result['ambient_space']not in ('camera','world'):raise ValueError('环境光方向基准不正确')
    result['ambient_space']='world'  # Accept old camera settings once, never retain following behavior.
    for name in ('brightness','fill_brightness'):
        if type(result[name])is not int or not 0<=result[name]<=200:raise ValueError('灯光亮度必须在0%至200%之间')
    for name in ('ambient_color','fill_color'):
        rgb=result[name]
        if not isinstance(rgb,(list,tuple))or len(rgb)!=3 or any(type(n)is not int or not 0<=n<=255 for n in rgb):raise ValueError('灯光RGB必须在0至255之间')
        result[name]=tuple(rgb)
    direction=result['ambient_direction'];directionality=result['ambient_directionality']
    if not isinstance(direction,(list,tuple))or len(direction)!=3 or any(type(n)not in(int,float)or not math.isfinite(n)or abs(n)>1 for n in direction):raise ValueError('环境光方向必须在-1至1之间')
    if type(directionality)not in(int,float)or not math.isfinite(directionality)or not 0<=directionality<=100:raise ValueError('环境光方向比例必须在0%至100%之间')
    result['ambient_direction']=tuple(f32(round(n,6))for n in direction);result['ambient_directionality']=round(float(directionality),1)
    lights=result['lights']
    if not isinstance(lights,(list,tuple))or len(lights)!=3:raise ValueError('需要三盏灯的设置')
    normalized=[]
    for row in lights:
        if not isinstance(row,(list,tuple))or len(row)!=8:raise ValueError('灯光设置格式不正确')
        enabled,kind,*rest=row
        if type(enabled)is not bool or type(kind)is not int or kind not in LIGHT_TYPES:raise ValueError('灯光类型不正确')
        if any(type(n)is not int or not 0<=n<=255 for n in rest[:3]):raise ValueError('灯光RGB必须在0至255之间')
        if any(type(n)not in(int,float)or not math.isfinite(n)or abs(n)>100 for n in rest[3:]):raise ValueError('灯光位置超出范围')
        normalized.append((enabled,kind,*rest[:3],*(float(n)for n in rest[3:])))
    result['lights']=tuple(normalized)
    if result['antialiasing']not in ('off','smaa','ssaa','msaa4'):raise ValueError('未知的抗锯齿选项')
    # Read old saved styles once into explicit fields. Returned state has no
    # style multiplier, so normalization/save/reload cannot apply it again.
    if legacy_style in ('soft','side'):
        if 'ambient_direction'not in value:result['ambient_direction']=tuple(f32(round(n,6))for n in LIGHT_PRESETS[legacy_style]['ambient_direction'])
        if 'ambient_directionality'not in value:result['ambient_directionality']=LIGHT_PRESETS[legacy_style]['ambient_directionality']
    elif legacy_style in ('warm','cool'):
        tint=(1.10,1.,.82)if legacy_style=='warm'else(.84,.96,1.12)
        for rgb_name,gain_name in (('ambient_color','brightness'),('fill_color','fill_brightness')):
            rgb=[n*t for n,t in zip(result[rgb_name],tint)];peak=max(rgb)/255
            if peak:
                result[rgb_name]=tuple(min(255,round(n/peak))for n in rgb)
                result[gain_name]=min(200,round(result[gain_name]*peak))
    return result


def key(value=None):return tuple(options(value).values())


def apply_preset(value,name):
    if name not in LIGHT_PRESETS:raise ValueError('未知的灯光预设')
    return options({**options(value),**{k:LIGHT_PRESETS[name][k]for k in LIGHT_FIELDS}})


def direction_angles(direction):
    x,y,z=direction
    return math.degrees(math.atan2(x,z)),math.degrees(math.atan2(y,math.hypot(x,z)))


def angle_direction(horizontal,elevation):
    h,v=map(math.radians,(horizontal,elevation));cv=math.cos(v)
    return math.sin(h)*cv,math.sin(v),math.cos(h)*cv


def rotate_direction(direction,rotation,*,to_view):
    if len(rotation)!=3 or any(len(row)!=3 for row in rotation):raise ValueError('相机方向尚未准备好')
    value=tuple(sum((rotation[i][j]if to_view else rotation[j][i])*direction[j]for j in range(3))for i in range(3))
    if not all(math.isfinite(n)for n in value):raise ValueError('相机方向不正确')
    return tuple(float(n)for n in value)


def default_lighting(value):
    value=options(value)
    return all(value[k]==DEFAULT[k]for k in ('brightness','fill_brightness','ambient_color','fill_color','ambient_direction','ambient_directionality','ambient_space'))and not any(row[0]for row in value['lights'])


def light_inputs(value,ambient,camera,*,view=None):
    value=options(value);gain=value['brightness']/100;fill_gain=value['fill_brightness']/100
    import numpy as np
    matrix=np.eye(3)if view is None else np.asarray(view,dtype=float)[:3,:3]
    if matrix.shape!=(3,3)or not np.isfinite(matrix).all()or not np.allclose(matrix@matrix.T,np.eye(3),atol=2e-5,rtol=0)or not np.isclose(np.linalg.det(matrix),1,atol=2e-5,rtol=0):raise ValueError('环境光相机旋转不正确')
    direction=np.asarray(rotate_direction(value['ambient_direction'],matrix,to_view=True),dtype='<f4')
    native_ambient=(np.array_equal(direction,np.asarray(DEFAULT['ambient_direction'],dtype='<f4'))and value['ambient_directionality']==DEFAULT['ambient_directionality'])
    if native_ambient and gain==fill_gain==1 and value['ambient_color']==value['fill_color']==(255,255,255):return ambient,tuple(camera)
    import json
    from cn_offscreen_lighting import mode0_ambient,PROFILE_PATH
    # Source source_lighting() has already validated this profile and its
    # constants against the installed CN executable before this function runs.
    profile=json.loads(PROFILE_PATH.read_bytes())
    def floats(name):return np.frombuffer(bytes.fromhex(profile['data'][name]['float32_hex']),dtype='<f4').copy()
    fraction=value['ambient_directionality']/100
    ambient_tint=np.asarray(value['ambient_color'],dtype='<f4')/np.float32(255)
    fill_tint=np.asarray(value['fill_color'],dtype='<f4')/np.float32(255)
    payload=ambient if native_ambient and gain==1 and value['ambient_color']==(255,255,255)else mode0_ambient(fraction,direction,floats('color')*ambient_tint*np.float32(gain),floats('basis'),profile['reflection_offset']['value'])
    direct=np.asarray(camera,dtype='<f4').copy();direct[:3]*=fill_tint*np.float32(fill_gain);direct[3]*=np.float32(fill_gain)*np.max(fill_tint)
    return payload,tuple(direct)
