"""Authored, non-shadowing local lights over the native material layers.

This deliberately small studio shader is not the game's pointlighting program.
All three lights share one pass; MSAA reads matching individual samples.
"""
import ctypes as c
import numpy as np
from d3d11_debug import P,U,F,TextureDesc,BufferDesc,Viewport,RasterizerDesc,compile_shader
from studio_lighting import LIGHT_TYPES

SOURCE=r'''
cbuffer Lights:register(b0) {
 row_major float4x4 inverseViewProjection;
 float4 metrics;
 float4 cameraPosition;
 float4 positions[3];
 float4 colors[3];
};
#if SAMPLES == 4
Texture2DMS<float4,4> sourceColor:register(t0);
Texture2DMS<float4,4> normals:register(t1);
Texture2DMS<float4,4> material:register(t2);
Texture2DMS<float4,4> albedo:register(t3);
Texture2DMS<float,4> sceneDepth:register(t4);
#define LOAD(tex,p,s) tex.Load(p,s)
#else
Texture2D<float4> sourceColor:register(t0);
Texture2D<float4> normals:register(t1);
Texture2D<float4> material:register(t2);
Texture2D<float4> albedo:register(t3);
Texture2D<float> sceneDepth:register(t4);
#define LOAD(tex,p,s) tex.Load(int3(p,0))
#endif
float4 vs(uint id:SV_VertexID):SV_POSITION {
 float2 uv=float2((id<<1)&2,id&2);return float4(uv*float2(2,-2)+float2(-1,1),0,1);
}
float4 shade(float4 screen,uint sampleIndex) {
 int2 pixel=int2(screen.xy);float4 original=LOAD(sourceColor,pixel,sampleIndex);
 float depth=LOAD(sceneDepth,pixel,sampleIndex);float4 packedNormal=LOAD(normals,pixel,sampleIndex);
 if(depth==metrics.z || packedNormal.w==0)return original;
 float2 uv=screen.xy/metrics.xy;
 float4 location=mul(inverseViewProjection,float4(uv*float2(2,-2)+float2(-1,1),depth,1));
 float3 p=location.xyz/location.w;
 float3 n=packedNormal.xyz*2-1;float normalLength=dot(n,n);
 if(normalLength<.01)return original;n*=rsqrt(normalLength);
 float4 properties=LOAD(material,pixel,sampleIndex);
 float3 base=LOAD(albedo,pixel,sampleIndex).rgb;base*=base;
 float roughness=clamp(properties.y,.12,1);
 float3 v=cameraPosition.xyz-p;v*=rsqrt(max(dot(v,v),1e-6));float3 contribution=0;
 [unroll]for(uint i=0;i<3;i++){
  float3 delta=positions[i].xyz-p;float d2=dot(delta,delta);float distance=sqrt(max(d2,1e-6));
  float3 l=delta/max(distance,.001);float ndl=saturate(dot(n,l));
  float rangeWeight=saturate(1-distance/max(positions[i].w,.001));
  float attenuation=rangeWeight*rangeWeight/(1+.35*d2);
  float3 h=l+v;h*=rsqrt(max(dot(h,h),1e-6));
  float specular=pow(saturate(dot(n,h)),lerp(96,4,roughness*roughness))*(1-roughness)*.35;
  contribution+=colors[i].rgb*colors[i].w*attenuation*ndl*(base*saturate(properties.x)+specular);
 }
 // The native offscreen color and albedo use square-root display encoding.
 // Keep source alpha/emission untouched; never illuminate background pixels.
 return float4(sqrt(max(original.rgb*original.rgb+contribution,0)),original.a);
}
#if SAMPLES == 4
float4 ps(float4 p:SV_POSITION,uint sampleIndex:SV_SampleIndex):SV_TARGET{return shade(p,sampleIndex);}
#else
float4 ps(float4 p:SV_POSITION):SV_TARGET{return shade(p,0);}
#endif
'''


