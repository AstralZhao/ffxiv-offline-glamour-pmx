"""Source Simple/Powder state sampled independently at a fixed source time.

CN source conventions: 0.1-frame damped steps, 1024-step rotations, signed
normal/direction packing, /50 random scales, four color keys and SNORM atlas.
The RNG algorithm is native xorshift128 with a stable preview seed, rather
than the running game's shared RNG state. No character motion is simulated.
"""
import hashlib
import math
import struct
from functools import lru_cache

import numpy as np

from effect_sample import nested,number,UnsupportedEffect
from vfx import records
from effect_simple_state import PARTICLE,StateInput,Update as StateUpdate,advance as advance_state,replay as replay_state


class SourceRandom:
    def __init__(self,seed):
        self.state=list(struct.unpack('<4I',hashlib.sha256(seed).digest()[:16]))
        if not any(self.state):self.state[0]=1

    def word(self):
        x,y,z,w=self.state;t=(x^(x<<11))&0xffffffff
        value=(w^(w>>19)^t^(t>>8))&0xffffffff
        self.state=[y,z,w,value];return value

    def unit(self):return (self.word()&65535)/65535
    def between(self,a,b):
        f=np.float32;a=f(a);b=f(b)
        return f(f(f(f(b-a)*f(self.word()&65535))/f(65535))+a)
    def integer(self,count):return ((self.word()&65535)*count)>>16


def packed_direction(values,precision):
    value=np.asarray(values,dtype=np.float32);length=np.sqrt(np.sum(value*value,dtype=np.float32))
    if length==0:return np.zeros(3,dtype=np.float32)
    packed=np.trunc(value/length*np.float32(precision))
    return (packed/np.float32(precision)).astype(np.float32)


def packed_vector(values,precision):
    value=np.asarray(values,dtype=np.float32);length=np.sqrt(np.sum(value*value,dtype=np.float32))
    return (packed_direction(value,precision)*length).astype(np.float32)


def position_state(values):
    value=np.asarray(values,dtype='<f4');f=np.float32
    length=f(np.sqrt(f(f(f(value[0]*value[0])+f(value[1]*value[1]))+f(value[2]*value[2]))))
    return length,np.trunc(value/length*f(32000)).astype('<i2')if length>0 else np.zeros(3,dtype='<i2')


def random_direction(random):
    # Keep float32 source inputs and the UNPACKED normalized direction until
    # authored speed has been applied. Initializer3e6745..3e686b packs later.
    f=np.float32;first=f(random.word()&65535);second=f(random.word()&65535)
    y=f(f(1)-f(f(first+first)/f(65535)));phase=f(f(second*f(2*math.pi))/f(65535))
    radius=f(math.sin(float(f(math.acos(float(y))))))
    values=np.array((f(f(math.sin(float(phase)))*radius),y,
        f(f(math.cos(float(phase)))*radius)),dtype='<f4')
    length=f(np.sqrt(f(f(f(values[1]*values[1])+f(values[0]*values[0]))+f(values[2]*values[2]))))
    if length>f(1e-5):values=values*f(f(1)/length)
    return values


def injection_direction(kind,random,*,radial=(0.,0.),normal=None):
    # CN3e5f6c: fixed axes consume NO random words. Type1 is computed
    # before position sampling; type5 reads the model prepass normal.
    if kind==0:return random_direction(random)
    if kind in(2,3,4):return np.eye(3,dtype='<f4')[kind-2]
    if kind==1:
        # CN405a30 consumes two words and samples uniformly in cosine
        # between the authored radial angles. The caller invokes this BEFORE
        # position sampling, matching3e5c98, not in the later direction switch.
        f=np.float32;first=f(random.word()&65535)
        lower=f(math.cos(float(f(radial[0]))));upper=f(math.cos(float(f(radial[1]))))
        y=f(lower-f(f(lower-upper)*f(first/f(65535))))
        if not -1<=y<=1:raise ValueError('Simple radial cosine outside finite domain')
        radius=f(math.sin(float(f(math.acos(float(y))))))
        phase=f(f(f(random.word()&65535)*f(2*math.pi))/f(65535))
        value=np.array((f(f(math.sin(float(phase)))*radius),y,
                        f(f(math.cos(float(phase)))*radius)),dtype='<f4')
        return value
    elif kind==5:value=np.zeros(3,dtype='<f4')if normal is None else np.asarray(normal,dtype='<f4').copy()
    else:raise UnsupportedEffect('Simple 注入方向类型 '+str(kind))
    return normalize_injection_direction(value)


