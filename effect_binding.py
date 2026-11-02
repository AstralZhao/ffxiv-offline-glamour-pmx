"""Original CN EID attachment transforms; no equipment-specific offsets."""
from dataclasses import dataclass
import hashlib
import struct

import numpy as np

from effect_sample import rotation,UnsupportedEffect


@dataclass(frozen=True)
class BindPoint:
    name:str
    position:tuple
    angles:tuple

    def world(self,bones):
        if self.name not in bones:raise UnsupportedEffect('附着点骨骼未接入 '+self.name)
        # CN25beb0: row Rx -> Ry -> Rz, then translation, then local @ bone.
        # Our matrices multiply column vectors, so transpose the whole chain.
        local=np.eye(4);local[:3,:3]=rotation(self.angles,0);local[:3,3]=self.position
        return bones[self.name]@local


def read_bind_points(raw):
    if len(raw)<16:raise ValueError('Truncated EID header')
    magic,version,revision,count,reserved=struct.unpack_from('<4sHHII',raw)
    if magic!=b'die\0'or(version,revision)!=(0x3132,0x3130):
        raise UnsupportedEffect('尚未接入的 EID 来源版本')
    if reserved or count>4096 or len(raw)!=16+64*count:raise ValueError('Invalid EID source size/count')
    result={}
    for index in range(count):
        at=16+64*index;name=raw[at:at+32].split(b'\0',1)[0].decode('ascii')
        identifier,*values=struct.unpack_from('<I6f',raw,at+32)
        if not name or identifier in result or not np.isfinite(values).all():raise ValueError('Invalid EID attachment record')
        result[identifier]=BindPoint(name,tuple(values[:3]),tuple(values[3:]))
    return result


class EffectBindPoints:
    def __init__(self,pack,race_sex_id):
        if type(race_sex_id)is not int or not 0<race_sex_id<=9999:raise ValueError('Invalid EID character identity')
        self.pack=pack;self.path=f'chara/human/c{race_sex_id:04}/skeleton/base/b0001/eid_c{race_sex_id:04}b0001.eid'
        self.points=None;self.sha256=None

    def resolve(self,identifier,bones):
        if self.points is None:
            try:raw=self.pack.read(self.path)
            except FileNotFoundError as error:
                if str(error)!=self.path:raise
                raise UnsupportedEffect('缺少角色附着点来源 '+self.path)from error
            self.points=read_bind_points(raw);self.sha256=hashlib.sha256(raw).hexdigest()
        if identifier not in self.points:raise UnsupportedEffect('EID 未定义附着点 '+str(identifier))
        return self.points[identifier].world(bones)
