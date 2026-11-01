"""Generic source-driven accessory route for the interactive Demo.

No item whitelist or per-item tuning. Uses the installed accessory EQDP,
PBD ancestry, IMC, source meshes and existing native material programs.
"""
import copy
import re
import struct

import numpy as np

from appearance_resolver import apply_visibility
from audit_cn_resources import material_info,material_path
from cn_dye import dataset_location
from geometry_probe import decode_model
from material_shader_probe import material_buffer
from preset_customization import sha
from preset_outfit import GearChoice,validate_dyes
from racial_deformation import Eqdp,deform_mesh
from shader_feasibility import inspect_shpk
from static_material_inputs import SHADERS,static_pass,vertex_stream

PARTS={'Ears':(0,'ear'),'Neck':(1,'nek'),'Wrists':(2,'wrs'),'FingerR':(3,'rir'),'FingerL':(4,'ril')}


class Accessories:
    def __init__(self,character):
        self.character=character;self.core=character.outfit;self.pack=character.base.pack
        self.eqdp={};self.prepared={}

    def source(self,model,slot):
        race=1401;index=PARTS[slot][0]
        while race is not None:
            if race not in self.eqdp:
                self.eqdp[race]=Eqdp(self.pack.read(f'chara/xls/charadb/accessorydeformerparameter/c{race:04}.eqdp'))
            if self.eqdp[race].entry(model)&(1<<(index*2+1)):return race
            race=self.core.pbd.parent(race)
        raise NotImplementedError('本机饰品EQDP/PBD未选出可用模型')

    def prepare(self,slot,item_id):
        key=(slot,item_id)
        if key in self.prepared:return self.prepared[key]
        row=self.core.database.execute('SELECT * FROM equipment WHERE item_id=?',(item_id,)).fetchone()
        expected='FingerL'if slot=='FingerR'else slot
        if row is None or row['slot']!=expected:raise ValueError('装备不属于这个饰品部位')
        model=row['model_main']&65535;variant=(row['model_main']>>16)&65535;race=self.source(model,slot)
        suffix=PARTS[slot][1];path=f'chara/accessory/a{model:04}/model/c{race:04}a{model:04}_{suffix}.mdl'
        imc=self.pack.read(f'chara/accessory/a{model:04}/a{model:04}.imc');count,mask=struct.unpack_from('<HH',imc)
        parts=[i for i in range(16)if mask&(1<<i)];index=PARTS[slot][0]
        if variant>count or index not in parts:raise ValueError('饰品变体不在本机IMC中')
        at=4+(variant*len(parts)+parts.index(index))*6
        material_id,decal,attributes,vfx,animation=struct.unpack_from('<BBHBB',imc,at)
        if decal or animation:raise NotImplementedError('饰品需要尚未接入的贴花或材质动画')
        raw=self.pack.read(path);original=decode_model(raw);visible,_=apply_visibility(original,slot,0,attributes&1023)
        meshes=[]
        for source,shown in zip(original,visible):
            if not len(shown.indices):continue
            deformation=self.core.deformation(race,source.bone_names)
            reference=deform_mesh(source,deformation)if deformation is not None else source
            if set(source.bone_names)-self.core.names.keys():raise NotImplementedError('饰品需要尚未接入的附加骨骼')
            palette=np.asarray([self.core.names[n]for n in source.bone_names])
            vertices=vertex_stream(source,reference.positions,reference.normals,deformation)
            transforms=np.sum(self.character.base.skinning[palette][source.joints]*source.weights[...,None,None],axis=1)
            positions=np.einsum('nij,nj->ni',transforms,np.column_stack((reference.positions,np.ones(len(vertices)))))[:,:3]
            filename=source.material_name.lstrip('/')
            match=re.fullmatch(r'mt_c\d{4}a(\d{4})_.*\.mtrl',filename)
            mpath=(f'chara/accessory/a{match[1]}/material/v{material_id:04}/{filename}'if match else material_path(self.pack,source.material_name,material_id))
            mraw=self.pack.read(mpath);material=material_info(mraw);shader='shader/sm5/shpk/'+material['shader']
            sraw=self.pack.read(shader)
            if shader not in SHADERS or sha(sraw)!=SHADERS[shader]or material['translucent']:
                raise NotImplementedError('这件饰品的材质尚未接入当前独立渲染器')
            packages=self.character.base.packages
            if shader not in packages:packages[shader]=inspect_shpk(sraw,True)[1]['_package']
            package=packages[shader];plans={role:static_pass(package,material['shader_keys'],source.weights.shape[1],role)for role in('material','direct')}
            constants,_,_=material_buffer(package,material);table=None
            if material['dataset_bytes']:
                start,size=dataset_location(mraw)
                if material['color_table_dimensions']!='53'or size<2048:raise NotImplementedError('饰品使用尚未接入的颜色表格式')
                table=mraw[start:start+2048]
            meshes.append({'slot':slot,'source_mesh':source.source_mesh,'vertices':vertices,'indices':shown.indices,
                'palette':palette,'posed_positions':positions,'material_path':mpath,'material':material,'constants':constants,
                'table':table,'shader_path':shader,'plan':plans['direct'],'plans':plans,'transparent':False,'overlay':None,
                'source_sha256':sha(raw),'pose_position_error':None,'item_id':item_id,'dyes':[0,0],
                'original_color_table_sha256':sha(table)if table else None,'color_table_sha256':sha(table)if table else None,
                '_source_material':mraw})
        value=(meshes,row['dye_count'])
        if len(self.prepared)>=40:self.prepared.pop(next(iter(self.prepared)))
        self.prepared[key]=value;return value

    def resolve(self,scene,state,info):
        selected={s:c for s,c in state['equipment'].items()if s in PARTS and c['item_id']is not None}
        if not selected:return scene
        flags=info.get('effective_eqp')or self.core.eqp.resolve({s:0 for s in('Head','Body','Gloves','Legs','Feet')})
        flags=int(flags['flags'],16)
        show={'Ears':bool(flags&(1<<49)),'Neck':bool(flags&(1<<11)and flags&(1<<45)),
            'Wrists':bool(flags&(1<<12)and flags&(1<<28)),'FingerL':bool(flags&(1<<29)),'FingerR':bool(flags&(1<<30))}
        result=copy.copy(scene);result.meshes=list(scene.meshes)
        for slot,c in selected.items():
            meshes,count=self.prepare(slot,c['item_id']);choice=GearChoice(**c);validate_dyes(choice,count,self.core.stains)
            if show[slot]:result.meshes.extend(self.core.dye_mesh(m,choice)for m in meshes)
        return result
