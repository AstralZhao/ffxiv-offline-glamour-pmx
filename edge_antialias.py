"""Optional spatial SMAA 1x; no history, sharpening or whole-image blur."""
import ctypes as c
import numpy as np
from d3d11_debug import P,U,F,Viewport,BufferDesc,InitialData,TextureDesc,compile_shader
from mesh_sampling_probe import SamplerDesc

WRAPPER=r'''
Texture2D colorTex : register(t0);
Texture2D edgesTex : register(t1);
Texture2D areaTex : register(t2);
Texture2D searchTex : register(t3);
Texture2D blendTex : register(t4);
float2 triangleUV(uint id) { return float2((id<<1)&2,id&2); }
float4 trianglePosition(float2 uv) { return float4(uv*float2(2,-2)+float2(-1,1),0,1); }
struct Edges {float4 position:SV_POSITION;float2 uv:TEXCOORD0;float4 offset[3]:TEXCOORD1;};
struct Weights {float4 position:SV_POSITION;float2 uv:TEXCOORD0;float2 pixel:TEXCOORD1;float4 offset[3]:TEXCOORD2;};
struct Blend {float4 position:SV_POSITION;float2 uv:TEXCOORD0;float4 offset:TEXCOORD1;};
Edges edgesVS(uint id:SV_VertexID) {Edges o;o.uv=triangleUV(id);o.position=trianglePosition(o.uv);SMAAEdgeDetectionVS(o.uv,o.offset);return o;}
float4 edgesPS(Edges i):SV_TARGET {return float4(SMAALumaEdgeDetectionPS(i.uv,i.offset,colorTex),0,0);}
Weights weightsVS(uint id:SV_VertexID) {Weights o;o.uv=triangleUV(id);o.position=trianglePosition(o.uv);SMAABlendingWeightCalculationVS(o.uv,o.pixel,o.offset);return o;}
float4 weightsPS(Weights i):SV_TARGET {return SMAABlendingWeightCalculationPS(i.uv,i.pixel,i.offset,edgesTex,areaTex,searchTex,0);}
Blend blendVS(uint id:SV_VertexID) {Blend o;o.uv=triangleUV(id);o.position=trianglePosition(o.uv);SMAANeighborhoodBlendingVS(o.uv,o.offset);return o;}
float4 blendPS(Blend i):SV_TARGET {return SMAANeighborhoodBlendingPS(i.uv,i.offset,colorTex,blendTex);}
'''


class EdgeAntialias:
    def __init__(self,renderer):
        self.r=renderer;self.owned=[];self.sized=[];self.size=None;self.source=None;self.source_srv=None
        try:
            from smaa_source import shader_source,lookup_tables
            code=shader_source();self.programs={}
            # Explicit registers adapt the upstream effect samplers to the
            # standalone D3DCompile host; the SMAA algorithm is unchanged.
            code=code.replace('SamplerState LinearSampler {','SamplerState LinearSampler : register(s0) {').replace('SamplerState PointSampler {','SamplerState PointSampler : register(s1) {')
            prefix='cbuffer Metrics : register(b0) {float4 metrics;}\n#define SMAA_RT_METRICS metrics\n#define SMAA_HLSL_4\n#define SMAA_PRESET_HIGH\n'
            source=(prefix+code+WRAPPER).encode('utf8')
            for name in ('edges','weights','blend'):
                self.programs[name]=tuple(self.keep(renderer.create(index,[P,c.c_size_t,P],raw,len(raw),None))
                    for entry,target,index in ((name+'VS','vs_5_0',12),(name+'PS','ps_5_0',15))
                    for raw in (compile_shader(entry.encode(),target.encode(),source),))
            self.linear=self.sampler(20);self.point=self.sampler(0)
            self.metrics=self.keep(renderer.create(3,[P,P],c.byref(BufferDesc(16,0,4,0,0,0)),None))
            self.area,self.search=[self.lookup(w,h,fmt,data)for w,h,fmt,data in lookup_tables()]
        except Exception:self.close();raise

    def keep(self,value):self.owned.append(value);return value

    def release(self,value):
        self.owned.remove(value);self.r.resources.remove(value);self.r.probe.release(value)

    def sampler(self,filter_):
        return self.keep(self.r.create(23,[P],c.byref(SamplerDesc(filter_,3,3,3,0,1,1,(F*4)(0,0,0,0),0,0))))

    def lookup(self,w,h,fmt,data):
        backing=c.create_string_buffer(data);pitch=len(data)//h;initial=InitialData(c.cast(backing,P),pitch,len(data))
        texture=self.keep(self.r.create(5,[P,P],c.byref(TextureDesc(w,h,1,1,fmt,1,0,1,8,0,0)),c.byref(initial)))
        return self.keep(self.r.create(7,[P,P],texture,None))

    def prepare(self):
        r=self.r
        if self.size==(r.width,r.height):return
        r.context(110,[])
        for value in self.sized[::-1]:self.release(value)
        self.sized=[];self.size=None;self.targets={}
        mark=len(self.owned)
        try:
            for name,fmt in (('edges',28),('weights',28),('blend',10)):
                texture=self.keep(r.texture(fmt,40));rtv=self.keep(r.create(9,[P,P],texture,None));srv=self.keep(r.create(7,[P,P],texture,None))
                self.targets[name]=(texture,rtv,srv);self.sized.extend((texture,rtv,srv))
            data=np.array((1/r.width,1/r.height,r.width,r.height),dtype='<f4')
            r.context(48,[P,U,P,P,U,U],self.metrics,0,None,data.ctypes.data,0,0)
        except Exception:
            for value in self.owned[mark:][::-1]:self.release(value)
            self.sized=[];self.targets={};raise
        self.size=(r.width,r.height)

    def apply(self,texture):
        self.prepare();r=self.r
        if self.source is None or self.source.value!=texture.value:
            if self.source_srv is not None:self.release(self.source_srv)
            self.source_srv=self.keep(r.create(7,[P,P],texture,None));self.source=texture
        for name in ('edges','weights','blend'):
            r.context(110,[]);target=self.targets[name]
            r.context(50,[P,P],target[1],(F*4)(0,0,0,0))
            r.context(33,[U,P,P],1,c.byref(target[1]),None)
            r.context(44,[U,P],1,c.byref(Viewport(0,0,r.width,r.height,0,1)))
            r.context(43,[P],r.rasters[False]);r.context(35,[P,P,U],None,None,0xffffffff)
            r.context(17,[P],None);r.context(24,[U],4)
            vs,ps=self.programs[name];r.context(11,[P,P,U],vs,None,0);r.context(9,[P,P,U],ps,None,0)
            r.context(7,[U,U,P],0,1,c.byref(self.metrics));r.context(16,[U,U,P],0,1,c.byref(self.metrics))
            samplers=(P*2)(self.linear,self.point);r.context(10,[U,U,P],0,2,samplers)
            views=(P*5)(self.source_srv if name!='weights'else None,self.targets['edges'][2]if name=='weights'else None,
                self.area if name=='weights'else None,self.search if name=='weights'else None,self.targets['weights'][2]if name=='blend'else None)
            r.context(8,[U,U,P],0,5,views);r.context(13,[U,U],3,0)
        r.context(110,[])
        return self.targets['blend'][0]

    def close(self):
        self.r.context(110,[])
        for value in self.owned[::-1]:self.r.resources.remove(value);self.r.probe.release(value)
        self.owned=[];self.sized=[];self.source=None;self.source_srv=None;self.size=None
