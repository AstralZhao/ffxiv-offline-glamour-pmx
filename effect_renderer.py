"""Original installed CN Apricot programs on the independent wardrobe device.

This frozen preview has no scene fog, exposure or depth of field. Neutral
postprocess inputs are defined explicitly; no game buffers/hooks are used.
"""
import ctypes as c
import hashlib
import struct

import numpy as np

from d3d11_debug import F,U,P,InputElement,TextureDesc,RasterizerDesc
from material_shader_probe import selector,disassemble
from material_stage_feasibility import signature
from mesh_sampling_probe import SamplerDesc
from shader_feasibility import inspect_shpk
from shader_input_profiles import program_plan
from static_material_inputs import shader_crc
from effect_sample import UnsupportedEffect,rotation


SHAPE_PATH='shader/sm5/shpk/apricot_shape.shpk'
SHAPE_SHA='8840be69ec20f030f1b7be71098d1dd3b346dcec0318498da330cc3a685549b1'
MODEL_PATH='shader/sm5/shpk/apricot_model.shpk'
MODEL_SHA='b1d91a2da0006aafa97a596966bbf15bf271f6840b800141574b3a6e8a14f10f'
POWDER_PATH='shader/sm5/shpk/apricot_powder.shpk'
POWDER_SHA='7716804edfa5dcff15aad2675991f76c7d33f4b3a3f9e277676e85ea27bd1fed'
VERTEX=np.dtype([('position','<f4',4),('color','<i4',4),('uv','<i4',4),('uv2','<i4',4),('extra','<f4',4)])
MODEL_VERTEX=np.dtype([('position','<f4',4),('normal','<f4',3),('tangent','<f4',3),('color','<f4',4),('uv','<f4',4),('uv2','<f4',4)])
POWDER_VERTEX=np.dtype([('position','<f4',3),('angles','<f4',3),('center','<f4',3),('uv','<f4',2),('color','<i4',4)])


def quantize_vertex(value,*,color=False):
    # Native shape/Powder packed inputs are signed 16-bit integers. Expand
    # them to the original VS int signature with the SAME truncation/wrap,
    # rather than rounding float values into an unbounded 32-bit input.
    packed=np.asarray(value,dtype='<f4')*np.float32(1000)
    if not np.isfinite(packed).all():raise ValueError('Non-finite Apricot vertex value')
    if color:packed=np.clip(packed,0,10000)
    if np.any(abs(packed)>2**31-1):raise ValueError('Apricot quantized vertex value exceeds its range')
    return ((np.trunc(packed).astype('<i8')+32768)%65536-32768).astype('<i4')


def shape_uv_coordinates(value,matrix):
    # Native 3eab8c centres each shape UV before applying its two original
    # rows. Keep that float32 operation order, including identity matrices.
    matrix=np.asarray(matrix);rows=matrix.astype('<f4');uv=np.asarray(value,dtype='<f4')-np.float32(.5)
    offset=np.asarray(matrix[:,3]+matrix[:,:2]@np.full(2,.5),dtype='<f4')
    return uv[:,0:1]*rows[:,0]+uv[:,1:2]*rows[:,1]+offset


def shader_settings(sample):
    # Original Powder VS has one fixed float2 UV input; all 75 CN nodes use
    # UvSetCount_0. This key is not the source particle's UvSN field.
    settings={'UvSetCount_Table':'UvSetCount_'+str(0 if sample['kind']=='powder'else sample['uv_count'])}
    # CN3aa8a9 reads RMT -> table 215ca40; 385b1d selects bits 8..9
    # from [NoneControl, ModulateAlpha, LerpWhite]. This is independent of
    # texture-layer arithmetic and must be selected before the exact SHPK route.
    final_color=(0,2,0,0,1,0,0,0,0,2,0,0,1)[sample.get('blend',0)]
    settings['ComputeFinalColorType_Table']='ComputeFinalColorType_'+('NoneControl','ModulateAlpha','LerpWhite')[final_color]
    for i,mode in enumerate(sample.get('uv_compute',())):
        if mode:settings[f'UvCompute{i}_Table']='UvCompute_ByPixelPosition'
    for channel,data in sample['textures'].items():
        name='TextureDistortion'if channel=='TD'else'TextureColor'+channel[-1]
        settings[name+'_Table']=name+'_Enable'
        settings[name+'_UvNo_Table']=name+'_Uv_'+str(data['uv'])
        if channel=='TD':
            for i,enabled in enumerate(data['distort_uv']):
                if enabled:settings[f'TextureDistortion_UvSet{i}_Table']='TextureDistortion_UvSet_Enable'
        else:
            if data.get('screen_copy'):settings[name+'_Decode_Table']=name+'_Decode_On'
            if data['color_to_alpha']:settings[name+'_ColorToAlpha_Table']=name+'_ColorToAlpha_On'
            if channel!='TC1':
                settings[name+'_CalculateColor_Table']=name+'_CalculateColor_'+('Mul','Add','Sub','Max','Min','None')[data['color_mode']]
                settings[name+'_CalculateAlpha_Table']=name+'_CalculateAlpha_'+('Mul','Max','Min','None')[data['alpha_mode']]
    if sample['soft']:settings['ComputeSoftParticleType_Table']='ComputeSoftParticleType_Enable'
    return settings


