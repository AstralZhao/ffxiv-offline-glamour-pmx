"""Independent CN facewear slots over original equipment models and shaders."""
from collections import OrderedDict
import copy
from functools import lru_cache
import hashlib
import json
from pathlib import Path

from ff14_readonly import Sheet


@lru_cache(maxsize=1)
def catalogue():
    record=json.loads((Path(__file__).resolve().parents[1]/'artifacts/cn-data/facewear-catalogue.json').read_bytes())
    if record.get('version')!=1 or record.get('region')!='CN':raise ValueError('面饰目录版本不正确')
    variants={};styles={}
    for style in record['styles']:
        if style['id']in styles or style['slot']not in(0,1):raise ValueError('面饰款式或槽位不正确')
        styles[style['id']]=style
        for item in style['variants']:
            if type(item['id'])is not int or not 0<item['id']<=65535 or item['id']in variants:raise ValueError('面饰编号不正确')
            variants[item['id']]={**item,'style_id':style['id'],'slot':style['slot'],'style_name':style['name']}
    return record,styles,variants


def selection(value=None):
    if value is None:return [0,0]
    if not isinstance(value,list)or len(value)!=2:raise ValueError('面部配饰槽位格式不正确')
    if any(type(i)is not int or not 0<=i<=65535 for i in value):raise ValueError('面部配饰编号不正确')
    if any(value):
        variants=catalogue()[2]
        if any(i and(i not in variants or variants[i]['slot']!=slot)for slot,i in enumerate(value)):
            raise ValueError('面饰不属于指定槽位，或不在当前国服目录中')
    return list(value)


def template_state(state):
    result={'equipment':state['equipment']}
    if any(state.get('facewear',[0,0])):result['facewear']=selection(state['facewear'])
    return result


def native_missing_bone(pack):
    """CN model palette lookup: missing name -> j_kao -> skeleton index0.

    Only the witnessed facewear case uses the first fallback. Other renderer
    families retain their strict skeleton/EST validation.
    """
    from source_executable import source_executable,executable_sha256
    from cn_offscreen_lighting import rva_bytes
    exe=source_executable(pack)
    if executable_sha256(exe)!='958b78e278ad896493c484eb313ff605ab41edefef74f92b0ee078e69b281bf9':
        raise ValueError('面饰骨骼绑定代码版本尚未核实')
    code=rva_bytes(exe,0x267ee2,0x267f1a-0x267ee2)
    if hashlib.sha256(code).hexdigest()!='f8100afbcc22256e1f91523852d520e273b3e20eaca740f581f74591e92e7c67':
        raise ValueError('面饰原生骨骼绑定分支不一致')
    if rva_bytes(exe,0x2146a78,6)!=b'j_kao\0':raise ValueError('面饰原生骨骼回退名称不一致')
    return 'j_kao'


class Facewear:
    def __init__(self,character):
        self.character=character;self.core=character.outfit;self.pack=character.base.pack
        self.prepared=OrderedDict();self.verified=False

    def verify(self):
        if self.verified:return
        expected=catalogue()[0]
        if self.pack.source_version()!=expected['client_version']:raise ValueError('面饰目录与当前资源版本不一致')
        for name,header in expected['headers'].items():
            if Sheet(self.pack,name).header.sha256!=header['sha256']:raise ValueError('面饰表结构与当前国服目录不一致')
        from build_facewear_catalogue import build
        if build(self.pack)!=expected:raise ValueError('面饰元数据与当前游戏文件不一致')
        self.verified=True

    def prepare(self,identifier):
        if identifier in self.prepared:self.prepared.move_to_end(identifier);return self.prepared[identifier]
        self.verify();record=catalogue()[2][identifier];model=record['model']&65535;variant=record['model']>>16
        from appearance_resolver import imc_entry,apply_visibility
        imc=imc_entry(self.pack.read(f'chara/equipment/e{model:04}/e{model:04}.imc'),'Head',variant)
        slot='Facewear'+str(record['slot'])
        if not self.core.variant_supported(imc,model_set=model,slot=slot):raise NotImplementedError('面饰使用尚未接入的材质或特效')
        race,trace=self.core.model_source(model,'Head',imc=imc)
        item={'item_id':identifier,'slot':slot,'model_set':model,'model_race':race,'variant':variant,'imc':imc,
            'model_path':f'chara/equipment/e{model:04}/model/c{race:04}e{model:04}_met.mdl'}
        original=self.core.model(item)
        binding=self.core;missing={n for m in original for n in m.bone_names}-self.core.names.keys();fallback={}
        if missing:
            try:self.core.attachment(item)
            except NotImplementedError as error:
                if str(error)!='本机EST中没有这件装备的额外骨架':raise
                name=native_missing_bone(self.pack)
                if name not in self.core.names:raise NotImplementedError('面饰缺少原生脸部回退骨骼')
                binding=copy.copy(self.core);binding.names={**self.core.names,**{n:self.core.names[name]for n in missing}}
                binding.parents={**self.core.parents,**{n:name for n in missing}};fallback={n:name for n in sorted(missing)}
        # Only original IMC attribute variants. Facewear never supplies head
        # EQP, visor/GMP, scalp/face hiding, or ordinary dye settings.
        visible,_=apply_visibility(original,'Head',0,imc['attribute_mask'])
        meshes=tuple(binding.equipment_mesh(raw,shown,item)for raw,shown in zip(original,visible)if len(shown.indices))
        # Preserve the independent EST owner for pose propagation. Its local
        # extra palette indices may overlap a simultaneously worn helmet.
        meshes=tuple({**mesh,'attachment_item':item}if max(mesh['palette'],default=-1)>=len(self.core.names)else mesh for mesh in meshes)
        value=(meshes,{'id':identifier,'name':record['name'],'slot':record['slot'],'model_path':item['model_path'],'model_race':race,'eqdp_selection':trace,'native_missing_bone':fallback})
        self.prepared[identifier]=value
        while len(self.prepared)>20:self.prepared.popitem(last=False)
        return value

    def resolve(self,scene,chosen):
        chosen=selection(chosen)
        if not any(chosen):return scene
        result=copy.copy(scene);result.meshes=list(scene.meshes);result.facewear=[]
        for identifier in chosen:
            if not identifier:continue
            meshes,info=self.prepare(identifier);result.meshes.extend(meshes);result.facewear.append(info)
        result.outfit_cache_identity=hashlib.sha256((getattr(scene,'outfit_cache_identity','')+'facewear-v1'+json.dumps(chosen)).encode()).hexdigest()
        return result
