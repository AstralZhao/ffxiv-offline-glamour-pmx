"""Weapon idle AVFX events, retaining live children across source loop wraps.

CN 3b0b70/3bf840 rearms the timeline items without deleting its children.
RTRG 3c7480 selects a ZERO-based trigger with xorshift128's low 16 bits.
The preview supplies stable independent seeds, not the game's global RNG state.
"""
from dataclasses import dataclass
from functools import lru_cache
import math,struct
from effect_sample import fields,number,items,UnsupportedEffect
from vfx import records


@dataclass(frozen=True)
class TimelineEvent:
    timeline:int
    item:int
    start:float
    end:float
    binder:int
    identity:tuple


@lru_cache(maxsize=256)
def _timeline(node):
    data=records(node.data)
    head=fields(r for r in data if r.tag not in('Item','Clip'))
    clips=tuple(r.data for r in data if r.tag=='Clip')
    if len(clips)!=number(head,'CpCn')or any(len(c)!=164 for c in clips):
        raise ValueError('武器时间线控制片段布局不正确')
    return head,items(data,'Item','bEna',number(head,'TICn')),clips


def trigger_choice(seed,low,high):
    from effect_simple import SourceRandom
    if not 0<=low<=high<128:raise UnsupportedEffect('武器随机触发范围尚未核实')
    return low+SourceRandom(seed).integer(high-low+1)


def events(effect,entries,frame):
    """Expand source loop births, not a modulo-wrapped emitter age.

    The caller establishes the already settled drawn/sheathed root epoch.
    Finite item intervals prune old loop instances, never the current epoch's
    earlier children. Budgets fail explicitly instead of truncating the effect.
    """
    if not math.isfinite(frame)or frame<0:raise ValueError('武器特效时间不正确')
    timelines=tuple(r for r in effect.source_records if r.tag=='TmLn')
    schedulers=tuple(r for r in effect.source_records if r.tag=='Schd')
    if len(schedulers)!=1:raise UnsupportedEffect('武器多调度器尚未接入')
    schedule=records(schedulers[0].data)
    head=fields(r for r in schedule if r.tag not in('Item','Trgr'))
    normal_count=number(head,'ItCn')
    triggers=items(schedule,'Trgr','bEna',normal_count+number(head,'TrCn'))[normal_count:]
    result=[];visited=0
    def expand(index,epoch,identity,triggered=False,parents=()):
        nonlocal visited
        visited+=1
        if visited>512 or len(parents)>16 or index in parents:
            raise UnsupportedEffect('武器时间线触发循环或预算')
        if not 0<=index<len(timelines):raise ValueError('武器时间线索引越界')
        top,rows,clips=_timeline(timelines[index])
        loop_start=number(top,'LpSt');loop_end=number(top,'LpEd')
        if loop_end and not 0<=loop_start<loop_end:raise ValueError('武器时间线循环范围不正确')
        if loop_end and loop_start:raise UnsupportedEffect('非零起点的武器时间线重装尚未核实')
        # Native trigger creation 3bd5b0 uses the scheduler's parent matrix;
        # normal creation 3bd14d..3bd1f8 alone attaches the timeline BnNo.
        inherited_binder=-1 if triggered else number(top,'BnNo',-1)
        for ordinal,row in enumerate(rows):
            if not number(row,'bEna',1):continue
            begin=number(row,'StTm');end=number(row,'EdTm',-1)
            if loop_end and begin>=loop_end:
                # The clock wraps BEFORE dispatch; this item cannot fire.
                continue
            if not loop_end:cycles=range(1)
            else:
                last=math.floor((frame-epoch-begin)/loop_end)
                first=0 if end<0 else max(0,math.floor((frame-epoch-end)/loop_end)+1)
                if last-first>512:raise UnsupportedEffect('武器时间线存活实例预算')
                cycles=range(first,last+1)
            for cycle in cycles:
                start=epoch+cycle*loop_end+begin
                if frame<start:continue
                stop=-1. if end<0 else epoch+cycle*loop_end+end
                emitter=number(row,'EmNo',-1);effector=number(row,'EfNo',-1)
                key=identity+(index,ordinal,cycle)
                if emitter>=0 or effector>=0:
                    if stop>=0 and frame>=stop:continue
                    binder=number(row,'BdNo',-1)
                    if binder<0:binder=inherited_binder
                    if len(result)>=512:raise UnsupportedEffect('武器时间线存活实例预算')
                    result.append(TimelineEvent(index,ordinal,start,stop,binder,key))
                    continue
                clip_index=number(row,'ClNo',-1)
                if not 0<=clip_index<len(clips):raise ValueError('武器时间线片段索引越界')
                kind,*values=struct.unpack_from('<I4i4f',clips[clip_index])
                if kind!=0x52545247 or any(values[2:]):
                    raise UnsupportedEffect('武器常规时间线控制尚未接入 '+hex(kind))
                chosen=trigger_choice((effect.sha256+repr(key)).encode(),values[0],values[1])
                if not 0<=chosen<len(triggers):raise ValueError('武器随机触发索引越界')
                target=triggers[chosen]
                if number(target,'TlNo',-1)>=0:
                    # 3bd5b0 does not consume the Trgr bEna/start fields.
                    expand(number(target,'TlNo'),start,key+('trigger',chosen),True,parents+(index,))
    for ordinal,(index,start)in enumerate(entries):expand(index,start,('normal',ordinal))
    return tuple(result)