def normalize_injection_direction(value):
    f=np.float32;square=f(f(f(value[1]*value[1])+f(value[0]*value[0]))+f(value[2]*value[2]))
    if square>f(1e-5):value=value*f(f(1)/f(np.sqrt(square)))
    return value


def packed_motion(direction,speed):
    # 3e6745..3e686b: multiply BEFORE packing; save measured float32
    # magnitude, not the authored speed. Negative speed reverses the bytes.
    f=np.float32;values=np.asarray(direction,dtype='<f4')*f(speed)
    length=f(np.sqrt(f(f(f(values[1]*values[1])+f(values[0]*values[0]))+f(values[2]*values[2]))))
    packed=np.trunc(values/length*f(100)).astype('i1')if length>0 else np.zeros(3,dtype='i1')
    return length,packed


def random_scales(data,random):
    # CN3e61da..3e6369: bSRL copies the packed X BYTE to Y; it does
    # not reuse a random fraction in the separate (possibly stale) Y range.
    values=[]
    for axis in ('X',)if number(data,'bSRL')else('X','Y'):
        low=number(data,'SR'+axis+'0',real=True);high=number(data,'SR'+axis+'1',real=True)
        value=np.float32(np.float32(high-low)*np.float32(random.word()&65535))
        value=np.float32(np.float32(value/np.float32(65535))+np.float32(low))
        packed=int(np.trunc(np.float32(value*np.float32(50))))
        values.append(np.float32(((packed+128)%256-128)/50))
    return np.array(values*2 if len(values)==1 else values,dtype=np.float32)


def random_atlas(spread,cells,random):
    # CN3e615c..3e61d6: nonzero UvNR is an INCLUSIVE upper bound;
    # pick 0..UvNR, then reduce modulo the authored atlas cell count.
    # Even a one-cell atlas consumes this random word when UvNR is nonzero.
    if not 0<=spread<=127 or not 1<=cells<=127*127:
        raise UnsupportedEffect('Simple 初始图集随机范围')
    return random.integer(spread+1)%cells if spread else 0


def random_parameters(data,random,cells):
    """Reviewed Simple tail order: atlas, sizes, speed, XYZ angles, XYZ spin.

    Position/direction generation precedes this; optional delay/reverse follow
    it. The order applies to one supported BlkN=1 particle, not shared game RNG.
    """
    f=np.float32;real=lambda key:number(data,key,0.,real=True)
    atlas=random_atlas(number(data,'UvNR'),cells,random)
    scales=random_scales(data,random)
    speed=random.between(real('VMin'),real('VMax'))
    base=[f(random.between(-real('RB'+a),real('RB'+a))+f(real('RI'+a)))for a in'XYZ']
    spin=[f(random.between(-real('RV'+a),real('RV'+a))+f(real('RA'+a)))for a in'XYZ']
    def packed(values):
        value=np.trunc(f(f(values)*f(1024))/f(2*math.pi)).astype(np.int64)
        return ((value+32768)%65536-32768).astype('<i2')
    return {'atlas':atlas,'scales':scales,'speed':speed,'angles':packed(base),'spin':packed(spin)}


