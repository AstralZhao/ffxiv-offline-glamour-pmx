"""A session-local equipment clock; never advances character animation."""
import time


class EffectClock:
    def __init__(self,now=time.monotonic):
        self.now=now;self.frame=30.;self.started=None

    @property
    def playing(self):return self.started is not None

    def sample(self):
        return self.frame if self.started is None else self.frame+max(0,self.now()-self.started)*60

    def play(self):
        if self.started is None:self.started=self.now()

    def pause(self,frame=None):
        self.frame=self.sample()if frame is None else float(frame);self.started=None