class StudioPointLights:
    def __init__(self,renderer):
        self.r=renderer;self.owned=[];self.sized=[];self.size=None;self.programs={}
        try:
            self.constants=self.keep(renderer.create(3,[P,P],c.byref(BufferDesc(192,0,4,0,0,0)),None))
            self.raster=self.keep(renderer.create(22,[P],c.byref(RasterizerDesc(3,1,1,0,0,0,1,0,1,0))))
        except Exception:self.close();raise

    def keep(self,p):self.owned.append(p);return p

    def release(self,p):self.owned.remove(p);self.r.resources.remove(p);self.r.probe.release(p)

    def prepare(self,samples):
        r=self.r;key=(r.width,r.height,samples)
        if samples not in self.programs:
            code=('#define SAMPLES '+str(samples)+'\n'+SOURCE).encode();made=[]
            try:
                for entry,stage,method in ((b'vs',b'vs_5_0',12),(b'ps',b'ps_5_0',15)):
                    raw=compile_shader(entry,stage,code);made.append(self.keep(r.create(method,[P,c.c_size_t,P],raw,len(raw),None)))
            except Exception:
                for p in made[::-1]:self.release(p)
                raise
            self.programs[samples]=tuple(made)
        if self.size==key:return
        mark=len(self.owned)
        try:
            color=self.keep(r.create(5,[P,P],c.byref(TextureDesc(r.width,r.height,1,1,10,samples,0,0,8,0,0)),None))
            source=self.keep(r.create(7,[P,P],color,None))
            depth=self.keep(r.create(5,[P,P],c.byref(TextureDesc(r.width,r.height,1,1,39,samples,0,0,8,0,0)),None))
            depth_srv=self.keep(r.create(7,[P,P],depth,(U*6)(41,6 if samples==4 else 4,0,1 if samples==1 else 0,0,0)))
        except Exception:
            for p in self.owned[mark:][::-1]:self.release(p)
            raise
        created=self.owned[mark:].copy();r.context(110,[])
        for p in self.sized[::-1]:self.release(p)
        self.color,self.source,self.depth,self.depth_srv=color,source,depth,depth_srv;self.sized=created;self.size=key

    def draw(self,camera,view,*,multisample=None):
        r=self.r;samples=multisample.samples if multisample else 1;self.prepare(samples)
        color,target,depth,layers=(multisample.color,multisample.rtv,multisample.depth,multisample.layers)if multisample else(r.color,r.rtv,r.depth,r.gbuffers)
        r.context(110,[]);r.context(47,[P,P],self.color,color);r.context(47,[P,P],self.depth,depth)
        camera=np.frombuffer(camera,dtype='<f4')if isinstance(camera,(bytes,bytearray))else np.asarray(camera)
        data=np.zeros(48,dtype='<f4');data[:16]=camera[160//4:160//4+16]
        data[16:20]=(r.width,r.height,r.clear_depth,0)
        data[20:24]=(*(-view[:3,:3].T@view[:3,3]),1)
        for i,row in enumerate(r.studio_options['lights']):
            enabled,kind,red,green,blue,x,y,z=row
            _,intensity,radius=LIGHT_TYPES[kind];data[24+i*4:28+i*4]=(x,y,z,radius)
            data[36+i*4:40+i*4]=(red/255,green/255,blue/255,intensity if enabled else 0)
        r.context(48,[P,U,P,P,U,U],self.constants,0,None,data.ctypes.data,0,0)
        r.context(33,[U,P,P],1,c.byref(target),None);r.context(44,[U,P],1,c.byref(Viewport(0,0,r.width,r.height,0,1)))
        r.context(43,[P],self.raster);r.context(36,[P,U],None,0);r.context(35,[P,P,U],None,None,0xffffffff)
        r.context(17,[P],None);r.context(24,[U],4);vs,ps=self.programs[samples]
        r.context(11,[P,P,U],vs,None,0);r.context(9,[P,P,U],ps,None,0);r.context(16,[U,U,P],0,1,c.byref(self.constants))
        r.context(8,[U,U,P],0,5,(P*5)(self.source,layers[0][2],layers[1][2],layers[2][2],self.depth_srv))
        r.context(13,[U,U],3,0);r.context(110,[])

    def close(self):
        self.r.context(110,[])
        for p in self.owned[::-1].copy():self.release(p)
        self.sized=[];self.size=None;self.programs={}
