"""Ordinary particle motion, distinct from the Simple .1 substep integrator.

Clocks and supplied update deltas belong to the preview. ARs scales each
increment; gravity has its own velocity/offset and is added in world Y after
coordinate assembly. No game FPS or missing update history is inferred.
"""
from dataclasses import dataclass,replace
import math

import numpy as np

from effect_curve import ClockTimes
from effect_sample import curve,curve_source,nested,number,rotation,UnsupportedEffect
from effect_emitter import motion_step
from effect_coordinates import compose,multiply


def needed(data,velocity):
    if np.any(velocity):return True
    for name in('Gra','VRX','VRY','VRZ'):
        source,keys=curve_source(data.get(name))
        if number(source,'RanT')or keys and any(k[4]!=0 for k in keys):return True
    return False


def rotated(data):
    for name in('VRX','VRY','VRZ'):
        source,keys=curve_source(data.get(name))
        if number(source,'RanT')or keys and any(k[4]!=0 for k in keys):return True
    return False


def coordinates(state,scale,angles,position,extra,origin,parent,order,rotation_order):
    """Reviewed mode2/3 prefix then world-Y gravity tail (3e471c)."""
    f=np.float32
    own_angles=f(np.asarray(angles,dtype='<f4')+np.asarray(extra,dtype='<f4'))
    own_position=f(np.asarray(position,dtype='<f4')+np.asarray(state.travel,dtype='<f4'))
    local=compose(scale,rotation(own_angles,rotation_order),own_position,order)
    local[:3,3]=f(local[:3,3]+np.asarray(origin,dtype='<f4'))
    world=multiply(local,parent)
    world[1,3]=f(f(state.gravity_offset)+world[1,3])
    return world


def selective_coordinates(state,scale,rotate,position,origin,order,basis,local_direction,auxiliary=None):
    """Mode0/1: own TRS, optional birth basis, binder, origin, gravity."""
    f=np.float32
    pos=f(np.asarray(position,dtype='<f4')+np.asarray(state.travel,dtype='<f4'))
    result=compose(scale,rotate,pos,order)
    if local_direction==1:
        birth=np.eye(4,dtype='<f4');birth[:3,:3]=basis;result=multiply(result,birth)
    if auxiliary is not None:result=multiply(result,auxiliary)
    result[:3,3]=f(result[:3,3]+np.asarray(origin,dtype='<f4'))
    result[1,3]=f(f(state.gravity_offset)+result[1,3])
    return result


@dataclass(frozen=True)
class Motion:
    wrapped:float=0.
    unwrapped:float=0.
    elapsed:float=0.
    requested:float=0.
    residual:float=0.
    evaluated:ClockTimes=ClockTimes(0.,0.)
    travel:tuple=(0.,0.,0.)
    gravity_velocity:float=0.
    gravity_offset:float=0.
    ended:bool=False


def scalar(data,name,clocks,default):
    if number(nested(data.get(name)),'RanT'):
        raise UnsupportedEffect('普通粒子随机运动曲线历史尚未接入')
    return np.float32(curve(data.get(name),clocks,default))


