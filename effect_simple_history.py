"""Preview-owned Simple state; original prewarm and explicit render updates.

The frozen preview requests native-style integer prewarm, not a guessed game
FPS. Subsequent updates consume this preview's supplied elapsed source time.
Prepared states commit only after a successful render; no full history grows.
"""
import math

import numpy as np

from effect_sample import nested,UnsupportedEffect
from effect_simple import initial,StateInput
from effect_simple_state import advance


class SimpleHistory:
    def __init__(self,effect):
        self.effect=effect;self.committed={};self.prepared=None
        from effect_emitter_history import EmitterHistory
        self.emitters=EmitterHistory()
        from effect_particle_motion import MotionHistory
        self.motion=MotionHistory()

    def begin(self):self.prepared={};self.emitters.begin();self.motion.begin()

    def sample(self,identity,particle,age,matrix_at):
        if self.prepared is None:raise ValueError('Simple state requires a prepared frame')
        if not math.isfinite(age)or age<0:raise ValueError('Invalid Simple instance age')
        node=nested(particle)['Smpl'];source=initial(self.effect,node);key=(identity,node)
        previous=self.committed.get(key);bound=source['bind_parent']
        def update(packet,delta,when):
            return advance(packet,source['damping'],float(delta),source['life'],source['repeat'],
                bound=bound,matrix=None if bound else matrix_at(when),atlas=source['atlas_limit'])
        if previous is None or age<previous['age']:
            # Original document prewarm3bb894..3bb8c1 truncates its requested
            # warmup and calls +60 with1 per iteration. Direct +60 -> +e8
            # reaches Simple3fa3a0. The preview chooses a frozen source phase,
            # not an alleged original UI frame rate or FViewer multiplier.
            units=math.trunc(age)
            if units>4096:raise UnsupportedEffect('Simple初始预推进超过离线预算')
            packets=source['initial_state'];seen={};at=0
            while at<units:
                # Bound ordinary state is independent of current parent TRS.
                # Skip only an EXACT repeated whole state; unbound moving
                # parents must retain every supplied birth-matrix event.
                if bound and packets in seen:
                    cycle=at-seen[packets];skip=(units-at)//cycle
                    if skip:at+=skip*cycle;continue
                elif bound and (len(seen)+1)*len(packets)<=16*1024*1024:seen[packets]=at
                packets=update(packets,1.,at);at+=1
            remainder=np.float32(age-units)
            if remainder>0:packets=update(packets,remainder,age)
            residual=np.float32(0)
        else:
            packets=previous['packets'];residual=previous['residual']
            # Input is the actual preview request delta in source units. Use
            # the native .1-unit carried quantization, never floor total age.
            elapsed=np.float32(age-previous['age']);value=np.float32(elapsed+residual)
            delta=np.float32(np.float32(np.trunc(np.float32(value*np.float32(10))))/np.float32(10))
            residual=np.float32(value-delta)
            if delta>0:packets=update(packets,delta,age)
        self.prepared[key]={'packets':packets,'age':age,'residual':residual}
        if len(self.prepared)>128:raise UnsupportedEffect('Simple实例状态预算')
        return StateInput((self.effect.sha256,node),packets)

    def commit(self):
        if self.prepared is not None:self.committed=self.prepared;self.prepared=None
        self.emitters.commit()
        self.motion.commit()

    def discard(self):self.prepared=None;self.emitters.discard();self.motion.discard()
