"""Transactional emitter clocks and event0 history for the independent preview.

Initial frozen samples use explicit one-unit prewarm calls. Playback consumes
the preview's actual request deltas, with the reviewed .1-unit carry. This is
not a reconstruction of the game's frame history or global random stream.
"""
from dataclasses import dataclass,replace
import math
import numpy as np

from effect_sample import curve,nested,number,UnsupportedEffect
from effect_curve import ClockTimes


@dataclass(frozen=True)
class Emission:
    time:float
    count:int
    first:int
    # Parameter/TRS evaluation precedes clock commit. Regular event curves
    # see the committed clocks; prewarm sees the old clocks in both phases.
    # Deferred nodes do not yet have a reviewed parent snapshot (None).
    parameter_clock:ClockTimes|None=None
    event_clock:ClockTimes|None=None


@dataclass(frozen=True)
class State:
    requested:float=0.
    elapsed:float=0.
    wrapped:float=0.
    unwrapped:float=0.
    evaluated:ClockTimes=ClockTimes(0.,0.)
    remaining:float=0.
    residual:float=0.
    generated:int=0
    ended:bool=False
    events:tuple=()


@dataclass(frozen=True)
class DelayedState:
    requested:float
    elapsed:float
    residual:float=0.
    generated:int=0
    ended:bool=False
    events:tuple=()


@dataclass(frozen=True)
class BirthSnapshot:
    age:float
    matrix_dtype:str
    matrix_bytes:bytes
    channel_values:tuple

    def parent(self):
        return np.frombuffer(self.matrix_bytes,dtype=self.matrix_dtype).reshape(4,4).copy()

    def channels(self):
        names=('scale','rotation','extra_rotation','birth_scale','birth_rotation')
        return tuple({'flags':row[0],'mode':row[1],**{name:np.asarray(value)for name,value in zip(names,row[2:])}}
            for row in self.channel_values)


@dataclass(frozen=True)
class InjectionCount:
    consumed:int=-1
    counter:int=0
    ordinals:tuple=()


def value(data,name,clocks,default):
    node=data.get(name)
    if number(nested(node),'RanT'):
        raise UnsupportedEffect('发射器随机发射曲线历史尚未接入')
    return np.float32(curve(node,clocks,default))


def dispatch(state,data,carry,at,clocks=None):
    clocks=state.evaluated if clocks is None else clocks
    count=int(np.trunc(value(data,'CrC',clocks,1)))
    if count>64:raise UnsupportedEffect('发射次数预算')
    count=max(0,count)
    event=(Emission(float(at),count,state.generated,state.evaluated,clocks),)if count else()
    return replace(state,remaining=float(np.float32(value(data,'CrI',clocks,1)+np.float32(carry))),
                   generated=state.generated+count,events=state.events+event)


def initial(data):
    # 3cd9bf sends initial event1, then event0 unconditionally. Event1's
    # per-link CrCn is handled by the caller, not by this shared interval.
    return dispatch(State(),data,0.,0.)


def advance(state,data,delta,*,prewarm=False):
    """One original-style invocation, never a floor(age/interval) expansion."""
    f=np.float32;step=f(delta);elapsed=f(f(state.elapsed)+step)
    if state.ended:return replace(state,elapsed=float(elapsed))
    cycle=f(f(state.wrapped)+step);total=f(f(state.unwrapped)+step)
    start=number(data,'LpSt');end=number(data,'LpEd')
    if end:
        if not 0<=start<end:raise ValueError('发射器循环范围不正确')
        width=f(end-start);wraps=0
        while cycle>=f(end):
            cycle=f(cycle-width);wraps+=1
            if wraps>4096:raise UnsupportedEffect('发射器循环步长预算')
    life=nested(data.get('Life'))
    if number(life,'ValR',0.,real=True):raise UnsupportedEffect('发射器随机寿命尚未接入')
    limit=number(life,'Val',-1.,real=True)
    ended=limit>=0 and cycle>f(limit)
    after=replace(state,elapsed=float(elapsed),wrapped=float(f(limit)if ended else cycle),
                  unwrapped=float(total),ended=ended)
    if ended:return after
    # +68 evaluates parameters before committing the new clocks. +70 then
    # performs regular emission with COMMITTED clocks. +60 prewarm instead
    # invokes its full +e8 callback before committing, then advances children.
    after=replace(after,evaluated=ClockTimes(state.wrapped,state.unwrapped))
    event_clocks=after.evaluated if prewarm else ClockTimes(after.wrapped,after.unwrapped)
    birth=state.elapsed if prewarm else after.elapsed
    remaining=f(state.remaining)
    if remaining>0:
        remaining=f(remaining-step);after=replace(after,remaining=float(remaining))
        if remaining<=0:after=dispatch(after,data,remaining,birth,event_clocks)
    else:
        remaining=value(data,'CrI',event_clocks,1)
        after=replace(after,remaining=float(remaining))
        if remaining>0:after=dispatch(after,data,0.,birth,event_clocks)
    return after


