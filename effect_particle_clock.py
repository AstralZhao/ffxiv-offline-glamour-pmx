"""Ordinary particle curve clocks, separate from accumulated Simple state."""
import numpy as np
from effect_curve import ClockTimes
from effect_sample import number,nested,UnsupportedEffect


def looping(data):
    end=number(data,'LpEd')
    if not end or number(data,'PrVT')not in(1,2,5,8,12,13):return False
    start=number(data,'LpSt')
    if not 0<=start<end<=32767:raise UnsupportedEffect('粒子循环范围尚未核实')
    life=nested(data.get('Life'))
    if number(life,'ValR',0.,real=True):raise UnsupportedEffect('循环粒子随机寿命历史尚未接入')
    limit=number(life,'Val',-1.,real=True)
    return limit<0 or end<=limit


def retention(data):
    return -1. if looping(data)else number(nested(data.get('Life')),'Val',-1.,real=True)


def clock(data,age):
    if not looping(data):return age
    f=np.float32;total=f(age);cycle=total;end=number(data,'LpEd');width=f(end-number(data,'LpSt'));count=0
    while cycle>=end:
        cycle=f(cycle-width);count+=1
        if count>4096:raise UnsupportedEffect('粒子循环离线步长预算')
    return ClockTimes(float(cycle),float(total))
