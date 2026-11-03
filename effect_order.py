"""Reviewed CN per-document AVFX draw order; no change to particle simulation."""
import math

import numpy as np

from effect_sample import UnsupportedEffect


def depth_key(view_row,position,sequence):
    # CN3e6eeb..3e6f57: float32 Y+X+Z+translation, *1000,
    # truncate to int64, biased 48-bit depth then low16 submission serial.
    row=np.asarray(view_row,dtype=np.float32);point=np.asarray(position,dtype=np.float32)
    depth=np.float32(np.float32(row[1]*point[1])+np.float32(row[0]*point[0]))
    depth=np.float32(depth+np.float32(row[2]*point[2]))
    depth=np.float32(depth+row[3]);scaled=np.float32(depth*np.float32(1000))
    if not math.isfinite(float(scaled))or abs(float(scaled))>=2**63:
        raise UnsupportedEffect('特效排序深度超出已审阅范围')
    return (((int(scaled)+0x7fffffffffff)<<16)|(int(sequence)&65535))&0xffffffffffffffff


def ordered_samples(samples,view):
    # Native3b1e00/3b2020/3b2250 iterate16 priority lists separately.
    # Inter-document order remains the existing source encounter order;
    # it is NOT claimed to reproduce the game's whole-frame submission.
    buckets={}
    for sequence,sample in enumerate(samples):
        mode=sample.get('draw_order',0)
        if mode not in(0,1,2):raise UnsupportedEffect('未审阅的特效绘制顺序')
        document=sample.get('effect_instance',sample.get('effect_path',''))
        buckets.setdefault(sample['priority'],{}).setdefault(document,[]).append((sequence,sample))
    result=[]
    for priority in sorted(buckets):
        for entries in buckets[priority].values():
            modes={sample.get('draw_order',0)for _,sample in entries}
            if len(modes)!=1:raise ValueError('One effect document has inconsistent draw order')
            mode=modes.pop()
            if mode==1:entries=entries[::-1]
            elif mode==2:
                def key(entry):
                    sequence,sample=entry
                    position=(sample['parent']@np.r_[sample['local'][:3,3],1])[:3]
                    return depth_key(view[2],position,sequence)
                entries=sorted(entries,key=key)
            result.extend(sample for _,sample in entries)
    return result
