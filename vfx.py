"""Bounded original AVFX resource graph for wardrobe equipment effects.

Reads source records without modifying or inventing emitters, particles or
bindings. Source preparation is separate from the pending particle renderer.
Format reference: local pinned VFXEditor AvfxBase/AvfxMain/AvfxTexture readers.
"""
from collections import OrderedDict
from dataclasses import dataclass
import hashlib
import struct


@dataclass(frozen=True)
class Record:
    tag:str
    data:bytes


def records(data):
    if len(data)>16*1024*1024:raise ValueError('AVFX exceeds the source byte budget')
    result=[];at=0
    while at<len(data):
        if at+8>len(data):raise ValueError('Truncated AVFX record header')
        raw=data[at:at+4][::-1].lstrip(b'\0')
        if not raw or any(value<32 or value>126 for value in raw):raise ValueError('Invalid AVFX record tag')
        size=struct.unpack_from('<I',data,at+4)[0];end=at+8+size;padded=(end+3)&~3
        if end>len(data)or padded>len(data):raise ValueError('AVFX record exceeds its source container')
        result.append(Record(raw.decode('ascii'),data[at+8:end]))
        if len(result)>32768:raise ValueError('AVFX exceeds the record budget')
        at=padded
    return tuple(result)


def integer(record):
    if len(record.data)!=4:raise ValueError('AVFX integer field has the wrong size')
    return struct.unpack('<i',record.data)[0]


GROUPS=(('ScCn','Schd'),('TlCn','TmLn'),('EmCn','Emit'),('PrCn','Ptcl'),
    ('EfCn','Efct'),('BdCn','Bind'),('TxCn','Tex'),('MdCn','Modl'))


@dataclass(frozen=True)
class Effect:
    path:str
    sha256:str
    version:int
    source_records:tuple
    groups:tuple
    textures:tuple
    particle_types:tuple
    missing:bool=False

    def summary(self):
        return {'path':self.path,'sha256':self.sha256,'version':self.version,
            'groups':dict(self.groups),'textures':list(self.textures),'particle_types':list(self.particle_types),
            'missing':self.missing,'particles_rendered':False}


def read_effect(path,raw):
    if len(raw)<8 or raw[:4]!=b'XFVA'or struct.unpack_from('<I',raw,4)[0]!=len(raw)-8:
        raise ValueError('Invalid original AVFX root signature/size')
    source=records(raw[8:]);versions=[integer(r)for r in source if r.tag=='Ver']
    if len(versions)!=1:raise ValueError('AVFX requires one source version')
    groups=[];textures=[];particle_types=[]
    for count_tag,node_tag in GROUPS:
        counts=[integer(r)for r in source if r.tag==count_tag]
        nodes=[r for r in source if r.tag==node_tag]
        if len(counts)!=1 or not 0<=counts[0]<=4096 or counts[0]!=len(nodes):
            raise ValueError('AVFX source group count mismatch: '+node_tag)
        groups.append((node_tag,counts[0]))
        for node in nodes:
            if node_tag=='Tex':
                name=node.data.split(b'\0',1)[0].decode('utf8')
                if not name or name.startswith('/')or'..'in name.replace('\\','/').split('/'):
                    raise ValueError('Invalid original effect texture path')
                textures.append(name)
            elif node_tag in('Schd','TmLn','Emit','Ptcl','Efct','Bind'):
                nested=records(node.data)
                if node_tag=='Ptcl':
                    kinds=[integer(r)for r in nested if r.tag=='PrVT']
                    if len(kinds)!=1:raise ValueError('AVFX particle requires one type')
                    particle_types.append(kinds[0])
    return Effect(path,hashlib.sha256(raw).hexdigest(),versions[0],source,tuple(groups),tuple(textures),tuple(particle_types))


class EquipmentEffects:
    def __init__(self,pack):self.pack=pack;self.sources=OrderedDict()

    def prepare(self,model_set,slot,identifier):
        if type(model_set)is not int or not 0<model_set<=65535 or type(identifier)is not int or not 1<=identifier<=255:
            raise ValueError('Invalid source equipment-effect identity')
        accessory=slot in('Ears','Neck','Wrists','FingerR','FingerL')
        if not accessory and slot not in('Head','Body','Gloves','Legs','Feet'):raise ValueError('Unknown effect equipment slot')
        folder,prefix=('accessory','a')if accessory else('equipment','e')
        # CN445133..4451d4 uses the shared vfx/equipment namespace at100.
        path=(f'vfx/equipment/eff/ve{identifier:04}.avfx'if not accessory and identifier>=100 else
            f'chara/{folder}/{prefix}{model_set:04}/vfx/eff/v{prefix}{identifier:04}.avfx')
        if path in self.sources:self.sources.move_to_end(path);return self.sources[path]
        try:raw=self.pack.read(path)
        except FileNotFoundError as error:
            if str(error)!=path:raise
            result=Effect(path,'',0,(),(),(),(),True)
        else:result=read_effect(path,raw)
        self.sources[path]=result
        while len(self.sources)>16:self.sources.popitem(last=False)
        return result
