"""A session-local equipment clock; never advances character animation."""
import time

# CN Framework +16c0 seconds -> 3939a0 uses original constant211bcb0=30,
# then carries .1-unit quantization remainder. Core config60 preserves that
# rate. This presentation clock samples elapsed time; it does not fabricate
# native per-update history for the separate, still-pending Simple simulator.
SOURCE_UNITS_PER_SECOND=30.


class EffectClock:
    def __init__(self,now=time.monotonic):
        self.now=now;self.frame=30.;self.started=None

    @property
    def playing(self):return self.started is not None

    def sample(self):
        return self.frame if self.started is None else self.frame+max(0,self.now()-self.started)*SOURCE_UNITS_PER_SECOND

    def play(self):
        if self.started is None:self.started=self.now()

    def pause(self,frame=None):
        self.frame=self.sample()if frame is None else float(frame);self.started=None
