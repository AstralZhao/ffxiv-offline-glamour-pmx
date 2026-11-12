"""Static native weapon models, independent palettes and reserved VFX inputs."""
from collections import OrderedDict
from dataclasses import dataclass,replace
import copy,hashlib,json,re,struct
import numpy as np
from ff14_readonly import Sheet
from geometry_probe import decode_model,resolve_mesh
from havok_probe import read_skeleton
from appearance_resolver import apply_visibility
from audit_cn_resources import material_info
from material_shader_probe import material_buffer
from cn_dye import dataset_location
from static_material_inputs import vertex_stream
from preset_outfit import GearChoice,validate_dyes
from source_session import shader_package

WEAPON_SLOTS=('MainHand','OffHand')
COMBAT_CATEGORIES=(1,2,3,4,5,6,7,8,9,10,11,84,87,88,89,96,97,98,105,106,107,108,109,110,111,113)


def weapon_visibility(meshes,imc_mask):
    # Native 8b2180 controls atr_attach independently of IMC and ATCH.
    # Static try-on does not enable the action-only Gauss Barrel attachment.
    _,plan=apply_visibility(meshes,'Head',0,imc_mask)
    visible=[resolve_mesh(mesh,(*plan['hidden_attributes'],'atr_attach'),plan['enabled_shapes'],plan['disabled_variant_attributes'])for mesh in meshes]
    plan['hidden_weapon_attributes']=['atr_attach']
    plan['visible_triangles']=sum(len(mesh.indices)//3 for mesh in visible)
    return visible,plan


def imc_model(model):
    # CN44d130: original category bitmap, second half shares main-hand IMC.
    category,remainder=divmod(model,100)
    return model-50 if category<=31 and (0xc4050008>>category)&1 and remainder>50 else model


def has_gauntlet(row):
    return row['ui_category']==1 and 0<row['model_sub']<0x100000000


def selection(value=None):
    if value is None:return {}
    if not isinstance(value,dict)or set(value)-set(WEAPON_SLOTS):raise ValueError('武器部位格式不正确')
    result={}
    for slot in WEAPON_SLOTS:
        choice=value.get(slot,{})
        if not isinstance(choice,dict)or set(choice)-{'item_id','first','second'}:raise ValueError('武器参数格式不正确')
        item=choice.get('item_id');first=choice.get('first',0);second=choice.get('second',0)
        if item is not None and(type(item)is not int or not 1<=item<=1000000):raise ValueError('武器编号不正确')
        if any(type(n)is not int or not 0<=n<=254 for n in(first,second)):raise ValueError('武器染色编号不正确')
        if item is None and(first or second):raise ValueError('未试穿的武器不能染色')
        result[slot]={'item_id':item,'first':first,'second':second}
    return result if any(c['item_id']for c in result.values())else{}


def validate_rows(conn,chosen,stains):
    rows={}
    for slot,choice in selection(chosen).items():
        if choice['item_id']is None:continue
        row=conn.execute('SELECT * FROM equipment WHERE item_id=?',(choice['item_id'],)).fetchone()
        if row is None or row['slot']!=slot or row['ui_category']not in COMBAT_CATEGORIES:raise ValueError('请选择对应部位的战斗武器')
        validate_dyes(GearChoice(**choice),row['dye_count'],stains);rows[slot]=dict(row)
    if 'MainHand'in rows and'OffHand'in rows and json.loads(rows['MainHand']['slot_rules']).get('OffHand',0)<0:
        raise ValueError('当前主手武器不能同时装备独立副手')
    return rows


@dataclass(frozen=True)
class WeaponEffectInput:
    slot:str
    part:str
    model_set:int
    body:int
    identifier:int
    path:str
    skeleton_path:str
    bones:tuple=()
    points:tuple=()
    drawn:bool=False
    attachment_bone:str=''


def sample_weapon_effects(inputs,*,time_seconds=0.,enabled=False,provider=None,simple_history=None):
    """Explicit standalone weapon boundary using its own bindings and cold state."""
    if not enabled:return ()
    if provider is None:raise ValueError('武器特效需要已核实的独立采样上下文')
    return tuple(sample for context in inputs for sample in provider.sample(
        context,provider.source(context),time_seconds*30.,simple_history)[0])


class Weapons:
    def __init__(self,character):
        self.character=character;self.core=character.outfit;self.pack=character.base.pack;self.prepared=OrderedDict();self.attachments=None;self.extra_skeletons=None;self.hanging_poses=None

    def verified_row(self,row):
        native=next((r for r in Sheet(self.pack,'Item').rows()if r.id==row['item_id']),None)
        for field,column in(('Name','name'),('ModelMain','model_main'),('ModelSub','model_sub'),('DyeCount','dye_count'),('EquipSlotCategory','slot_category')):
            if native is None or native.value(self.core.mapping['tables']['Item']['fields'][field])!=row[column]:raise ValueError('武器目录与当前游戏原始数据不一致')

    def part(self,word,slot,part_index,item_id,*,drawn=False):
        model=word&65535;body=word>>16&65535;variant=word>>32&65535
        base=f'chara/weapon/w{model:04}/obj/body/b{body:04}';path=base+f'/model/w{model:04}b{body:04}.mdl'
        raw=self.pack.read(path)
        original=decode_model(raw,allow_v5=True);owner=imc_model(model);imc_path=f'chara/weapon/w{owner:04}/obj/body/b{body:04}/b{body:04}.imc';imc_raw=self.pack.read(imc_path)
        count,mask=struct.unpack_from('<HH',imc_raw);parts=[i for i in range(16)if mask&(1<<i)]
        if len(parts)!=1 or variant>count or len(imc_raw)!=4+(count+1)*6:raise NotImplementedError('该武器使用尚未核实的IMC布局')
        material,decal,attributes,vfx,animation=struct.unpack_from('<BBHBB',imc_raw,4+variant*6)
        skel_path=f'chara/weapon/w{model:04}/skeleton/base/b0001/skl_w{model:04}b0001.sklb';skeleton=read_skeleton(self.pack.read(skel_path));names={b['name']for b in skeleton['bones']}
        if self.extra_skeletons is None:
            from weapon_skeleton import extra_skeletons
            self.extra_skeletons=extra_skeletons(self.pack.read('chara/xls/extraskl/extra_weapon.eslb'))
        extra=None
        if (model,body)in self.extra_skeletons:
            extra_path=f'chara/weapon/w{model:04}/skeleton/parts/p{body:04}/skl_w{model:04}p{body:04}.sklb'
            extra=read_skeleton(self.pack.read(extra_path));names.update(b['name']for b in extra['bones'])
        missing=set().union(*(set(m.bone_names)-names for m in original));fallback={}
        if missing:
            from weapon_skeleton import missing_root
            fallback=missing_root(self.pack,skeleton['bones'],missing)
        from weapon_pose import sheathed_book,drawn_weapon
        bone_deltas,local_pose=(drawn_weapon if drawn else sheathed_book)(self.pack,model,body,skeleton,extra)
        from weapon_hanging import prepare as hanging_source
        hanging=hanging_source(self.pack,model,body,skeleton,extra)
        visible,_=weapon_visibility(original,attributes&1023);meshes=[]
        for source,shown in zip(original,visible):
            if not material or not len(shown.indices):continue
            filename=source.material_name.lstrip('/');match=re.fullmatch(r'mt_w(\d{4})b(\d{4})_.+\.mtrl',filename)
            if match:
                if model//100==20 and filename[14]=='c':mpath='chara/weapon/w2001/obj/body/b0001/material/v0001/mt_w2001b0001_c.mtrl'
                else:
                    if owner!=model:filename=filename[:4]+f'{owner:04}'+filename[8:]
                    mpath=f'chara/weapon/w{owner:04}/obj/body/b{body:04}/material/v{material:04}/'+filename
            elif filename.startswith('chara/'):mpath=filename
            else:raise NotImplementedError('武器材质引用路径尚未核实')
            mraw=self.pack.read(mpath);mat=material_info(mraw);spath='shader/sm5/shpk/'+mat['shader'];sraw=self.pack.read(spath)
            if not self.core.material_supported(spath,sraw,mat):raise NotImplementedError('该武器材质家族尚未接入')
            packages=self.character.base.packages
            if spath not in packages:packages[spath]=shader_package(self.pack,spath,sraw)
            package=packages[spath];plans=self.core.material_programs(package,mat,source.weights.shape[1]);constants,_,_=material_buffer(package,mat);table=None
            if mat['dataset_bytes']:
                start,size=dataset_location(mraw)
                if mat['color_table_dimensions']!='53'or size<2048:raise NotImplementedError('武器颜色表格式尚未接入')
                table=mraw[start:start+2048]
            signed_nan=(path=='chara/weapon/w1701/obj/body/b0093/model/w1701b0093.mdl'and hashlib.sha256(raw).hexdigest()=='65c12769536c4ce0b5cad3db49da6aa6672be5eecf7ab9c410720b0cb081c1bf')
            mesh={'slot':slot,'source_mesh':source.source_mesh,'vertices':vertex_stream(source,source.positions,source.normals,None,allow_signed_uv_nan=signed_nan),'indices':shown.indices,
                'palette':np.arange(len(source.bone_names)),'posed_positions':source.positions.copy(),'material_path':mpath,'material':mat,'constants':constants,'table':table,
                'shader_path':spath,'plans':plans,'plan':plans.get('direct',plans.get('gbuffer_color',next(iter(plans.values())))),'transparent':False,'overlay':None,
                'source_sha256':hashlib.sha256(raw).hexdigest(),'item_id':item_id,'dyes':[0,0],'pose_position_error':None,'original_color_table_sha256':hashlib.sha256(table).hexdigest()if table else None,
                'color_table_sha256':hashlib.sha256(table).hexdigest()if table else None,'_source_material':mraw,'weapon_part':part_index,
                'weapon_bone_names':tuple(fallback.get(name,name)for name in source.bone_names)}
            # Preserve native material emission/decal, reserve AVFX separately.
            mesh=self.core.finalize_equipment_mesh(mesh,source,{'slot':slot,'model_set':model,'imc':{'decal':decal,'vfx':0,'material_animation':0}},package)
            if bone_deltas:
                mesh['weapon_local_matrices']=np.asarray([bone_deltas[fallback.get(name,name)]for name in source.bone_names])
                mesh['weapon_local_matrices'].setflags(write=False)
            if animation:
                animation_path=f'chara/weapon/w{model:04}/animation/s{animation:04}/body/material.pap';self.pack.locate(animation_path)
                mesh['weapon_material_animation_source']=animation_path
                mesh['preview_notice']='武器材质动画暂未播放，当前显示原始静态材质。'
            meshes.append(mesh)
        points=();effect_bones=()
        if vfx:
            from weapon_effects import model_points,posed_bones
            points=model_points(raw);effect_bones=posed_bones(skeleton,extra,bone_deltas)
        effect_path=(f'vfx/weapon/eff/vw{vfx:04}.avfx'if vfx>=100 else base+f'/vfx/eff/vw{vfx:04}.avfx')
        effect=WeaponEffectInput(slot,str(part_index),model,body,vfx,effect_path,skel_path,
                                 points=points,drawn=drawn)
        return {'meshes':tuple(meshes),'model':model,'body':body,'variant':variant,'effect':effect,
                'effect_bones':effect_bones,'model_path':path,'native_missing_bone':fallback,
                'local_pose':local_pose,'hanging':hanging,'bone_deltas':bone_deltas if hanging else None,
                'skeleton_sha256':hashlib.sha256(self.pack.read(skel_path)).hexdigest()}

    def prepare(self,row,secondary=True,*,drawn=False):
        key=(row['item_id'],secondary,drawn)
        if key in self.prepared:self.prepared.move_to_end(key);return self.prepared[key]
        self.verified_row(row);sub=row['model_sub']
        if has_gauntlet(row):sub=(row['model_main']&~65535)|((row['model_main']&65535)+50)
        parts=[self.part(word,row['slot'],i,row['item_id'],drawn=drawn)for i,word in enumerate((row['model_main'],sub))if word and(secondary or i==0)]
        self.prepared[key]=parts
        while len(self.prepared)>16:self.prepared.popitem(last=False)
        return parts

    def resolve_character(self,choices,state):
        """Native fist tertiary equipment replaces gloves before body visibility."""
        rows=validate_rows(self.core.database,state.get('weapons'),self.core.stains);row=rows.get('MainHand');core=self.core
        if row and has_gauntlet(row)and state.get('weapon_secondary_visible',True):
            from appearance_resolver import imc_entry
            from preset_outfit import GloveChoice
            self.verified_row(row);key=('gauntlet',row['item_id'])
            if key in self.prepared:core=self.prepared[key];self.prepared.move_to_end(key)
            else:
                model=row['model_sub']&65535;variant=row['model_sub']>>16&65535;imc=imc_entry(self.pack.read(f'chara/equipment/e{model:04}/e{model:04}.imc'),'Gloves',variant)
                race,trace=self.core.model_source(model,'Gloves',imc=imc)
                metadata={'item_id':row['item_id'],'name':row['name'],'slot':'Gloves','dye_count':row['dye_count'],'model_set':model,'variant':variant,'schema_sha256':self.core.schema_sha256}
                record={**metadata,'model_race':race,'model_path':f'chara/equipment/e{model:04}/model/c{race:04}e{model:04}_glv.mdl','eqdp_selection':trace,'imc':{**imc,'vfx':0,'material_animation':0}}
                core=copy.copy(self.core);core.items=dict(core.items);core.prepared=dict(core.prepared);core.equipment_effects_enabled=False
                core.items['metadata','Gloves',row['item_id']]=metadata;core.items['Gloves',row['item_id']]=record
                self.prepared[key]=core
                while len(self.prepared)>16:self.prepared.popitem(last=False)
            choices={**choices,'Gloves':GloveChoice(**state['weapons']['MainHand'])}
        scene,info=core.resolve(choices['Gloves'],feet=choices['Feet'],body=choices['Body'],legs=choices['Legs'],head=choices['Head'],head_visible=state['head_visible'])
        if core is not self.core:
            scene.meshes=[{**m,'weapon_gauntlet':True,'attachment_item':core.items['Gloves',row['item_id']]}if m['slot']=='Gloves'and m['item_id']==row['item_id']else m for m in scene.meshes]
        return scene,info

    def resolve(self,scene,chosen,*,view='character',focus='MainHand',secondary=True,drawn=False):
        chosen=selection(chosen);rows=validate_rows(self.core.database,chosen,self.core.stains)
        if not rows and view=='character':return scene
        from weapon_attachment import WeaponAttachments,attach_local
        if rows and self.attachments is None:self.attachments=WeaponAttachments(self.pack,self.core.race)
        result=copy.copy(scene);result.meshes=[]if view=='weapon'else list(scene.meshes);result.weapon_effect_inputs=[];result.weapon_notices=[];result.weapon_models=[]
        if view=='weapon':result.effect_frame=None;result.simple_histories={}
        worlds=scene.skinning@self.core.bind
        selected=focus if focus in rows else next(iter(rows),None)
        gauntlets=[{**m,'slot':'MainHand'}for m in scene.meshes if m.get('weapon_gauntlet')]
        if view=='character':result.meshes=[m for m in result.meshes if not m.get('weapon_gauntlet')]
        if view=='character'or selected=='MainHand':result.meshes.extend(gauntlets)
        for slot,row in rows.items():
            if view=='weapon'and slot!=selected:continue
            for part_index,part in enumerate(self.prepare(row,secondary,drawn=drawn)):
                index=1 if slot=='OffHand'or part_index else 0;entry=self.attachments.entry(part['model'],index,drawn=drawn)
                if entry['bone']not in self.core.names:raise NotImplementedError('角色缺少原生武器收刀挂点：'+entry['bone'])
                matrix=worlds[self.core.names[entry['bone']]]@attach_local(entry)
                hanging=part.get('hanging');hanging_deltas=None;hanging_notice=None
                if hanging is not None:
                    from weapon_hanging import HangingPoses
                    if self.hanging_poses is None:self.hanging_poses=HangingPoses()
                    hanging_deltas,hanging_notice=self.hanging_poses.resolve(hanging,matrix,part['bone_deltas'])
                for mesh in part['meshes']:
                    current=self.core.dye_mesh(mesh,GearChoice(**chosen[slot]));vertices=current['vertices'];points=np.column_stack((vertices['position'],np.ones(len(vertices))))
                    local=mesh.get('weapon_local_matrices')
                    if hanging_deltas is not None:local=np.asarray([hanging_deltas.get(n,np.eye(4))for n in mesh['weapon_bone_names']])
                    if local is None:
                        joints=np.repeat(matrix[None],len(mesh['palette']),axis=0);posed=(points@matrix.T)[:,:3]
                    else:
                        joints=matrix@local;posed=np.zeros((len(vertices),3));weights=vertices['weights'].astype(float)/255.
                        for influence in range(weights.shape[1]):
                            posed+=np.einsum('nij,nj->ni',joints[vertices['joints'][:,influence]],points)[:,:3]*weights[:,influence,None]
                        if not np.isfinite(posed).all():raise ValueError('武器姿态产生非有限顶点')
                    current={**current,'joint_matrices':joints,'posed_positions':posed,'weapon_attachment_bone':entry['bone']};result.meshes.append(current)
                if part['effect'].identifier:
                    bones=[]
                    effect_bones=part['effect_bones']if hanging_deltas is None else tuple(
                        (n,hanging_deltas.get(n,np.eye(4))@b)for n,b in zip(hanging.names,hanging.reference))
                    for name,local in effect_bones:
                        world=matrix@local;world.setflags(write=False);bones.append((name,world))
                    result.weapon_effect_inputs.append(replace(part['effect'],bones=tuple(bones),attachment_bone=entry['bone']))
                result.weapon_models.append({'slot':slot,'item_id':row['item_id'],'part':part_index,'path':part['model_path'],'attachment':entry,'local_pose':part['local_pose'],
                    'model_set':part['model'],'body_id':part['body'],'slot_index':index,
                    **({'hanging_pose':hanging_notice}if hanging_notice else{})})
        if view=='weapon':
            bounds=[m['posed_positions'][np.unique(m['indices'])]for m in result.meshes if len(m['indices'])]
            if bounds:result.bounds=np.vstack(bounds)
            else:result.weapon_notices.append('这件武器在当前静态状态下没有可见部件。'if rows else'请选择一件武器进行单独预览。')
        result.outfit_cache_identity=hashlib.sha256((getattr(scene,'outfit_cache_identity','')+'weapon-static-v1'+json.dumps([chosen,view,focus],sort_keys=True)).encode()).hexdigest()
        return result