class EmitterHistory:
    def __init__(self):
        self.committed={};self.prepared=None;self.delays={};self.prepared_delays=None
        self.births={};self.prepared_births=None
        self.injections={};self.prepared_injections=None
    def begin(self):self.prepared={};self.prepared_delays={};self.prepared_births={};self.prepared_injections={}
    def commit(self):
        if self.prepared is not None:self.committed=self.prepared;self.prepared=None
        if self.prepared_delays is not None:self.delays=self.prepared_delays;self.prepared_delays=None
        if self.prepared_births is not None:self.births=self.prepared_births;self.prepared_births=None
        if self.prepared_injections is not None:self.injections=self.prepared_injections;self.prepared_injections=None
    def discard(self):self.prepared=None;self.prepared_delays=None;self.prepared_births=None;self.prepared_injections=None

    def retain_injection(self,identity):
        if self.prepared_injections is None:raise ValueError('Injection count requires a prepared frame')
        old=self.injections.get(identity,InjectionCount())
        self.prepared_injections.setdefault(identity,replace(old,ordinals=()))
        if len(self.prepared_injections)>512:raise UnsupportedEffect('累计注入链接预算')

    def injection_ordinal(self,identity,sequence,accepted):
        """Probability is tested BEFORE the signed16 BIA counter increments.

        Keep cumulative counts and only currently requested live ordinals.
        The caller supplies a reviewed constant-schedule trial resolver, not
        a guessed reconstruction of a varying or looping emission history.
        """
        if self.prepared_injections is None:raise ValueError('Injection count requires a prepared frame')
        state=self.prepared_injections.get(identity,self.injections.get(identity,InjectionCount()))
        saved={**dict(self.injections.get(identity,InjectionCount()).ordinals),**dict(state.ordinals)}
        if sequence in saved and sequence<=state.consumed:
            ordinal=saved[sequence]
        else:
            if sequence<=state.consumed:state=InjectionCount()
            if sequence-state.consumed>4096:raise UnsupportedEffect('累计注入计数初始重建超过离线预算')
            counter=state.counter;ordinal=None
            for trial in range(state.consumed+1,sequence+1):
                if accepted(trial):
                    if trial==sequence:ordinal=counter
                    counter=(counter+1)&65535
            if ordinal is None:raise ValueError('Injection count requested a rejected birth')
            state=InjectionCount(sequence,counter)
        current=self.prepared_injections.get(identity)
        active=dict(current.ordinals)if current is not None else{}
        active[sequence]=ordinal
        if len(active)>512:raise UnsupportedEffect('累计注入存活计数预算')
        self.prepared_injections[identity]=replace(state,ordinals=tuple(active.items()))
        if len(self.prepared_injections)>512:raise UnsupportedEffect('累计注入链接预算')
        return ordinal

    def birth_snapshot(self,identity,age,resolve):
        """Keep the actual prepared ancestor state, not a future age re-query.

        Only instances visited by this prepared frame survive commit. Byte
        packets keep equality/camera checks deterministic and do not retain
        callbacks, source graphs or matrices from every intervening update.
        """
        if self.prepared_births is None:raise ValueError('Birth snapshot requires a prepared frame')
        if not math.isfinite(age)or age<0:raise ValueError('Invalid birth snapshot age')
        old=self.births.get(identity)
        if old is not None and age>=old.age:
            saved=replace(old,age=float(age))
        else:
            parent,channels=resolve();parent=np.asarray(parent)
            if parent.shape!=(4,4)or parent.dtype.kind!='f'or not np.isfinite(parent).all():raise ValueError('Invalid ancestor birth matrix')
            if len(channels)>13:raise UnsupportedEffect('出生继承渠道预算')
            names=('scale','rotation','extra_rotation','birth_scale','birth_rotation');rows=[]
            for node in channels:
                vectors=tuple(tuple(map(float,node[name]))for name in names)
                if any(len(v)!=3 or not np.isfinite(v).all()for v in vectors):raise ValueError('Invalid ancestor birth channels')
                rows.append((int(node['flags']),int(node['mode']),*vectors))
            saved=BirthSnapshot(float(age),parent.dtype.str,parent.tobytes(),tuple(rows))
        self.prepared_births[identity]=saved
        if len(self.prepared_births)>512:raise UnsupportedEffect('出生父级快照预算')
        return saved

    def delayed(self,identity,age,count,delay,staggered,max_age):
        if self.prepared_delays is None:raise ValueError('Delayed emitter requires a prepared frame')
        if not math.isfinite(age)or age<0 or not 0<=count<=64 or delay<=0:raise ValueError('Invalid delayed source link')
        f=np.float32;old=self.delays.get(identity)
        if old is None or age<old.requested:
            # Delayed-node +e8 is RET: null prewarm advances its clock, but
            # births occur only at the following normal +70 preparation.
            state=DelayedState(float(age),float(f(age)));update=True
        else:
            available=f(f(age-old.requested)+f(old.residual))
            delta=f(f(np.trunc(f(available*f(10))))/f(10))
            state=replace(old,requested=float(age),elapsed=float(f(f(old.elapsed)+delta)),residual=float(f(available-delta)))
            update=age>old.requested
        if update and not state.ended:
            threshold=delay*state.generated if staggered else delay
            if f(threshold)<=f(state.elapsed):
                n=1 if staggered else count
                generated=state.generated+n
                state=replace(state,generated=generated,ended=generated>=count,
                    events=state.events+((Emission(state.elapsed,n,state.generated),)if n else()))
        if max_age>=0:state=replace(state,events=tuple(e for e in state.events if e.time>=state.elapsed-max_age))
        self.prepared_delays[identity]=state
        if len(self.prepared_delays)>512:raise UnsupportedEffect('延迟发射器实例预算')
        return state

    def sample(self,identity,data,age,max_age):
        if self.prepared is None:raise ValueError('Emitter state requires a prepared frame')
        if not math.isfinite(age)or age<0:raise ValueError('Invalid emitter age')
        old=self.committed.get(identity);state=old
        reset=old is None or age<old.requested
        if reset:
            state=initial(data);units=math.trunc(age)
            if units>4096:raise UnsupportedEffect('发射器初始预推进超过离线预算')
            deltas=(1.,)*units+((float(np.float32(age-units)),)if age>units else())
        else:
            f=np.float32;available=f(f(age-old.requested)+f(old.residual))
            delta=f(f(np.trunc(f(available*f(10))))/f(10))
            state=replace(old,residual=float(f(available-delta)))
            deltas=(float(delta),)if age>old.requested else()
        for delta in deltas:
            state=advance(state,data,delta,prewarm=reset)
            # Keep live births, plus cumulative counters and the current
            # clock/countdown. No ever-growing per-frame log is retained.
            if max_age>=0:
                cutoff=state.elapsed-max_age
                state=replace(state,events=tuple(e for e in state.events if e.time>=cutoff))
            if sum(e.count for e in state.events)>512:raise UnsupportedEffect('发射器存活出生预算')
        if reset:
            # One normal zero-delta prepare after null prewarm; no synthetic
            # elapsed time or extra child movement is introduced.
            state=advance(state,data,0.)
        if sum(e.count for e in state.events)>512:raise UnsupportedEffect('发射器存活出生预算')
        state=replace(state,requested=float(age));self.prepared[identity]=state
        if len(self.prepared)>256:raise UnsupportedEffect('发射器实例状态预算')
        return state