def advance(state,data,velocity,delta,*,prewarm=False,basis=None,local_direction=0):
    """One +60, or +68 followed by +70/+78; no synthetic substeps."""
    f=np.float32;delta=f(delta)
    if not np.isfinite(delta)or delta<0:raise ValueError('Invalid ordinary motion delta')
    elapsed=f(f(state.elapsed)+delta)
    if state.ended:return replace(state,elapsed=float(elapsed))
    wrapped=f(f(state.wrapped)+delta);unwrapped=f(f(state.unwrapped)+delta)
    end=number(data,'LpEd');start=number(data,'LpSt')
    if end:
        if not 0<=start<end<=32767:raise UnsupportedEffect('粒子循环范围尚未核实')
        turns=0
        while wrapped>=f(end):
            wrapped=f(wrapped-f(end-start));turns+=1
            if turns>4096:raise UnsupportedEffect('粒子循环离线步长预算')
    life=nested(data.get('Life'))
    if number(life,'ValR',0.,real=True):raise UnsupportedEffect('普通粒子随机寿命历史尚未接入')
    limit=number(life,'Val',-1.,real=True)
    ended=limit>=0 and wrapped>f(limit)
    old=ClockTimes(state.wrapped,state.unwrapped)
    after=replace(state,elapsed=float(elapsed),wrapped=float(f(limit)if ended else wrapped),
                  unwrapped=float(unwrapped),ended=ended)
    if ended:return after
    # Actual Windmill/Shape +e8 is RET. +60 commits their clocks without
    # advancing ordinary motion or evaluating authored TRS in that phase.
    if prewarm and number(data,'PrVT')in(2,8):return after
    clocks=old if prewarm else ClockTimes(after.wrapped,after.unwrapped)
    displacement=None
    if np.any(velocity):
        angles=tuple(scalar(data,name,clocks,0)for name in('VRX','VRY','VRZ'))
        if any(angles):
            if basis is None:raise UnsupportedEffect('普通粒子旋转速度缺少已核实出生方向')
            from effect_velocity_rotation import increment
            displacement=increment(velocity,delta,angles,basis,local_direction)
    # Original 3e71b0: semi-implicit, not .5*g*t*t and not Simple gravity.
    gravity_velocity=f(f(delta*scalar(data,'Gra',clocks,0))+f(state.gravity_velocity))
    gravity_offset=f(f(gravity_velocity*delta)+f(state.gravity_offset))
    resistance=scalar(data,'ARs',clocks,1)
    travel=motion_step(state.travel,velocity,delta,resistance)if displacement is None else f(
        np.asarray(state.travel,dtype='<f4')+f(displacement*resistance))
    return replace(after,evaluated=old,travel=tuple(map(float,travel)),
        gravity_velocity=float(gravity_velocity),gravity_offset=float(gravity_offset))


class MotionHistory:
    """Bounded per-live-instance snapshots, committed only after rendering."""
    def __init__(self):self.committed={};self.prepared=None
    def begin(self):self.prepared={}
    def commit(self):
        if self.prepared is not None:self.committed=self.prepared;self.prepared=None
    def discard(self):self.prepared=None

    def sample(self,identity,data,age,velocity,*,direction=None,local_direction=0,birth_parent=None):
        if self.prepared is None:raise ValueError('Ordinary motion requires a prepared frame')
        if not math.isfinite(age)or age<0:raise ValueError('Invalid ordinary particle age')
        velocity=np.asarray(velocity,dtype='<f4')
        if velocity.shape!=(3,)or not np.isfinite(velocity).all():raise ValueError('Invalid ordinary initial velocity')
        basis=None
        if direction is not None:
            from effect_velocity_rotation import construct
            velocity,basis=construct(velocity,direction,local_direction,birth_parent)
        signature=(velocity.tobytes(),None if basis is None else basis.tobytes(),local_direction);old=self.committed.get(identity)
        reset=old is None or age<old[0].requested or signature!=old[1]
        f=np.float32
        if reset:
            state=Motion();units=math.trunc(age)
            if units>4096:raise UnsupportedEffect('普通粒子初始预推进超过离线预算')
            for _ in range(units):state=advance(state,data,velocity,1.,prewarm=True,basis=basis,local_direction=local_direction)
            if age>units:state=advance(state,data,velocity,float(f(age-units)),prewarm=True,basis=basis,local_direction=local_direction)
            state=advance(state,data,velocity,0.,basis=basis,local_direction=local_direction)
        else:
            state=old[0]
            if age>state.requested:
                available=f(f(age-state.requested)+f(state.residual))
                delta=f(f(np.trunc(f(available*f(10))))/f(10))
                state=replace(advance(state,data,velocity,float(delta),basis=basis,local_direction=local_direction),residual=float(f(available-delta)))
        state=replace(state,requested=float(age));self.prepared[identity]=(state,signature)
        if len(self.prepared)>512:raise UnsupportedEffect('普通粒子运动状态预算')
        return state
