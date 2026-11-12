"""Reviewed CN scalar/RGB AVFX curve math, independent of display frame rate."""
import bisect
import math
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class ClockTimes:
    wrapped:float
    unwrapped:float


def tangent_byte(value):
    with np.errstate(over='ignore',invalid='ignore'):
        scaled=np.float32(value)*np.float32(15)
    # CVTTSS2SI's indefinite int32 has low byte0 on overflow/NaN.
    packed=int(np.trunc(scaled))if np.isfinite(scaled)and -2**31<=scaled<2**31 else 0
    return (packed+128)%256-128


def scalar_segment(keys,index,time):
    if any(not 0<=key[0]<=0x3fff for key in keys):raise ValueError('Scalar key time outside reviewed packed range')
    f=np.float32;a,b=keys[index:index+2]
    av,bv=f(a[4]),f(b[4]);span=f(b[0]-a[0]);t=f(f(f(time)-f(a[0]))/span)
    # CN397a69 selects the interpolator from the ARRIVING key's top2 bits.
    kind=b[1]
    if kind==2:return bv if f(time)>=f(b[0])else av
    delta=f(bv-av)
    if kind==1:return f(f(t*delta)+av)
    if kind!=0:raise ValueError('Unreviewed scalar AVFX key type')
    weight=lambda key,component:f(f(tangent_byte(key[component]))*f(1/15))
    end=f(weight(b,3)*delta)
    if index+2<len(keys):
        c=keys[index+2]
        end=f(f(end+f(weight(b,2)*f(f(c[4])-bv)))*f(span/f(c[0]-a[0])))
    start=f(delta*weight(a,2))
    if index>0:
        previous=keys[index-1]
        start=f(f(f(weight(a,3)*f(av-f(previous[4])))+start)*f(span/f(b[0]-previous[0])))
    t2=f(t*t);t3=f(t2*t);three=f(t2*f(3));twice=f(t3+t3)
    h00=f(f(twice-three)+f(1));h01=f(three-twice)
    h10=f(f(t3-f(t2+t2))+t);h11=f(t3-t2)
    return f(f(f(f(h00*av)+f(h01*bv))+f(h10*start))+f(h11*end))


def scalar_value(keys,time,pre=0,post=0):
    """CN scalar time lookup and hold/repeat/offset boundaries.

    Input time is already the chosen source clock. This function does not
    establish the runtime mapping between absolute and normalized clocks.
    """
    f=np.float32;time=f(time)
    if not np.isfinite(time):raise ValueError('Non-finite scalar curve time')
    # CN3974dc..397502 stores the source signed16 time in only 14 bits.
    # Preserve source order, including native boundary priority for a packed
    # first key later than its last key. Do not sort or clamp authored keys.
    if any(not 0<=k[0]<=0x3fff for k in keys):
        if any(not -32768<=k[0]<=32767 for k in keys):raise ValueError('Scalar source time outside signed16 range')
        keys=tuple((k[0]&0x3fff,*k[1:])for k in keys)
    if len(keys)==1:return f(keys[0][4])
    start,end=keys[0][0],keys[-1][0];offset=None;before=False
    if time<start or time>end:
        before=time<start;mode=pre if before else post
        if mode in(0,-1):return f(keys[0 if before else -1][4])
        if mode not in(1,2):raise ValueError('Unreviewed scalar curve boundary mode')
        if end<=start:raise ValueError('Repeated scalar packed interval is not increasing')
        span=f(end-start);distance=f(f(start)-time)if before else f(time-f(end))
        # CN397d90/397f50 first truncate the float distance to int32,
        # then divide by the integer period and add1. Exact negative
        # periods map to the LAST key, unlike floor/modulo wrapping.
        if distance>=2**31:raise ValueError('Scalar curve wrap exceeds reviewed int32 range')
        turns=int(np.trunc(distance))//int(span)+1
        shift=f(f(turns)*span);time=f(time+shift)if before else f(time-shift)
        if mode==2:
            delta=f(f(keys[-1][4])-f(keys[0][4]));offset=f(delta*f(turns))
    # CN397770 searches strictly BEFORE ceilf(time). At an exact key,
    # evaluate the arriving segment, including its step/float32 endpoint.
    if any(a[0]>=b[0]for a,b in zip(keys,keys[1:])):
        raise ValueError('Scalar packed interior interval is not increasing')
    index=max(0,bisect.bisect_left([k[0]for k in keys],math.ceil(float(time)))-1)
    if index>=len(keys)-1:raise ValueError('Scalar time lookup outside reviewed key interval')
    result=scalar_segment(keys,index,time)
    # Native interior/hold paths do not perform an extra +0 operation.
    return result if offset is None else f(result-offset)if before else f(result+offset)


def rgb_value(keys,time,pre=0,post=0):
    """CN RGB curves have linear0/1, step2 and repeat1/2 aliases.

    Unlike scalar curves, RGB checks its boundary handlers at equality and
    has no cumulative-value offset for source boundary mode2.
    """
    f=np.float32;time=f(time)
    if not np.isfinite(time):raise ValueError('Non-finite RGB curve time')
    if any(not 0<=k[0]<=0x7fff for k in keys):raise ValueError('RGB key time outside reviewed source range')
    value=lambda k:np.asarray(k[2:],dtype='<f4')
    if len(keys)==1:return value(keys[0])
    start,end=keys[0][0],keys[-1][0]
    if time<=start or time>=end:
        before=time<=start;mode=pre if before else post
        if mode in(0,-1):return value(keys[0 if before else -1])
        if mode not in(1,2):raise ValueError('Unreviewed RGB curve boundary mode')
        span=f(end-start);distance=f(f(start)-time)if before else f(time-f(end))
        if distance>=2**31:raise ValueError('RGB curve wrap exceeds reviewed int32 range')
        turns=int(np.trunc(distance))//int(span)+1
        shift=f(f(turns)*span);time=f(time+shift)if before else f(time-shift)
    # Original39afb0 accepts both interval endpoints. Follow its binary
    # search so exact keys do not choose a different float32 calculation.
    low,high=0,len(keys)-1;index=None
    while low<=high:
        middle=low+(high-low)//2
        if f(keys[middle][0])>time:high=middle-1
        else:
            low=middle+1
            if middle+1<len(keys)and time<=f(keys[middle+1][0]):index=middle;break
    if index is None:raise ValueError('RGB time lookup outside reviewed key interval')
    a,b=keys[index:index+2];kind=b[1]
    if kind==2:return value(b if time>=f(b[0])else a)
    if kind not in(0,1):raise ValueError('Unreviewed RGB AVFX key type')
    t=f(f(time-f(a[0]))/f(b[0]-a[0]));av,bv=value(a),value(b)
    return ((bv-av)*t+av).astype('<f4')