@lru_cache(maxsize=64)
def emission_points(effect,index):
    models=[r for r in effect.source_records if r.tag=='Modl']
    if not 0<=index<len(models):raise ValueError('Simple injection model index differs')
    data=nested(models[index]);vertices=data.get('VEmt');numbers=data.get('VNum')
    if vertices is None or numbers is None or len(vertices.data)%28 or len(numbers.data)%2:
        raise ValueError('Invalid original AVFX emission model')
    dtype=np.dtype([('position','<f4',3),('normal','<f4',3),('color','u1',4)])
    values=np.frombuffer(vertices.data,dtype=dtype);order=np.frombuffer(numbers.data,dtype='<u2')
    if not len(order)or not len(values)or np.any(order>=len(values)):raise ValueError('Emission model vertex order differs')
    if not np.isfinite(values['position']).all()or not np.isfinite(values['normal']).all():raise ValueError('Non-finite emission model')
    result=values['position'][order].copy(),values['normal'][order].copy()
    for value in result:value.flags.writeable=False
    return result


def model_positions(points,area,count,random,*,state=None):
    """CN3e5510 prepass, supported BlkN=1 / VBMN=-1 / SIPT=1.

    All model positions precede the general direction/attribute pass. Source
    random area components are consumed Z, Y, X, even for zero extents.
    """
    points=np.asarray(points,dtype='<f4');area=np.asarray(area,dtype='<f4')
    if points.ndim!=2 or points.shape[1]!=3 or not len(points)or not np.isfinite(points).all():
        raise ValueError('Invalid Simple emission positions')
    if area.shape!=(3,)or not np.isfinite(area).all()or type(count)is not int or not 1<=count<=512:
        raise ValueError('Invalid Simple model area/count')
    result=np.empty((count,3),dtype='<f4')
    for i in range(count):
        point=points[i%len(points)].copy()
        for axis in(2,1,0):point[axis]=np.float32(point[axis]+random.between(-area[axis],area[axis]))
        length,packed=position_state(point)
        result[i]=(packed.astype('<f4')/np.float32(32000))*length
        if state is not None:state['length'][i]=length;state['packed_position'][i]=packed
    return result


