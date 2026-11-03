"""Explicit CN Simple old-buffer -> new-buffer state updates.

This kernel consumes supplied update events. It never invents a frame rate,
reconstructs elapsed history from final age, or chooses a playback multiplier.
Scope: ordinary packed birth, no model-sequential injection/follow/atlas limit
or random lifetime. Those source branches require their own state inputs.
"""
from dataclasses import dataclass
import math

import numpy as np


PARTICLE=np.dtype({'names':('age','position','birth_position','velocity','speed','length','delay','packed_position','packed_velocity','state'),
    'formats':('<f4',('<f4',3),('<f4',3),('<f4',3),'<f4','<f4','<i2',('<i2',3),('i1',3),'u1'),
    'offsets':(0,4,16,28,40,44,52,68,74,79),'itemsize':80})


@dataclass(frozen=True)
class Update:
    delta:float
    advance:bool=True
    matrix:object=None
    bound:bool=True


def _particles(packets):
    if type(packets)is not bytes or len(packets)%80 or len(packets)>512*80:
        raise ValueError('Simple state requires at most 512 complete 80-byte packets')
    # Copy raw bytes, including opaque/padding fields; a field-wise structured
    # array copy is not a guarantee that all 80 bytes are retained.
    array=np.frombuffer(bytearray(packets),dtype=PARTICLE)
    if np.any(array['state']>2):raise ValueError('Simple state outside waiting/live/dead branch')
    for name in('age','position','birth_position','velocity','speed','length'):
        if not np.isfinite(array[name]).all():raise ValueError('Non-finite Simple state')
    return array


def _matrix(value):
    if value is None:raise ValueError('Unbound Simple birth requires an explicit birth matrix')
    result=np.asarray(value,dtype='<f4')
    if result.shape!=(4,4)or not np.isfinite(result).all()or not np.array_equal(result[3],(0,0,0,1)):
        raise ValueError('Simple birth requires a finite affine matrix')
    return result


def _transform(values,matrix,point):
    # Native 3fce98, float32 X+Y, then Z, then translation. No FMA.
    result=(values[:,0,None]*matrix[:3,0]+values[:,1,None]*matrix[:3,1])+values[:,2,None]*matrix[:3,2]
    return result+matrix[:3,3]if point else result


def _birth(array,selected,matrix,bound):
    f=np.float32
    position=(array['packed_position'][selected].astype('<f4')/f(32000))*array['length'][selected,None]
    velocity=(array['packed_velocity'][selected].astype('<f4')/f(100))*array['speed'][selected,None]
    if not bound:position=_transform(position,matrix,True);velocity=_transform(velocity,matrix,False)
    array['position'][selected]=position;array['birth_position'][selected]=position
    array['velocity'][selected]=velocity;array['age'][selected]=0;array['state'][selected]=1


def advance(packets,damping,delta,life,repeat,*,matrix=None,bound=True):
    """3fa3a0 + ordinary 3fcd70 birth, supplied finite delta and matrix.

    Every age update occurs before state dispatch. A waiting birth does not
    move in that same substep; a live death occurs AFTER movement. Rebirth
    discards the previous age remainder. Unwritten packet bytes survive.
    """
    array=_particles(packets);f=np.float32;damping=np.asarray(damping,dtype='<f4')
    if damping.shape!=(3,)or not np.isfinite(damping).all()or np.any(damping<0)or np.any(damping>1):
        raise ValueError('Simple damping outside supported source range')
    if type(delta)not in(int,float)or not math.isfinite(delta)or abs(delta)>1e9:
        raise ValueError('Invalid explicit Simple delta')
    if type(life)is not int or not 0<=life<=32767 or type(repeat)is not bool or type(bound)is not bool:
        raise ValueError('Invalid Simple lifetime/repeat/birth state')
    parent=None if bound else _matrix(matrix)
    left=min(f(delta),f(10));steps=0
    while left>0:
        # These masks are snapshots BEFORE any birth/death in this substep.
        # Otherwise a freshly born particle would incorrectly move immediately.
        state=array['state'].copy();array['age']=array['age']+f(.1)
        live=state==1
        array['velocity'][live]=array['velocity'][live]*damping
        array['position'][live]=array['velocity'][live]*f(.1)+array['position'][live]
        ended=live&(array['age']>=f(life))if life>0 else np.zeros(len(array),dtype=bool)
        waiting=(state==0)&(array['age']>=array['delay'].astype('<f4'))
        if repeat:_birth(array,ended,parent,bound)
        else:array['state'][ended]=2
        _birth(array,waiting,parent,bound)
        left=f(left+f(-.1));steps+=1
        if steps>128:raise ValueError('Simple source step budget exceeded')
    return array.tobytes()


def replay(packets,damping,life,repeat,updates):
    """Apply explicit successful/copy events; never infer missing events."""
    _particles(packets);result=packets
    if len(updates)>100000:raise ValueError('Explicit Simple history exceeds bound')
    for event in updates:
        if not isinstance(event,Update)or type(event.advance)is not bool:raise ValueError('Invalid Simple update event')
        if event.advance:result=advance(result,damping,event.delta,life,repeat,matrix=event.matrix,bound=event.bound)
        # The preservation-copy branch does not age, move or birth particles.
    return result
