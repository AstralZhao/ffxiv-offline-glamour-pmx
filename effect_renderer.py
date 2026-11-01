"""Original installed CN Apricot programs on the independent wardrobe device.

This frozen preview has no scene fog, exposure or depth of field. Neutral
postprocess inputs are defined explicitly; no game buffers/hooks are used.
"""
import ctypes as c
import hashlib
import struct

import numpy as np

from d3d11_debug import F,U,P,InputElement,TextureDesc
from material_shader_probe import selector,disassemble
from material_stage_feasibility import signature
from mesh_sampling_probe import SamplerDesc
from shader_feasibility import inspect_shpk
from shader_input_profiles import program_plan
from static_material_inputs import shader_crc


SHAPE_PATH='shader/sm5/shpk/apricot_shape.shpk'
SHAPE_SHA='8840be69ec20f030f1b7be71098d1dd3b346dcec0318498da330cc3a685549b1'
VERTEX=np.dtype([('position','<f4',4),('color','<i4',4),('uv','<i4',4),('uv2','<i4',4),('extra','<f4',4)])


class EffectRenderer:
    def __init__(self,renderer,pack):
        self.renderer=renderer;raw=pack.read(SHAPE_PATH)
        if hashlib.sha256(raw).hexdigest()!=SHAPE_SHA:raise ValueError('CN Apricot shader package changed')
        self.package=inspect_shpk(raw,True)[1]['_package'];self.plans={};self.samplers={};self.depth_copy=None

    def plan(self,sample):
        key=(sample['color_to_alpha'],sample['soft'])
        if key in self.plans:return self.plans[key]
        overrides={'UvSetCount_Table':'UvSetCount_1','TextureColor1_Table':'TextureColor1_Enable'}
        if key[0]:overrides['TextureColor1_ColorToAlpha_Table']='TextureColor1_ColorToAlpha_On'
        if key[1]:overrides['ComputeSoftParticleType_Table']='ComputeSoftParticleType_Enable'
        overrides={shader_crc(k):shader_crc(v)for k,v in overrides.items()}
        groups=[];seen=set()
        for group in('system','scene','material'):
            values=[]
            for entry in self.package['keys'][group]:
                identifier=int(entry['id'],16);value=overrides.get(identifier,int(entry['default'],16))
                if identifier in overrides:seen.add(identifier)
                values.append(value)
            groups.append(selector(values))
        if seen!=set(overrides):raise ValueError('Original Apricot scene keys are missing')
        route=selector(groups+[selector(self.package['subview_defaults'])])
        if route not in self.package['selectors']:raise ValueError('No original Apricot shader route')
        node_id=self.package['selectors'][route];node=self.package['nodes'][node_id]
        if len(node['passes'])!=1:raise NotImplementedError('Multi-pass Apricot route')
        pass_id,vi,pi,hs,ds,gs=node['passes'][0]
        if any(value!=0xffffffff for value in(hs,ds,gs)):raise NotImplementedError('Additional Apricot shader stage')
        result={'node':node_id,'pass':pass_id,'selector':route}
        for stage,index in(('vertex',vi),('pixel',pi)):
            program=self.package['stages'][stage][index];plan=program_plan(program,index,stage,True)
            code=program['bytecode'];identity=(stage,plan['code_sha256'])
            if identity not in self.renderer.programs:
                self.renderer.programs[identity]=self.renderer.create(12 if stage=='vertex'else 15,[P,c.c_size_t,P],code,len(code),None)
            result[stage]=(program,plan,self.renderer.programs[identity])
        program,plan,_=result['vertex']
        inputs={(s['name'],s['index'])for s in signature(disassemble(program['bytecode']),False)if s['system']=='NONE'}
        specs=[('POSITION',0,2,0),('TEXCOORD',0,4,16),('TEXCOORD',1,4,32),('TEXCOORD',2,4,48),('TEXCOORD',3,2,64)]
        # D3D validates the complete declared input signature, including the
        # extra channels that this source-selected program does not read.
        if {(n,i)for n,i,_,_ in specs}!=inputs:raise ValueError('Unknown Apricot vertex layout')
        elements=(InputElement*len(specs))(*[InputElement(n.encode(),i,f,0,o,0,0)for n,i,f,o in specs])
        code=program['bytecode'];result['layout']=self.renderer.create(11,[P,U,P,c.c_size_t],elements,len(specs),code,len(code))
        if result['vertex'][1]['resources']or result['pixel'][1]['output_slots']!=[0,1]:raise ValueError('Unknown original Apricot resource/output contract')
        self.plans[key]=result;return result

    def sampler(self,sample):
        key=(sample['filter'],sample['address'])
        if key not in self.samplers:
            mode,address=key
            if mode not in(0,1,2,3,4)or any(a not in(0,1,2)for a in address):raise ValueError('Invalid AVFX sampler')
            native_address=tuple({0:1,1:3,2:2}[v]for v in address)
            descriptor=SamplerDesc(0 if mode==0 else 21 if mode==1 else 85,*native_address,3,0.,(1,1,2,4,8)[mode],1,(F*4)(0,0,0,0),0,15)
            self.samplers[key]=self.renderer.create(23,[P],c.byref(descriptor))
        return self.samplers[key]

    def neutral(self):
        r=self.renderer;key=('apricot-neutral-postprocess',)
        if key not in r.cache:r.cache[key]=r.texture_data(1,1,1,1,28,[b'\xff'*4],[4])
        return r.cache[key]

    def copy_depth(self):
        r=self.renderer
        if self.depth_copy is None:
            descriptor=TextureDesc(r.width,r.height,1,1,39,1,0,0,8,0,0)
            texture=r.create(5,[P,P],c.byref(descriptor),None)
            # D3D11_SHADER_RESOURCE_VIEW_DESC R32_FLOAT / TEXTURE2D.
            view=r.create(7,[P,P],texture,(U*6)(41,4,0,1,0,0));self.depth_copy=(texture,view)
        r.context(47,[P,P],self.depth_copy[0],r.depth)
        return self.depth_copy[1]

    def blend(self,mode):
        r=self.renderer;key=('apricot-blend',mode)
        if key not in r.cache:
            # Defined fixed-preview RGBA composition. Native draw-state
            # capture has not been performed for these AVFX routes.
            src,dst,operation={0:(5,6,1),1:(1,3,1),2:(5,2,1),3:(5,2,3),4:(2,4,1),8:(2,1,1)}[mode]
            targets=[1,src,dst,operation,1,2,1,7]+[0,1,0,1,1,0,1,15]*7
            r.cache[key]=r.create(20,[P],(U*66)(0,0,*targets))
        return r.cache[key]

    def depth(self,sample):
        r=self.renderer;key=('apricot-depth',sample['depth_test'],sample['depth_write'])
        if key not in r.cache:
            comparison=7 if r.clear_depth==0 else 4
            data=struct.pack('<4I2B2x8I',int(key[1]),int(key[2]),comparison,0,255,255,*([1,1,1,8]*2))
            r.cache[key]=r.create(21,[P],c.create_string_buffer(data))
        return r.cache[key]

    def draw(self,scene,samples,camera,view):
        if not samples:return []
        r=self.renderer;depth=self.copy_depth()if any(s['soft']for s in samples)else None
        projection=camera[288//4:352//4].reshape(4,4).astype(float)
        vp=projection@view;neutral=self.neutral();result=[]
        ordered=sorted(samples,key=lambda s:float((view@s['parent']@np.r_[s['local'][:3,3],1])[2]))
        for sample in ordered:
            plan=self.plan(sample);parent=sample['parent'];local=sample['local'];positions=sample['positions']
            if sample['frame']in(5,6):
                # Static camera-facing plane; local spin/scale is retained.
                matrix=np.eye(4);matrix[:3,:3]=view[:3,:3].T@local[:3,:3]
                matrix[:3,3]=(parent@np.r_[local[:3,3],1])[:3]
            else:matrix=parent@local
            positions=(matrix@np.column_stack((positions,np.ones(len(positions)))).T).T[:,:3]
            vertices=np.zeros(len(positions),dtype=VERTEX);vertices['position'][:,:3]=positions;vertices['position'][:,3]=sample['depth_offset']
            for name,values in(('color',sample['color']),('uv',np.pad(sample['uv'],((0,0),(0,2))))):
                packed=np.rint(np.asarray(values)*1000)
                if not np.isfinite(packed).all()or np.any(abs(packed)>2**31-1):raise ValueError('Apricot quantized vertex value exceeds its range')
                vertices[name]=packed.astype('<i4')
            vb=r.temporary(r.buffer(vertices,1));indices=np.array((0,1,2,0,2,3),dtype='<u4');ib=r.temporary(r.buffer(indices,2))
            r.context(17,[P],plan['layout']);r.context(24,[U],4)
            r.context(18,[U,U,P,P,P],0,1,c.byref(vb),c.byref(U(VERTEX.itemsize)),c.byref(U(0)))
            r.context(19,[P,U,U],ib,42,0)
            r.context(11,[P,P,U],plan['vertex'][2],None,0);r.context(9,[P,P,U],plan['pixel'][2],None,0)
            doc=np.zeros(20,dtype='<f4');doc[:4]=(r.width,r.height,1,.1);doc[4:6]=(r.width,r.height);doc[8:12]=1
            constants={'g_VS_ViewProjectionMatrix':np.asarray(vp.T,dtype='<f4').tobytes(),
                'g_VS_ProjectionInverseMatrix':np.asarray(np.linalg.inv(projection).T,dtype='<f4').tobytes(),
                'g_PS_ViewProjectionInverseMatrix':np.asarray(np.linalg.inv(vp).T,dtype='<f4').tobytes(),
                'g_PS_InstanceExtraParameters':struct.pack('<8f',0,1,1,0,sample['blend'],0,0,0),
                'g_PS_DocumentParameters':doc.tobytes(),'g_ToneMapParameter':struct.pack('<4f',0,0,0,1),
                'g_PS_DofCocParam':bytes(16)}
            for stage,setter in(('vertex',7),('pixel',16)):
                for entry in plan[stage][1]['constants']:
                    payload=constants.get(entry['name'])
                    if payload is None or len(payload)!=entry['bytes']:raise ValueError('Unresolved original Apricot constant: '+entry['name'])
                    buffer=r.temporary(r.buffer(np.frombuffer(payload,dtype=np.uint8),4));r.context(setter,[U,U,P],entry['slot'],1,c.byref(buffer))
            sources={'g_SamplerColor1':r.source_texture(scene.pack,sample['texture']),
                'g_ToneMapSampler':neutral,'g_DofLutSampler':neutral,'g_SamplerDepth':depth}
            sampler=self.sampler(sample);neutral_sampler=r.sampler('g_SamplerGBuffer',{})
            for entry in plan['pixel'][1]['resources']:
                resource=sources.get(entry['name'])
                if resource is None:raise ValueError('Unresolved original Apricot texture: '+entry['name'])
                r.context(8,[U,U,P],entry['slot'],1,c.byref(resource))
            for entry in plan['pixel'][0]['bindings']['samplers']:
                selected=sampler if entry['name']=='g_SamplerColor1'else neutral_sampler
                r.context(10,[U,U,P],entry['slot'],1,c.byref(selected))
            r.context(35,[P,P,U],self.blend(sample['blend']),None,0xffffffff);r.context(36,[P,U],self.depth(sample),0)
            r.context(43,[P],r.rasters[False]);r.context(12,[U,U,c.c_int],len(indices),0,0)
            result.append({'path':sample['effect_path'],'particle':sample['source_particle'],'age':sample['source_age'],
                'texture':sample['texture'],'node':plan['node'],'vertex':plan['vertex'][1]['index'],'pixel':plan['pixel'][1]['index']})
        r.last_pass_counts['equipment_effects']=len(result)
        return result
