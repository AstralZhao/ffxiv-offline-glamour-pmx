"""Original CN EID attachment transforms; no equipment-specific offsets."""
from dataclasses import dataclass
import hashlib
import struct
import json
from pathlib import Path
from functools import lru_cache
from source_executable import executable_sha256
from renderer_reference import renderer_reference

import numpy as np

from effect_sample import rotation,UnsupportedEffect


def attachment_scale(scales,vfx,bias,separate):
    """CN3c0280 / 3c0461+3c0487; float32 operations, no fitted multiplier."""
    scales=np.asarray(scales,dtype='<f4');vfx=np.float32(vfx);bias=np.float32(bias)
    if scales.shape!=(3,)or not np.isfinite(scales).all()or not np.isfinite((vfx,bias)).all():
        raise ValueError('Invalid attachment scale')
    if separate:return scales*((vfx-np.float32(1))*bias+np.float32(1))
    return (scales*vfx-np.float32(1))*bias+np.float32(1)


@lru_cache(maxsize=4)
def character_vfx_scale(game_root,race):
    from cn_offscreen_lighting import rva_bytes
    profile=json.loads((Path(__file__).resolve().parents[1]/'docs/cn-equipment-effect-rules.json').read_text(encoding='utf8'))
    from ff14_readonly import SqPack
    pack=SqPack(Path(game_root));exe=renderer_reference()
    if executable_sha256(exe)!=profile['executable_sha256']:raise ValueError('Unreviewed CN VFX scale source')
    for name in('character_scale','character_scale_callback','separate_scale','combined_scale'):
        entry=profile['native_ranges'][name]
        if hashlib.sha256(rva_bytes(exe,int(entry['rva'],16),entry['bytes'])).hexdigest()!=entry['sha256']:
            raise ValueError('Original CN attachment scale changed')
    if str(race)not in profile['character_scales']:raise UnsupportedEffect('未审阅的角色特效尺度')
    return profile['character_scales'][str(race)]


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
        self.race_sex_id=race_sex_id
        self.points=None;self.sha256=None

    def vfx_scale(self):return character_vfx_scale(str(self.pack.game_root),self.race_sex_id)

    def resolve(self,identifier,bones):
        if self.points is None:
            try:raw=self.pack.read(self.path)
            except FileNotFoundError as error:
                if str(error)!=self.path:raise
                raise UnsupportedEffect('缺少角色附着点来源 '+self.path)from error
            self.points=read_bind_points(raw);self.sha256=hashlib.sha256(raw).hexdigest()
        if identifier not in self.points:raise UnsupportedEffect('EID 未定义附着点 '+str(identifier))
        return self.points[identifier].world(bones)
