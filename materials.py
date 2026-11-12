"""Original CN special equipment materials in the fixed Demo preview."""
import struct
import copy
from collections import OrderedDict

from static_material_inputs import SHADERS,shader_crc,static_pass,alpha_test_enabled
from preset_customization import sha


class DemoMaterials:
    # Source AVFX is optional in the viewer; unsupported branches remain
    # explicit in the preview notices, never substituted with invented meshes.
    equipment_effects_enabled=True

    def variant_supported(self,imc,*,model_set=None,slot=None):
        if not imc['material_id']:return False
        if imc['vfx']and self.equipment_effects_enabled:self.equipment_effect_source(model_set,slot,imc['vfx'])
        if imc['material_animation']:self.material_animation_source(model_set,slot,imc['material_animation'])
        return True

    def equipment_effect_source(self,model_set,slot,identifier):
        library=getattr(self,'effect_library',None)
        if library is None:
            from vfx import EquipmentEffects
            self.effect_library=EquipmentEffects(self.pack);library=self.effect_library
        return library.prepare(model_set,slot,identifier)

    def material_animation_source(self,model_set,slot,identifier):
        cache=getattr(self,'material_animation_sources',None)
        if cache is None:self.material_animation_sources={};cache=self.material_animation_sources
        if slot not in('Head','Body','Gloves','Legs','Feet')or not 1<=identifier<=255:
            raise NotImplementedError('当前部位的材质动画尚未接入')
        # Original CN444f30..444fb8: IDs >=10 select common equipment;
        # smaller IDs select the equipment model set, not the material variant.
        path=(f'chara/common/animation/equipment/s{identifier:04}/material.pap'if identifier>=10 else
            f'chara/equipment/e{model_set:04}/animation/s{identifier:04}/material.pap')
        if path not in cache:
            try:raw=self.pack.read(path)
            except FileNotFoundError as error:
                if str(error)!=path:raise
                cache[path]={'path':path,'status':'absent_optional_source','overrides_applied':False,
                    'notice':'装备引用的材质动画资源在本机缺失，当前显示原始静态材质。'}
            else:
                raise NotImplementedError('已找到材质动画文件，尚未接入其初始材质参数：'+path)
        return cache[path]

    def material_supported(self,path,shader,material):
        return path in SHADERS and sha(shader)==SHADERS[path]

    def material_programs(self,package,material,count):
        # Parsing original shader instructions is source preparation, not an
        # item-specific operation. Share its result across gear and dye states.
        # Keep the actual package object with the entry: an id can be reused
        # only after its previous object dies, which this reference prevents.
        cache=getattr(self,'material_program_cache',None)
        if cache is None:
            self.material_program_cache=OrderedDict();cache=self.material_program_cache
            self.material_program_cache_hits=0;self.material_program_cache_misses=0
        key=(id(package),material['shader'],bool(material['translucent']),count,
            tuple(sorted(material['shader_keys'].items())))
        if key in cache:
            cache.move_to_end(key);self.material_program_cache_hits+=1
            return copy.deepcopy(cache[key][1])
        plans=self.uncached_material_programs(package,material,count)
        self.material_program_cache_misses+=1
        # Never share nested mutable binding lists with a mesh or renderer.
        cache[key]=(package,copy.deepcopy(plans))
        while len(cache)>128:cache.popitem(last=False)
        return plans

    def uncached_material_programs(self,package,material,count):
        overlay={'charactertattoo.shpk':'overlay_tattoo','characterocclusion.shpk':'overlay_occlusion'}.get(material['shader'])
        if overlay:roles=(overlay,)
        elif material['shader']=='characterstockings.shpk':roles=('material','gbuffer_color')
        elif material['shader']=='characterglass.shpk':roles=('direct',)
        elif material['shader']=='charactertransparency.shpk'or(material['translucent']and material['shader']!='hair.shpk'):roles=('direct',)
        else:
            try:return super().material_programs(package,material,count)
            except ValueError as error:
                if material['shader']!='characterlegacy.shpk'or str(error)!='Static preview color pass is absent or ambiguous':raise
                from shader_programs import equipment_pass
                return {'material':equipment_pass(package,material['shader_keys'],count,'material'),
                    'direct':static_pass(package,material['shader_keys'],count,'direct')}
        return {role:static_pass(package,material['shader_keys'],count,role)for role in roles}

    def skin_inputs(self,slot):
        cache=getattr(self,'skin_input_cache',None)
        if cache is None:self.skin_input_cache={};cache=self.skin_input_cache
        if slot in cache:return cache[slot]
        sources=[m for m in self.base.meshes if m['shader_path'].endswith('/skin.shpk')and m['slot']in(slot,'Body')]
        sources.sort(key=lambda m:m['slot']!=slot)
        if not sources:raise NotImplementedError('丝袜材质没有预设角色的原始皮肤来源')
        source=sources[0];material=source['material'];package=self.base.packages[source['shader_path']]
        if material['shader_keys'].get(0x380caed0)!=0x2bdb45f1:
            raise NotImplementedError('当前丝袜输入需要原始PART_BODY皮肤材质')
        params={identifier:(offset,size)for identifier,offset,size in package['parameters']}
        def field(name,size):
            key=shader_crc('g_'+name)
            if key not in params or params[key][1]!=size:raise ValueError('皮肤材质缺少原始参数：'+name)
            offset,_=params[key]
            return source['constants'][offset:offset+size]
        # PART_BODY's original skin PS uses body customization (the type2
        # branch in stockings) and shader-type row32+trunc(g_ShaderID).
        shader_id=int(struct.unpack('<f',field('ShaderID',4))[0])+32
        if not 32<=shader_id<64:raise ValueError('皮肤着色类型不在本机皮肤参数表中')
        payload=(field('DiffuseColor',12)+struct.pack('<ii',2,shader_id)
            +b''.join(field(name,4)for name in('SheenRate','SheenTintRate','SheenAperture'))
            +field('TileScale',8)+struct.pack('<i',int(struct.unpack('<f',field('TileIndex',4))[0]))+field('TileAlpha',4))
        if len(payload)!=48:raise ValueError('皮肤参数尺寸不正确')
        from prepared_programs import complete_stage_programs
        programs=complete_stage_programs(package,'pixel')
        textures={}
        for suffix in('Diffuse','Normal','Mask'):
            bindings={int(binding['id'],16)for stage in programs for binding in stage['bindings']['textures']if binding['name']=='g_Sampler'+suffix}
            sampler=next((s for s in material['samplers']if s['id']in bindings),None)
            if sampler is None:raise ValueError('原始皮肤材质缺少贴图：'+suffix)
            textures['g_SamplerSkin'+suffix]=material['textures'][sampler['texture_index']]
        value=(payload,textures,material);cache[slot]=value;return value

    def finalize_equipment_mesh(self,result,source,item,package):
        result=super().finalize_equipment_mesh(result,source,item,package)
        shader=result['material']['shader']
        if shader=='characterstockings.shpk':
            constants,textures,material=self.skin_inputs(item['slot'])
            result.update(extra_constants={'g_SkinMaterialParameter':constants},extra_textures=textures,skin_material=material)
        elif shader=='characterglass.shpk':
            result.update(transparent=True,forward_transparent=True,
                extra_constants={'g_GlassOffscreenParam':struct.pack('<4f',1,0,0,0)})
        elif shader in('charactertattoo.shpk','characterocclusion.shpk'):
            role='overlay_tattoo'if shader=='charactertattoo.shpk'else'overlay_occlusion'
            result.update(overlay=role,transparent=True,plan=result['plans'][role])
        elif shader=='character.shpk'and result['material']['translucent']and alpha_test_enabled(package,result['constants']):
            # Authored alpha clipping also applies to clothing and ribbons.
            # Their solid surfaces need depth before blending soft edges.
            plans={role:static_pass(package,result['material']['shader_keys'],source.weights.shape[1],role)
                for role in('material','gbuffer_color','direct')}
            result.update(transparent=True,alpha_core=True,plans=plans)
        elif shader=='charactertransparency.shpk'or(result['material']['translucent']and shader not in('hair.shpk','characterstockings.shpk')):
            result.update(transparent=True,forward_transparent=True)
        elif result['material']['translucent']:result['transparent']=True
        decal=item.get('imc',{}).get('decal',0)
        if decal:
            path=f'chara/common/texture/decal_equip/-decal_{decal:03}.tex'
            self.pack.locate(path)
            result['extra_textures']={**result.get('extra_textures',{}),'g_SamplerDecal':path}
            # Slot decals use their own neutral preview multiplier. Face-paint
            # tint belongs to the Face slot and must not recolor outfit decals.
            result['extra_constants']={**result.get('extra_constants',{}),
                'g_DecalColor':struct.pack('<4f',1,1,1,1)}
        animation=item.get('imc',{}).get('material_animation',0)
        if animation:
            plan=self.material_animation_source(item['model_set'],item['slot'],animation)
            result['preview_notice']=plan['notice']
            result['material_animation_source']=plan
        vfx=item.get('imc',{}).get('vfx',0)
        if vfx and self.equipment_effects_enabled:
            effect=self.equipment_effect_source(item['model_set'],item['slot'],vfx)
            result['equipment_effect']=effect.summary()
            result['equipment_effect_source']=effect
        return result