def document_parameters(sample,width,height):
    result=np.zeros(20,dtype='<f4');result[:4]=(width,height,1,sample['soft_range']if sample['soft']else 0)
    result[4:6]=(width,height);result[8:12]=1
    return result


def tree_basis(matrix,camera):
    # CN3e3d68..3e3ff4 reads the ALREADY composed matrix's Y axis.
    # It replaces its orientation with these three independently normalized
    # axes; the authored rotation is not multiplied into the result again.
    matrix=np.asarray(matrix,dtype='<f4');up=matrix[:3,1].copy()
    delta=matrix[:3,3]-np.asarray(camera,dtype='<f4');right=np.cross(delta,up)
    forward=np.cross(right,up)
    vectors=np.column_stack((right,up,forward));lengths=np.linalg.norm(vectors,axis=0)
    if not np.isfinite(lengths).all()or np.any(lengths<1e-8):raise UnsupportedEffect('树形朝向退化')
    return vectors/lengths


def world_matrix(sample,view):
    parent,local=sample['parent'],sample['local'];matrix=parent@local
    if sample['frame']in(4,5,6):
        basis=view[:3,:3].T.copy()
        if sample['frame']==4:
            facing=basis[:,2].copy();facing[1]=0;length=np.linalg.norm(facing)
            if length<1e-8:raise ValueError('Singular axis-Y billboard camera')
            facing/=length;up=np.array((0.,1.,0.));basis=np.column_stack((np.cross(up,facing),up,facing))
        matrix[:3,:3]=basis@np.diag(np.linalg.norm(parent[:3,:3],axis=0))@local[:3,:3]
    elif sample['frame']==9:
        basis=tree_basis(matrix,np.linalg.inv(view)[:3,3])
        scales=np.linalg.norm(parent[:3,:3],axis=0)*sample['local_scale']
        matrix[:3,:3]=basis*scales
    return matrix


def simple_world_positions(sample,matrix):
    centers=sample['simple_centers'];gravity=sample['simple_gravity']
    if not len(centers):return np.empty((0,3))
    world=(matrix@np.column_stack((centers,np.ones(len(centers)))).T).T[:,:3]
    world+=gravity@matrix[:3,:3].T if sample['simple_bind_parent']else gravity
    basis=matrix[:3,:3].copy()
    if not sample['simple_inherit_scale']:
        lengths=np.linalg.norm(basis,axis=0)
        if np.any(lengths<1e-8):raise ValueError('Singular Simple attachment scale')
        basis/=lengths
    # CN3fc4ad: authored sizes are half extents and pivot is a multiplier.
    corners=np.array(((-1,1),(1,1),(-1,-1),(1,-1)))+sample['simple_pivot']
    # Powder's original VS rotates Z -> X -> Y, independent of the parent's
    # separately composed transform. Preserve that order for resolved corners.
    rotations=np.array([rotation(value,2)for value in sample['simple_angles']])
    plane=np.pad(corners[None,:,:]*sample['simple_sizes'][:,None,:],((0,0),(0,0),(0,1)))
    offsets=np.einsum('ij,njk,nvk->nvi',basis,rotations,plane)
    return (world[:,None,:]+offsets).reshape(-1,3)


