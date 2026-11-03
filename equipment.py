"""Preset-owned attachment of original equipment EST skeletons for the Demo.

Keep source reference locals under the current fixed character anchors. This
does not animate cloth, visors or equipment physics. Every missing deformer
joint must inherit through an explicit original skeleton parent chain.
"""
from collections import OrderedDict
import json
from pathlib import Path
import re
import struct

import numpy as np

from geometry_probe import local_matrix
from havok_probe import read_skeleton
from prepare_preset_customization import bundle_arrays
from preset_customization import sha
from preset_outfit import PresetOutfit,outfit_visibility
from materials import DemoMaterials
from audit_cn_resources import model_info,PART_INDEX,SUFFIXES


class DemoOutfit(DemoMaterials,PresetOutfit):
    mesh_owned_joints=True

    def visible_meshes(self,meshes,slot,flags,mask):
        return outfit_visibility(meshes,slot,flags,mask,race=self.base.pose['preset']['parameters']['race'])

    def model_variant_available(self,model_set,slot,race,material_id):
        cache=getattr(self,'model_variant_sources',None)
        if cache is None:self.model_variant_sources=OrderedDict();cache=self.model_variant_sources
        key=(model_set,slot,race,material_id)
        if key in cache:cache.move_to_end(key);return cache[key]
        path=f'chara/equipment/e{model_set:04}/model/c{race:04}e{model_set:04}_{SUFFIXES[slot]}.mdl'
        names=model_info(self.pack.read(path))['materials'];available=True
        for name in names:
            match=re.fullmatch(r'/?(mt_c\d{4}e(\d{4})_.+\.mtrl)',name)
            if not match:continue
            required=f'chara/equipment/e{match[2]}/material/v{material_id:04}/{match[1]}'
            try:self.pack.locate(required)
            except FileNotFoundError as error:
                if str(error)!=required:raise
                available=False;break
        cache[key]=available
        while len(cache)>256:cache.popitem(last=False)
        return available

    def model_source(self,model_set,slot,*,imc=None):
        try:
            race,trace=super().model_source(model_set,slot,imc=imc)
        except NotImplementedError as error:
            if str(error)!='No model in the local EQDP/PBD ancestry':raise
        else:
            if imc is None:return race,trace
            initial=race
            while race is not None:
                flags=self.eqdp(race).entry(model_set)
                if flags&(1<<(PART_INDEX[slot]*2+1))and self.model_variant_available(model_set,slot,race,imc['material_id']):
                    if race!=initial:trace=trace+[{'race':race,'eqdp_entry':flags,'selection':'matching_model_and_material_variant'}]
                    return race,trace
                race=self.pbd.parent(race)
            raise NotImplementedError('当前变体没有成套匹配的原始装备模型和材质')
        # A few race-exclusive items have no model in the selected race's
        # ancestor chain. An unambiguous original source may still be adapted
        # through the common PBD ancestor. Never pick an arbitrary sibling.
        candidates=[];bit=1<<(PART_INDEX[slot]*2+1)
        for race in sorted(self.pbd.by_race):
            path=f'chara/xls/charadb/equipmentdeformerparameter/c{race:04}.eqdp'
            try:flags=self.eqdp(race).entry(model_set)
            except FileNotFoundError as error:
                if str(error)!=path:raise
                continue
            if flags&bit and(imc is None or self.model_variant_available(model_set,slot,race,imc['material_id'])):candidates.append((race,flags))
        if len(candidates)!=1:raise NotImplementedError('专属装备需要唯一的原生模型来源：'+str(candidates))
        race,flags=candidates[0]
        return race,[{'race':race,'eqdp_entry':flags,'selection':'unique_cross_branch_demo_adaptation'}]

    def __init__(self,base):
        super().__init__(base)
        self.preview_restricted_items=True
        self.attachments=OrderedDict();self.est={}
        path=Path(base.pose['source_assembly']['path'])
        arrays=bundle_arrays(json.loads(path.read_bytes()),path,'bind_geometry_bundle')
        self.bind=arrays['skeleton_bind']
        self.parents={name:(arrays['skeleton_names'][arrays['skeleton_parents'][index]]
            if arrays['skeleton_parents'][index]>=0 else None)for name,index in self.names.items()}

    def extra_source(self,item):
        kind={'Head':('extra_met','met','m'),'Body':('extra_top','top','t')}.get(item['slot'])
        if kind is None:raise NotImplementedError('当前部位没有可连接的原生装备骨架')
        table,folder,prefix=kind
        if table not in self.est:
            raw=self.pack.read('chara/xls/charadb/'+table+'.est')
            if len(raw)<4:raise ValueError('装备EST表截断')
            count=struct.unpack_from('<I',raw)[0]
            if len(raw)!=4+count*6:raise ValueError('装备EST表长度不正确')
            rows={}
            for i in range(count):
                model,race=struct.unpack_from('<HH',raw,4+i*4)
                key=(race,model)
                if key in rows:raise ValueError('装备EST存在重复映射')
                rows[key]=struct.unpack_from('<H',raw,4+count*4+i*2)[0]
            self.est[table]=rows
        race=self.race
        while race is not None:
            skeleton=self.est[table].get((race,item['model_set']))
            if skeleton:
                path=f'chara/human/c{race:04}/skeleton/{folder}/{prefix}{skeleton:04}/skl_c{race:04}{prefix}{skeleton:04}.sklb'
                return path,read_skeleton(self.pack.read(path))
            race=self.pbd.parent(race)
        raise NotImplementedError('本机EST中没有这件装备的额外骨架')

    def attachment(self,item):
        key=(item['slot'],item['model_set'])
        if key in self.attachments:
            self.attachments.move_to_end(key);return self.attachments[key]
        path,skeleton=self.extra_source(item)
        lookup=dict(self.names);parents=dict(self.parents)
        bind=list(self.bind);current=list(self.base.skinning@self.bind)
        bones=skeleton['bones'];seen=set()
        for index,bone in enumerate(bones):
            name=bone['name'];parent=bone['parent']
            if name in seen or not -1<=parent<index:raise ValueError('装备骨架名称或父级不正确')
            seen.add(name)
            if parent<0:
                if name not in self.names:raise NotImplementedError('装备骨架根节点未连接到角色：'+name)
                continue
            parent_name=bones[parent]['name'];local=local_matrix(bone)
            if parent_name not in lookup:raise ValueError('装备父骨骼尚未绑定')
            p=lookup[parent_name]
            if name in lookup:
                if parents[name]!=parent_name or not np.allclose(np.linalg.solve(bind[p],bind[lookup[name]]),local,rtol=0,atol=2e-5):
                    raise NotImplementedError('装备骨架与角色已有的关节连接冲突：'+name)
                continue
            lookup[name]=len(bind);parents[name]=parent_name
            bind.append(bind[p]@local);current.append(current[p]@local)
        bind=np.asarray(bind);current=np.asarray(current)
        skinning=current@np.linalg.inv(bind)
        # Retain the preset's existing matrices exactly, including fixed hair
        # results; only the newly attached bones need reconstruction.
        skinning[:len(self.bind)]=self.base.skinning
        skinning.setflags(write=False)
        value={'names':lookup,'parents':parents,'skinning':skinning,
            'source':path,'source_sha256':sha(self.pack.read(path)),'added_bones':len(bind)-len(self.bind)}
        if not np.isfinite(value['skinning']).all():raise ValueError('装备骨架产生非有限变换')
        self.attachments[key]=value
        while len(self.attachments)>32:self.attachments.popitem(last=False)
        return value

    def joint_binding(self,source,item):
        if not set(source.bone_names)-self.names.keys():
            palette,joints=super().joint_binding(source,item)
            return palette,joints
        attached=self.attachment(item)
        missing=set(source.bone_names)-attached['names'].keys()
        if missing:raise NotImplementedError('原生装备骨架仍缺少关节：'+', '.join(sorted(missing)))
        palette=np.asarray([attached['names'][name]for name in source.bone_names])
        return palette,attached['skinning'][palette]

    def mesh_deformation(self,source,item):
        race=item['model_race']
        if race==self.race:return None
        missing=set(source.bone_names)-self.names.keys()
        parents=self.attachment(item)['parents']if missing else self.parents
        source_chain=[];node=race
        while node is not None:source_chain.append(node);node=self.pbd.parent(node)
        target_chain=[];node=self.race
        while node is not None:target_chain.append(node);node=self.pbd.parent(node)
        common=next((n for n in source_chain if n in target_chain),None)
        if common is None:raise NotImplementedError('装备与角色没有共用的PBD来源')
        edges=[]
        for child in source_chain[:source_chain.index(common)]:
            edges.append((self.pbd.direct_matrices(self.pbd.parent(child),child),True))
        for child in reversed(target_chain[:target_chain.index(common)]):
            edges.append((self.pbd.direct_matrices(self.pbd.parent(child),child),False))
        result={name:np.eye(4)for name in source.bone_names}
        for matrices,inverse in edges:
            for name in result:
                ancestor=name;seen=set()
                if inverse and name not in matrices:
                    raise NotImplementedError('跨体型逆变形缺少显式原生关节：'+name)
                while ancestor not in matrices:
                    if ancestor is None or ancestor in seen:raise NotImplementedError('装备关节没有可继承的变形：'+name)
                    seen.add(ancestor);ancestor=parents.get(ancestor)
                matrix=np.linalg.inv(matrices[ancestor])if inverse else matrices[ancestor]
                result[name]=matrix@result[name]
        return result