@lru_cache(maxsize=128)
def initial(effect,node):
    d=nested(node);integer=lambda key,default=0:number(d,key,default)
    real=lambda key,default=0.:number(d,key,default,real=True)
    count=integer('CCnt');life=integer('CrIL');interval=integer('CrI');group=integer('CrIC')
    if not 1<=count<=512 or not 0<life<=4096 or interval<=0 or group<=0:raise UnsupportedEffect('Simple 数量、寿命或发射间隔')
    if integer('CrLR')or integer('VBMN',-1)!=-1 or integer('SBDT')!=0:
        raise UnsupportedEffect('Simple 随机寿命、绑定模型或方向类型')
    if integer('SIDT')not in range(6):raise UnsupportedEffect('Simple 注入方向类型 '+str(integer('SIDT')))
    spread=integer('CrIR')
    if not 0<=spread<=127 or interval>127:raise UnsupportedEffect('Simple 有符号发射间隔范围')
    if integer('SIPT')not in(0,1)or integer('BlkN')!=1:raise UnsupportedEffect('Simple 注入或分块类型')
    # VBMN=-1 above selects the original no-follow branch in BOTH update
    # and draw. FltR/FltS are inactive there, irrespective of their values.
    frames=d.get('Frms');colors=d.get('Cols')
    if frames is None or len(frames.data)!=8 or colors is None or len(colors.data)!=16:raise ValueError('Invalid Simple color frame source')
    keys=np.array(struct.unpack('<4h',frames.data),dtype=float)
    if np.any(np.diff(keys)<=0):raise UnsupportedEffect('Simple 重叠颜色帧')
    rgba=np.frombuffer(colors.data,dtype='u1').reshape(4,4).astype(float)/255
    area=np.array([real('CrA'+a)for a in'XYZ']);damping=np.array([real('CA'+a)for a in'XYZ'])
    gravity=np.array([real('CG'+a)for a in'XYZ']);curve=real('SC')
    if np.any(damping<0)or np.any(damping>1)or curve<0:raise UnsupportedEffect('Simple 衰减或尺寸曲线范围')
    start=np.array((real('SBX'),real('SBY')));end=np.array((real('SEX'),real('SEY')))
    cells=(integer('UvCU'),integer('UvCV'));uv_interval=integer('UvIv')
    if any(not 1<=v<=127 for v in cells)or uv_interval<0:raise UnsupportedEffect('Simple 图集尺寸或间隔')
    loops=integer('UvLC')
    if not 0<=loops<=127 or loops and not 1<=uv_interval<=127:raise UnsupportedEffect('Simple 有限图集参数')
    positions=np.zeros((count,3),dtype=np.float32);velocity=positions.copy();angles=positions.copy();spin=positions.copy()
    scales=np.ones((count,2));atlas=np.zeros(count,dtype=int);reverse=np.zeros(count,dtype=bool);random=SourceRandom(effect.sha256.encode()+node.data)
    state=np.zeros(count,dtype=PARTICLE);model=integer('SIPT')==1
    if model:positions[:]=model_positions(emission_points(effect,integer('IJMN',-1))[0],area,count,random,state=state)
    direction_type=integer('SIDT');model_normals=emission_points(effect,integer('IJMN',-1))[1]if model and direction_type==5 else None
    if model_normals is not None:state['velocity']=model_normals[np.arange(count)%len(model_normals)]
    for i in range(count):
        radial=injection_direction(1,random,radial=(real('IRD0'),real('IRD1')))if direction_type==1 else None
        if not model:
            position=[np.float32(random.between(0,a)*radial[j])for j,a in enumerate(area)]if radial is not None else[random.between(-a,a)for a in area]
            length,packed=position_state(position)
            state['length'][i]=length;state['packed_position'][i]=packed
            positions[i]=(packed.astype('<f4')/np.float32(32000))*length
        direction=normalize_injection_direction(radial)if radial is not None else injection_direction(direction_type,random,
            normal=None if model_normals is None else model_normals[i%len(model_normals)])
        parameters=random_parameters(d,random,cells[0]*cells[1])
        speed,packed=packed_motion(direction,parameters['speed'])
        velocity[i]=(packed.astype('<f4')/np.float32(100))*speed
        angles[i]=parameters['angles'];spin[i]=parameters['spin']
        scales[i]=parameters['scales'];atlas[i]=parameters['atlas']
        # CN3e68ea..3e6975: inclusive random delay follows spin and
        # precedes reverse-UV. It changes birth time, never playback speed.
        delay=(i//group)*interval+(random.integer(spread+1)if spread else 0)
        reverse[i]=bool(random.integer(2))if integer('bRUV')else False
        state['speed'][i]=speed;state['packed_velocity'][i]=packed
        state['angles'][i]=parameters['angles'];state['spin'][i]=parameters['spin']
        state['scales'][i]=np.rint(parameters['scales']*np.float32(50)).astype('i1')
        state['atlas'][i]=int(atlas[i])|(int(reverse[i])<<15)
        state['delay'][i]=((int(delay)+32768)%65536)-32768
    result={'positions':positions,'velocity':velocity,'angles':angles,'spin':spin,'scales':scales,'atlas':atlas,
        'delay':state['delay'].astype(float),'life':life,'repeat':bool(integer('bCrN')),
        'damping':damping,'gravity':gravity,'curve':curve,'start':start,'end':end,'frames':keys,'colors':rgba,
        'cells':cells,'uv_interval':uv_interval,'reverse':reverse,'pivot':np.array((real('PvtX'),real('PvtY'))),
        'atlas_limit':(*cells,uv_interval,loops)if loops else None,
        'inherit_scale':bool(integer('bSnP')),'bind_parent':bool(integer('bBnP')),'initial_state':state.tobytes(),
        'random_state':tuple(random.state)}
    for value in result.values():
        if isinstance(value,np.ndarray):value.flags.writeable=False
    return result


def geometry(effect,node,time,*,updates=None):
    source=initial(effect,node)
    if updates is None:
        if source['atlas_limit']is not None:raise UnsupportedEffect('Simple 有限图集需要明确更新历史')
        # Legacy default stays explicit until the caller supplies its actual
        # update/retention history. Never label this branch as native replay.
        age=time-source['delay'];live=age>=0
        if source['repeat']:age=np.remainder(age,source['life'])
        else:live&=age<source['life']
        age=age[live];count=len(age);steps=np.floor(age*10+1e-6).astype(int)
        displacement=np.empty((count,3));motion=np.empty((count,3))
        for axis,d in enumerate(source['damping']):
            total=steps if d==1 else d*(1-np.power(d,steps))/(1-d)
            motion[:,axis]=total*.1
            displacement[:,axis]=source['velocity'][live,axis]*total*.1
        centers=source['positions'][live]+displacement
    else:
        if isinstance(updates,StateInput):
            if updates.source!=(effect.sha256,node)or len(updates.packets)!=len(source['initial_state']):
                raise ValueError('Simple snapshot belongs to another source')
            packets=updates.packets
        else:
            if not isinstance(updates,(tuple,list)):raise ValueError('Simple history requires an explicit ordered sequence')
            if any(not isinstance(event,StateUpdate)or event.bound!=source['bind_parent']for event in updates):
                raise ValueError('Simple history binding differs from the original source')
            packets=replay_state(source['initial_state'],source['damping'],source['life'],source['repeat'],updates,atlas=source['atlas_limit'])
        state=np.frombuffer(packets,dtype=PARTICLE);live=state['state']==1
        age=state['age'][live].copy();centers=state['position'][live].copy();count=len(age)
    gravity=.5*age[:,None]**2*source['gravity']if updates is None else((age*age)[:,None]*source['gravity'].astype('<f4'))*np.float32(.5)
    angles=(source['angles'][live]+np.trunc(age[:,None]*source['spin'][live])).astype(int)&1023
    angles=angles*(2*np.pi/1024)
    sizes=(source['start']+(source['end']-source['start'])*(age/source['life'])[:,None]**source['curve'])*source['scales'][live]
    colors=np.column_stack([np.interp(age,source['frames'],source['colors'][:,i])for i in range(4)])
    u,v=source['cells'];cell=source['atlas'][live].copy()
    if source['uv_interval']:cell+=np.floor(age/source['uv_interval']).astype(int)
    cell%=u*v;step=np.array((32767//u,32767//v));origin=np.column_stack((cell%u,cell//u))*step
    corners=np.array(((0,0),(1,0),(0,1),(1,1)))
    uv=(origin[:,None,:]+corners[None,:,:]*step)/32767
    reversed_rows=source['reverse'][live]
    uv[reversed_rows]=uv[reversed_rows][:,(1,0,3,2)]
    indices=(np.arange(count)[:,None]*4+np.array((0,1,2,2,1,3))).ravel().astype('<u4')
    result={'positions':np.zeros((count*4,3)),'colors':np.repeat(colors,4,axis=0),'indices':indices,
        'base_uv':np.tile(uv.reshape(-1,1,2),(1,4,1)),
        'simple_centers':centers,'simple_angles':angles,'simple_sizes':sizes,'simple_pivot':source['pivot'],
        'simple_gravity':gravity,'simple_bind_parent':source['bind_parent'],
        'simple_inherit_scale':source['inherit_scale'],'simple_particle_count':count,
        'random_policy':'native-xorshift128-independent-stable-preview-seed'}
    if updates is None:
        result.update(simple_initial_positions=source['positions'][live].copy(),
            simple_initial_velocity=source['velocity'][live].copy(),simple_motion_factors=motion,simple_birth_age=time-age,
            simple_state_policy='legacy-age-reconstruction')
    else:result.update(simple_centers_world=not source['bind_parent'],simple_state_packets=packets,
        simple_state_policy='explicit-native-update-history')
    return result
