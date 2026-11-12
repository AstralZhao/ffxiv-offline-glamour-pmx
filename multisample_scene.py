"""Four-sample original material rendering with per-sample GBuffer reads.

Original shaders stay unchanged: each color consumer reads an exact
single-sample extraction, while its output/depth test targets only that MSAA
sample. Material IDs/normals from different surfaces are never averaged.
"""
import ctypes as c
import numpy as np
from d3d11_debug import P,U,F,TextureDesc,BufferDesc,Viewport,RasterizerDesc,compile_shader,check


SOURCE=b'''
cbuffer SampleSelection:register(b0){uint sampleIndex;uint reverseDepth;uint2 padding;};
Texture2DMS<float4,4> layer0:register(t0);
Texture2DMS<float4,4> layer1:register(t1);
Texture2DMS<float4,4> layer2:register(t2);
Texture2DMS<float4,4> layer3:register(t3);
Texture2DMS<float4,4> layer4:register(t4);
Texture2DMS<float,4> sceneDepth:register(t5);
float4 vs(uint id:SV_VertexID):SV_POSITION {
 float2 uv=float2((id<<1)&2,id&2);return float4(uv*float2(2,-2)+float2(-1,1),0,1);
}
struct Layers {float4 a:SV_TARGET0;float4 b:SV_TARGET1;float4 c:SV_TARGET2;float4 d:SV_TARGET3;float4 e:SV_TARGET4;};
Layers loadLayers(int2 p,uint s){Layers o;o.a=layer0.Load(p,s);o.b=layer1.Load(p,s);o.c=layer2.Load(p,s);o.d=layer3.Load(p,s);o.e=layer4.Load(p,s);return o;}
Layers extractPS(float4 p:SV_POSITION){return loadLayers(int2(p.xy),sampleIndex);}
struct Resolved {float4 a:SV_TARGET0;float4 b:SV_TARGET1;float4 c:SV_TARGET2;float4 d:SV_TARGET3;float4 e:SV_TARGET4;float depth:SV_DEPTH;};
Resolved nearestPS(float4 p:SV_POSITION){
 int2 pixel=int2(p.xy);uint selected=0;float depth=sceneDepth.Load(pixel,0);
 [unroll]for(uint s=1;s<4;s++){float candidate=sceneDepth.Load(pixel,s);if(reverseDepth?candidate>depth:candidate<depth){depth=candidate;selected=s;}}
 Layers v=loadLayers(pixel,selected);Resolved o;o.a=v.a;o.b=v.b;o.c=v.c;o.d=v.d;o.e=v.e;o.depth=depth;return o;
}
'''


