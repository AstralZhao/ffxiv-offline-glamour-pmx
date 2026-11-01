"""Original CN special equipment materials in the fixed Demo preview."""
import struct

from static_material_inputs import SHADERS,shader_crc,static_pass
from preset_customization import sha


class DemoMaterials:
    def variant_supported(self,imc):
        return bool(imc['material_id'])and not(imc['vfx']or imc['material_animation'])

    def material_supported(self,path,shader,material):
        if path not in SHADERS or sha(shader)!=SHADERS[path]:return False
        return not material['translucent']or path.endswith('/characterglass.shpk')

    def material_programs(self,package,material,count):
        if material['shader']=='characterstockings.shpk':roles=('material','gbuffer_color')
        elif material['shader']=='characterglass.shpk':roles=('direct',)
        else:return super().material_programs(package,material,count)
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
        textures={}
        for suffix in('Diffuse','Normal','Mask'):
            bindings={int(binding['id'],16)for stage in package['stages']['pixel']for binding in stage['bindings']['textures']if binding['name']=='g_Sampler'+suffix}
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
        decal=item.get('imc',{}).get('decal',0)
        if decal:
            path=f'chara/common/texture/decal_equip/-decal_{decal:03}.tex'
            self.pack.locate(path)
            result['extra_textures']={**result.get('extra_textures',{}),'g_SamplerDecal':path}
            # Slot decals use their own neutral preview multiplier. Face-paint
            # tint belongs to the Face slot and must not recolor outfit decals.
            result['extra_constants']={**result.get('extra_constants',{}),
                'g_DecalColor':struct.pack('<4f',1,1,1,1)}
        return result
