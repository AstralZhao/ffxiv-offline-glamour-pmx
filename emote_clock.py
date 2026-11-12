"""Presentation time in seconds, independent of render cadence."""
import math,time


class EmoteClock:
    def __init__(self,now=time.monotonic):
        self.now=now;self.time=0.;self.duration=0.;self.speed=1.;self.repeat=False
        self.playing=False;self.suspended=False;self.started=None
        self.loop_start=None

    def sample(self):
        elapsed=0. if self.started is None else max(0.,self.now()-self.started)*self.speed
        value=self.time+elapsed
        if self.loop_start is not None and value>=self.duration:
            return self.loop_start+(value-self.loop_start)%(self.duration-self.loop_start)
        return value%self.duration if self.repeat and self.duration>0 else min(value,self.duration)

    def seek(self,value):
        if not math.isfinite(value)or not 0<=value<=self.duration:raise ValueError('动作时间越界')
        self.time=float(value);self.started=self.now()if self.playing and not self.suspended else None

    def pause(self):
        self.time=self.sample();self.playing=False;self.started=None

    def play(self):
        if self.duration<=0:return
        if self.time>=self.duration:self.time=0.
        self.playing=True;self.started=None if self.suspended else self.now()

    def suspend(self,value):
        if self.suspended==value:return
        self.time=self.sample();self.suspended=value;self.started=self.now()if self.playing and not value else None

    def set_speed(self,value):
        if value not in(.25,.5,1.,1.5,2.):raise ValueError('动作播放速度不正确')
        self.time=self.sample();self.speed=value;self.started=self.now()if self.playing and not self.suspended else None