class MultisampleScene:
    samples=4

    def __init__(self,renderer,*,samples=4):
        if samples not in (1,4):raise ValueError('Unsupported material sample count')
        self.samples=samples;self.r=renderer;self.owned=[];self.sized=[];self.size=None
        try:
            for fmt in (10,87,40):
                levels=U();check(renderer.probe.method(renderer.probe.device,30,c.c_long,U,U,P)(renderer.probe.device,fmt,samples,c.byref(levels)),'Check 4x MSAA format')
                if not levels.value:raise ValueError('当前显卡不支持此预览格式的4× MSAA')
            source=SOURCE
            if samples==1:
                source=source.replace(b'Texture2DMS<float4,4>',b'Texture2D<float4>').replace(b'Texture2DMS<float,4>',b'Texture2D<float>').replace(b'.Load(p,s)',b'.Load(int3(p,0))').replace(b'.Load(pixel,0)',b'.Load(int3(pixel,0))').replace(b'.Load(pixel,s)',b'.Load(int3(pixel,0))').replace(b's<4',b's<1')
            for entry,stage,method in ((b'vs',b'vs_5_0',12),(b'extractPS',b'ps_5_0',15),(b'nearestPS',b'ps_5_0',15)):
                code=compile_shader(entry,stage,source);setattr(self,entry.decode(),self.keep(renderer.create(method,[P,c.c_size_t,P],code,len(code),None)))
            self.constants=self.keep(renderer.create(3,[P,P],c.byref(BufferDesc(16,0,4,0,0,0)),None))
            self.rasters={flag:self.keep(renderer.create(22,[P],c.byref(RasterizerDesc(3,3 if flag else 1,1,0,0,0,1,0,1,0))))for flag in (False,True)}
            # Full-screen sample extraction never depth tests. The final
            # nearest-surface export explicitly writes a single-sample depth.
            import struct
            self.write_depth=self.keep(renderer.create(21,[P],c.create_string_buffer(struct.pack('<4I2B2x8I',1,1,8,0,255,255,*([1,1,1,8]*2)))))
        except Exception:self.close();raise

    def keep(self,pointer):self.owned.append(pointer);return pointer

    def release(self,pointer):
        self.r.resources.remove(pointer);self.owned.remove(pointer);self.r.probe.release(pointer)

    def prepare(self):
        r=self.r;size=(r.width,r.height)
        if size==self.size:return
        start=len(self.owned)
        try:
            layers=[]
            for fmt in (87,87,87,10,87):
                tex=self.keep(r.create(5,[P,P],c.byref(TextureDesc(*size,1,1,fmt,self.samples,0,0,40,0,0)),None))
                layers.append((tex,self.keep(r.create(9,[P,P],tex,None)),self.keep(r.create(7,[P,P],tex,None))))
            color=self.keep(r.create(5,[P,P],c.byref(TextureDesc(*size,1,1,10,self.samples,0,0,32,0,0)),None));rtv=self.keep(r.create(9,[P,P],color,None))
            depth=self.keep(r.create(5,[P,P],c.byref(TextureDesc(*size,1,1,39,self.samples,0,0,72,0,0)),None))
            dsv=self.keep(r.create(10,[P,P],depth,(U*6)(40,5 if self.samples>1 else 3,0,0,0,0)))
            depth_srv=self.keep(r.create(7,[P,P],depth,(U*6)(41,6 if self.samples>1 else 4,0,1 if self.samples==1 else 0,0,0)))
        except Exception:
            for pointer in self.owned[start:][::-1]:self.release(pointer)
            raise
        created=self.owned[start:].copy();r.context(110,[])
        for pointer in self.sized[::-1]:self.release(pointer)
        self.layers=layers;self.color=color;self.rtv=rtv;self.depth=depth;self.dsv=dsv;self.depth_srv=depth_srv;self.size=size;self.sized=created

    def extract(self,index,*,nearest=False):
        r=self.r;r.context(110,[])
        r.context(33,[U,P,P],5,(P*5)(*[v[1]for v in r.gbuffers]),r.dsv if nearest else None)
        r.context(44,[U,P],1,c.byref(Viewport(0,0,r.width,r.height,0,1)))
        r.context(43,[P],self.rasters[False]);r.context(36,[P,U],self.write_depth if nearest else None,0)
        r.context(11,[P,P,U],self.vs,None,0);r.context(9,[P,P,U],self.nearestPS if nearest else self.extractPS,None,0)
        r.context(24,[U],4);r.context(8,[U,U,P],0,6,(P*6)(*[v[2]for v in self.layers],self.depth_srv))
        values=(U*4)(index,int(r.clear_depth==0),0,0);r.context(48,[P,U,P,P,U,U],self.constants,0,None,values,0,0)
        r.context(16,[U,U,P],0,1,c.byref(self.constants));r.context(13,[U,U],3,0);r.context(110,[])

    def draw(self,scene,meshes,shared,view):
        r=self.r;self.prepare();old_rasters=r.rasters;r.rasters=self.rasters
        try:
            if not r.gbuffers:
                for fmt in (87,87,87,10,87):
                    tex=r.texture(fmt,40);r.gbuffers.append((tex,r.create(9,[P,P],tex,None),r.create(7,[P,P],tex,None)))
            r.context(110,[]);r.context(44,[U,P],1,c.byref(Viewport(0,0,r.width,r.height,0,1)))
            r.context(50,[P,P],self.rtv,(F*4)(*getattr(r,'studio_background',(.035,.045,.065,0))))
            r.context(53,[P,U,F,c.c_ubyte],self.dsv,1,r.clear_depth,0)
            for _,rtv,_ in self.layers:r.context(50,[P,P],rtv,(F*4)(0,0,0,0))
            r.context(33,[U,P,P],5,(P*5)(*[v[1]for v in self.layers]),self.dsv)
            r.context(36,[P,U],r.depth_state,0);r.context(35,[P,P,U],None,None,0xffffffff)
            solid=[m for m in meshes if not m['overlay']and not m.get('forward_transparent')]
            for mesh in solid:r.draw_mesh(scene,{**mesh,'plan':r.render_plan(scene,mesh,'material')},shared,view)
            for role,blend in (('overlay_tattoo',r.alpha_blend),('overlay_occlusion',r.multiply_blend)):
                slots=[None,None,self.layers[2][1]]if role=='overlay_tattoo'else[None,self.layers[1][1],self.layers[2][1]]
                r.context(33,[U,P,P],3,(P*3)(*slots),self.dsv);r.context(36,[P,U],r.transparent_depth_state,0);r.context(35,[P,P,U],blend,None,0xffffffff)
                for mesh in meshes:
                    if mesh['overlay']==role:r.draw_mesh(scene,mesh,shared,view)
            for index in range(self.samples):
                self.extract(index)
                r.context(33,[U,P,P],1,c.byref(self.rtv),self.dsv)
                r.context(44,[U,P],1,c.byref(Viewport(0,0,r.width,r.height,0,1)))
                r.context(35,[P,P,U],None,None,1<<index);r.context(36,[P,U],r.equal_depth_state,0)
                for mesh in solid:r.draw_mesh(scene,{**mesh,'plan':r.render_plan(scene,mesh,'gbuffer_color')},shared,view)
            r.context(35,[P,P,U],r.fringe_blend,None,0xffffffff);r.context(36,[P,U],r.fringe_depth_state,0)
            for mesh in r.transparent_meshes_for_draw(meshes,view):
                triangles=mesh['indices'].reshape(-1,3);centers=mesh['posed_positions'][triangles].mean(axis=1);depth=centers@view[2,:3]+view[2,3]
                selected={**mesh,'indices':triangles[np.argsort(depth,kind='stable')].ravel(),'plan':r.render_plan(scene,mesh,'direct')}
                if mesh.get('forward_transparent'):r.draw_forward_transparent(scene,selected,shared,view)
                else:r.draw_mesh(scene,selected,shared,view)
            if hasattr(r,'draw_studio_points'):r.draw_studio_points(shared['g_CameraParameter'],view,multisample=self)
            r.context(110,[])
            if self.samples>1:r.context(57,[P,U,P,U,U],r.color,0,self.color,0,10)
            else:r.context(47,[P,P],r.color,self.color)
            self.extract(0,nearest=True)
            r.context(33,[U,P,P],1,c.byref(r.rtv),r.dsv);r.context(44,[U,P],1,c.byref(Viewport(0,0,r.width,r.height,0,1)))
            r.last_multisample={'samples':self.samples,'size':list(self.size),'original_shaders_unchanged':True,'per_sample_color_consumers':True,'post_depth':'nearest source sample','post_layers':'same nearest sample, no material-ID averaging'}
        finally:r.rasters=old_rasters

    def close(self):
        self.r.context(110,[])
        for pointer in self.owned[::-1].copy():self.release(pointer)
        self.sized=[];self.size=None
