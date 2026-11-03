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
from effect_simple_state import Update as StateUpdate,advance as advance_state,replay as replay_state


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


def model_positions(points,area,count,random):
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
        result[i]=packed_vector(point,32000)
    return result


@lru_cache(maxsize=128)
def initial(effect,node):
    d=nested(node);integer=lambda key,default=0:number(d,key,default)
    real=lambda key,default=0.:number(d,key,default,real=True)
    count=integer('CCnt');life=integer('CrIL');interval=integer('CrI');group=integer('CrIC')
    if not 1<=count<=512 or not 0<life<=4096 or interval<=0 or group<=0:raise UnsupportedEffect('Simple 数量、寿命或发射间隔')
    if integer('CrIR')or integer('CrLR')or integer('VBMN',-1)!=-1 or integer('SIDT')!=0 or integer('SBDT')!=0:
        raise UnsupportedEffect('Simple 随机寿命、绑定模型或方向类型')
    if integer('SIPT')not in(0,1)or integer('BlkN')!=1:raise UnsupportedEffect('Simple 注入或分块类型')
    if real('FltR')!=1 or real('FltS')<0:raise UnsupportedEffect('Simple 速度追随曲线')
    if integer('UvLC'):raise UnsupportedEffect('Simple 有限图集循环')
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
    positions=np.zeros((count,3),dtype=np.float32);velocity=positions.copy();angles=positions.copy();spin=positions.copy()
    scales=np.ones((count,2));atlas=np.zeros(count,dtype=int);reverse=np.zeros(count,dtype=bool);random=SourceRandom(effect.sha256.encode()+node.data)
    model=integer('SIPT')==1
    if model:positions[:]=model_positions(emission_points(effect,integer('IJMN',-1))[0],area,count,random)
    for i in range(count):
        if not model:positions[i]=packed_vector([random.between(-a,a)for a in area],32000)
        y=1-2*random.unit();phi=random.unit()*2*math.pi;r=math.sqrt(max(0,1-y*y))
        direction=np.array((r*math.sin(phi),y,r*math.cos(phi)),dtype=np.float32)
        # Native directions are normalized, truncated to signed bytes, then
        # multiplied by the separately stored authored speed.
        direction=packed_direction(direction,100)
        parameters=random_parameters(d,random,cells[0]*cells[1])
        velocity[i]=direction*parameters['speed'];angles[i]=parameters['angles'];spin[i]=parameters['spin']
        scales[i]=parameters['scales'];atlas[i]=parameters['atlas']
        reverse[i]=bool(random.integer(2))if integer('bRUV')else False
    result={'positions':positions,'velocity':velocity,'angles':angles,'spin':spin,'scales':scales,'atlas':atlas,
        'delay':np.floor(np.arange(count)/group)*interval,'life':life,'repeat':bool(integer('bCrN')),
        'damping':damping,'gravity':gravity,'curve':curve,'start':start,'end':end,'frames':keys,'colors':rgba,
        'cells':cells,'uv_interval':uv_interval,'reverse':reverse,'pivot':np.array((real('PvtX'),real('PvtY'))),
        'inherit_scale':bool(integer('bSnP')),'bind_parent':bool(integer('bBnP'))}
    for value in result.values():
        if isinstance(value,np.ndarray):value.flags.writeable=False
    return result


def geometry(effect,node,time):
    source=initial(effect,node);age=time-source['delay'];live=age>=0
    if source['repeat']:age=np.remainder(age,source['life'])
    else:live&=age<source['life']
    age=age[live];count=len(age);steps=np.floor(age*10+1e-6).astype(int)
    damping=source['damping'];displacement=np.empty((count,3));motion=np.empty((count,3))
    for axis,d in enumerate(damping):
        total=steps if d==1 else d*(1-np.power(d,steps))/(1-d)
        motion[:,axis]=total*.1
        displacement[:,axis]=source['velocity'][live,axis]*total*.1
    centers=source['positions'][live]+displacement;gravity=.5*age[:,None]**2*source['gravity']
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
    return {'positions':np.zeros((count*4,3)),'colors':np.repeat(colors,4,axis=0),'indices':indices,
        'base_uv':np.tile(uv.reshape(-1,1,2),(1,4,1)),
        'simple_centers':centers,'simple_angles':angles,'simple_sizes':sizes,'simple_pivot':source['pivot'],
        'simple_initial_positions':source['positions'][live].copy(),
        'simple_initial_velocity':source['velocity'][live].copy(),'simple_motion_factors':motion,
        'simple_birth_age':time-age,
        'simple_gravity':gravity,'simple_bind_parent':source['bind_parent'],
        'simple_inherit_scale':source['inherit_scale'],'simple_particle_count':count,
        'random_policy':'native-xorshift128-independent-stable-preview-seed'}