class EffectRenderer:
    def __init__(self,renderer,pack):
        self.renderer=renderer;self.pack=pack;self.packages={};self.plans={};self.samplers={};self.depth_copy=None;self.depth_size=None
        self.color_copy=None;self.color_size=None

    def plan(self,sample):
        kind=sample['kind']if sample['kind']in('model','powder')else'shape';settings=shader_settings(sample)
        key=(kind,tuple(sorted(settings.items())))
        if key in self.plans:return self.plans[key]
        if kind not in self.packages:
            path,expected={'model':(MODEL_PATH,MODEL_SHA),'powder':(POWDER_PATH,POWDER_SHA),'shape':(SHAPE_PATH,SHAPE_SHA)}[kind]
            raw=self.pack.read(path)
            if hashlib.sha256(raw).hexdigest()!=expected:raise ValueError('CN Apricot shader package changed')
            self.packages[kind]=inspect_shpk(raw,True)[1]['_package']
        package=self.packages[kind];overrides={shader_crc(k):shader_crc(v)for k,v in settings.items()}
        groups=[];seen=set()
        for group in('system','scene','material'):
            values=[]
            for entry in package['keys'][group]:
                identifier=int(entry['id'],16);value=overrides.get(identifier,int(entry['default'],16))
                if identifier in overrides:seen.add(identifier)
                values.append(value)
            groups.append(selector(values))
        if seen!=set(overrides):raise ValueError('Original Apricot scene keys are missing')
        route=selector(groups+[selector(package['subview_defaults'])])
        if route not in package['selectors']:raise UnsupportedEffect('原始特效着色组合尚未接入')
        node_id=package['selectors'][route];node=package['nodes'][node_id]
        if len(node['passes'])!=1:raise NotImplementedError('Multi-pass Apricot route')
        pass_id,vi,pi,hs,ds,gs=node['passes'][0]
        if any(value!=0xffffffff for value in(hs,ds,gs)):raise NotImplementedError('Additional Apricot shader stage')
        result={'node':node_id,'pass':pass_id,'selector':route}
        for stage,index in(('vertex',vi),('pixel',pi)):
            program=package['stages'][stage][index];plan=program_plan(program,index,stage,True)
            code=program['bytecode'];identity=(stage,plan['code_sha256'])
            if identity not in self.renderer.programs:
                self.renderer.programs[identity]=self.renderer.create(12 if stage=='vertex'else 15,[P,c.c_size_t,P],code,len(code),None)
            result[stage]=(program,plan,self.renderer.programs[identity])
        program,plan,_=result['vertex']
        inputs={(s['name'],s['index'])for s in signature(disassemble(program['bytecode']),False)if s['system']=='NONE'}
        specs=([('POSITION',0,2,0),('NORMAL',0,6,16),('TANGENT',0,6,28),('COLOR',0,2,40),('TEXCOORD',0,2,56),('TEXCOORD',1,2,72)]if kind=='model'else
            [('POSITION',0,6,0),('TEXCOORD',0,6,12),('TEXCOORD',1,6,24),('TEXCOORD',2,16,36),('TEXCOORD',3,4,44)]if kind=='powder'else
            [('POSITION',0,2,0),('TEXCOORD',0,4,16),('TEXCOORD',1,4,32),('TEXCOORD',2,4,48),('TEXCOORD',3,2,64)])
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
        if self.depth_copy is not None and self.depth_size!=(r.width,r.height):
            for pointer in reversed(self.depth_copy):r.resources.remove(pointer);r.probe.release(pointer)
            self.depth_copy=None
        if self.depth_copy is None:
            descriptor=TextureDesc(r.width,r.height,1,1,39,1,0,0,8,0,0)
            texture=r.create(5,[P,P],c.byref(descriptor),None)
            # D3D11_SHADER_RESOURCE_VIEW_DESC R32_FLOAT / TEXTURE2D.
            view=r.create(7,[P,P],texture,(U*6)(41,4,0,1,0,0));self.depth_copy=(texture,view)
            self.depth_size=(r.width,r.height)
        r.context(47,[P,P],self.depth_copy[0],r.depth)
        return self.depth_copy[1]

    def blend(self,mode):
        r=self.renderer;key=('apricot-blend',mode)
        if key not in r.cache:
            # CN38832f table: 9..12 share the exact RGB branches of 1..4.
            # Packed fields are enable:1, operation:3, src:4, dst:4; D3D
            # factors/operations below use its one-based enums. Preserve the
            # preview emission alpha; the native second DOF target is absent.
            src,dst,operation={0:(5,6,1),1:(1,3,1),2:(5,2,1),3:(5,2,3),4:(2,4,1),
                5:(10,1,1),6:(2,2,4),7:(2,2,5),8:(5,6,1),9:(1,3,1),10:(5,2,1),11:(5,2,3),12:(2,4,1)}[mode]
            targets=[int(mode!=8),src,dst,operation,1,2,1,7]+[0,1,0,1,1,0,1,15]*7
            r.cache[key]=r.create(20,[P],(U*66)(0,0,*targets))
        return r.cache[key]

    def copy_color(self):
        """Snapshot the owned preview, before particles; no CPU readback."""
        r=self.renderer;size=(r.width,r.height)
        if self.color_copy is not None and self.color_size!=size:
            for pointer in reversed(self.color_copy):r.resources.remove(pointer);r.probe.release(pointer)
            self.color_copy=None
        if self.color_copy is None:
            texture=r.create(5,[P,P],c.byref(TextureDesc(*size,1,1,10,1,0,0,8,0,0)),None)
            self.color_copy=(texture,r.create(7,[P,P],texture,None));self.color_size=size
        r.context(47,[P,P],self.color_copy[0],r.color)
        return self.color_copy[1]

    def depth(self,sample):
        r=self.renderer;key=('apricot-depth',sample['depth_test'],sample['depth_write'])
        if key not in r.cache:
            comparison=7 if r.clear_depth==0 else 4
            data=struct.pack('<4I2B2x8I',int(key[1]),int(key[2]),comparison,0,255,255,*([1,1,1,8]*2))
            r.cache[key]=r.create(21,[P],c.create_string_buffer(data))
        return r.cache[key]

    def raster(self,mode):
        if mode not in(0,1,2,3):raise ValueError('Unknown source AVFX culling')
        r=self.renderer;key=('apricot-raster',mode)
        if key not in r.cache:r.cache[key]=r.create(22,[P],c.byref(RasterizerDesc(3,{0:1,1:2,2:3,3:1}[mode],1,0,0,0,1,0,0,0)))
        return r.cache[key]

    def draw(self,scene,samples,camera,view):
        if not samples:return []
        r=self.renderer;depth=self.copy_depth()if any(s['soft']for s in samples)else None
        screen_copy=self.copy_color()if any(t.get('screen_copy')for s in samples for t in s['textures'].values())else None
        projection=camera[288//4:352//4].reshape(4,4).astype(float)
        vp=projection@view;neutral=self.neutral();result=[]
        ordered=sorted(samples,key=lambda s:(s['priority'],float((view@s['parent']@np.r_[s['local'][:3,3],1])[2])))
        for sample in ordered:
            plan=self.plan(sample);matrix=world_matrix(sample,view);positions=sample['positions'];is_model=sample['kind']=='model';is_powder=sample['kind']=='powder'
            if sample.get('disc_parameters',{}).get('scaling',1)<1:
                from effect_disc import geometry as disc_geometry
                positions=disc_geometry(sample['disc_parameters'],np.linalg.norm(matrix[:3,:3],axis=0))['positions']
            dtype=MODEL_VERTEX if is_model else POWDER_VERTEX if is_powder else VERTEX;vertices=np.zeros(len(positions),dtype=dtype)
            world_positions=(simple_world_positions(sample,matrix)if 'simple_centers'in sample else
                (matrix@np.column_stack((positions,np.ones(len(positions)))).T).T[:,:3])
            if is_powder:
                # The original VS adds center after rotating the local corner.
                # Supplying the fully resolved corner as center, with zero
                # local xy/angles, is algebraically identical for this path.
                vertices['center']=world_positions;vertices['position'][:,2]=sample['depth_offset']
            else:
                vertices['position'][:,:3]=positions if is_model else world_positions
                vertices['position'][:,3]=1 if is_model else sample['depth_offset']
            uv=sample['base_uv'].copy()
            if not is_model:
                for i,uv_matrix in enumerate(sample['uv_matrices']):uv[:,i]=shape_uv_coordinates(uv[:,i],uv_matrix)
            values={'color':sample['colors']*(1 if is_model else sample['color']),'uv':uv[:,0]if is_powder else uv[:,:2].reshape(-1,4)}
            if not is_powder:values['uv2']=uv[:,2:].reshape(-1,4)
            for name,value in values.items():
                if is_model or(is_powder and name=='uv'):vertices[name]=value;continue
                vertices[name]=quantize_vertex(value,color=name=='color')
            distortion=sample['textures'].get('TD',{});power=distortion.get('power',0)
            if is_model:
                vertices['normal']=sample['normals'];vertices['tangent']=sample['tangents']
            elif not is_powder:vertices['extra']=(power,*distortion.get('distort_uv',[False]*4)[:3])
            create_vertices=r.buffer if is_model else getattr(r,'dynamic_buffer',r.buffer)
            vb=r.temporary(create_vertices(vertices,1));indices=sample['indices'];ib=r.temporary(r.buffer(indices,2))
            r.context(17,[P],plan['layout']);r.context(24,[U],4)
            r.context(18,[U,U,P,P,P],0,1,c.byref(vb),c.byref(U(dtype.itemsize)),c.byref(U(0)))
            r.context(19,[P,U,U],ib,42,0)
            r.context(11,[P,P,U],plan['vertex'][2],None,0);r.context(9,[P,P,U],plan['pixel'][2],None,0)
            doc=document_parameters(sample,r.width,r.height)
            uv_transform=np.tile(np.array(((1,0,0,0),(0,1,0,0)),dtype='<f4'),(4,1))
            for i,value in enumerate(sample['uv_matrices']):uv_transform[i*2:i*2+2]=value
            specific=np.zeros((6,4),dtype='<f4');fresnel=sample.get('fresnel_type',0)
            if fresnel not in(0,1):raise NotImplementedError('Unresolved model Fresnel axis')
            specific[1,:3]=np.linalg.inv(view)[:3,3];specific[1,3]=sample.get('fresnel_power',1)
            specific[2]=sample.get('fresnel_begin',np.ones(4))if fresnel else 1
            specific[3]=sample.get('fresnel_end',np.ones(4))if fresnel else 1
            per_instance=np.r_[matrix.T.flatten(),[sample['depth_offset'],0,0,0],sample['color']].astype('<f4')
            first=sample['textures'].get('TC1',{});calculate_color=first.get('color_mode',1);calculate_alpha=first.get('alpha_mode',1)
            constants={'g_VS_ViewProjectionMatrix':np.asarray(vp.T,dtype='<f4').tobytes(),
                'g_VS_ProjectionInverseMatrix':np.asarray(np.linalg.inv(projection).T,dtype='<f4').tobytes(),
                'g_PS_ViewProjectionInverseMatrix':np.asarray(np.linalg.inv(vp).T,dtype='<f4').tobytes(),
                'g_VS_PerInstanceParameters':per_instance.tobytes(),'g_PS_Parameters':struct.pack('<4f',power,0,0,0),
                'g_PS_UvTransform':uv_transform.tobytes(),'g_PS_ModelSpecificParameters':specific.tobytes(),
                'g_PS_InstanceExtraParameters':struct.pack('<8f',int(fresnel==1),calculate_color,calculate_alpha,0,sample['blend'],0,0,0),
                'g_PS_DocumentParameters':doc.tobytes(),'g_ToneMapParameter':struct.pack('<4f',0,0,0,1),
                'g_PS_DofCocParam':bytes(16)}
            for stage,setter in(('vertex',7),('pixel',16)):
                for entry in plan[stage][1]['constants']:
                    payload=constants.get(entry['name'])
                    if payload is None or len(payload)!=entry['bytes']:raise ValueError('Unresolved original Apricot constant: '+entry['name'])
                    buffer=r.temporary(r.buffer(np.frombuffer(payload,dtype=np.uint8),4));r.context(setter,[U,U,P],entry['slot'],1,c.byref(buffer))
            sources={'g_ToneMapSampler':neutral,'g_DofLutSampler':neutral,'g_SamplerDepth':depth};samplers={}
            for channel,data in sample['textures'].items():
                name='g_SamplerDistortion'if channel=='TD'else'g_SamplerColor'+channel[-1]
                sources[name]=screen_copy if data.get('screen_copy')else r.source_texture(scene.pack,data['path']);samplers[name]=self.sampler(data)
            neutral_sampler=r.sampler('g_SamplerGBuffer',{})
            for entry in plan['pixel'][1]['resources']:
                resource=sources.get(entry['name'])
                if resource is None:raise ValueError('Unresolved original Apricot texture: '+entry['name'])
                r.context(8,[U,U,P],entry['slot'],1,c.byref(resource))
            for entry in plan['pixel'][0]['bindings']['samplers']:
                selected=samplers.get(entry['name'],neutral_sampler)
                r.context(10,[U,U,P],entry['slot'],1,c.byref(selected))
            r.context(35,[P,P,U],self.blend(sample['blend']),None,0xffffffff);r.context(36,[P,U],self.depth(sample),0)
            r.context(43,[P],self.raster(sample['cull']));r.context(12,[U,U,c.c_int],len(indices),0,0)
            result.append({'path':sample['effect_path'],'particle':sample['source_particle'],'age':sample['source_age'],
                'texture':sample['texture'],'kind':sample['kind'],'triangles':len(indices)//3,'emitter':sample['source_emitter'],
                'textures':{k:v['path']for k,v in sample['textures'].items()},'node':plan['node'],'vertex':plan['vertex'][1]['index'],'pixel':plan['pixel'][1]['index']})
        r.last_pass_counts['equipment_effects']=len(result)
        return result
