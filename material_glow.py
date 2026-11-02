"""GPU-only fixed-preview bloom from the original material emission mask.

The native offscreen PS returns sqrt-encoded RGB and an emission/color ratio
in alpha when its EnvParameter.w emission selector is enabled. Work in linear
RGB for filtering/composition. This is a preview display effect, not a claim
to reproduce the game's complete exposure/bloom/post-processing stack.
"""
import ctypes as c
import numpy as np

from d3d11_debug import P,U,F,TextureDesc,Viewport,compile_shader
from mesh_sampling_probe import SamplerDesc

# Shared display calibration against the CN native try-on target, 2026-11-02.
# The old uncalibrated 2.0 gain made the measured halo about thirteen times
# too strong. This is a preview calibration, not an extracted native uniform
# or a replacement for the still-pending complete Glare/ToneMapping pipeline.
PREVIEW_GLOW_STRENGTH=.15

SOURCE=b'''
struct Output { float4 position:SV_POSITION; float2 uv:TEXCOORD; };
Output vs(uint id:SV_VertexID) {
    Output o;o.uv=float2((id<<1)&2,id&2);
    o.position=float4(o.uv*float2(2,-2)+float2(-1,1),0,1);return o;
}
Texture2D<float4> source:register(t0);Texture2D<float4> glow:register(t1);
SamplerState linearClamp:register(s0);cbuffer Settings:register(b0) { float4 settings; };
float3 emission(float2 uv) {
    float4 c=source.SampleLevel(linearClamp,uv,0);
    return max(c.rgb*c.rgb,0)*saturate(c.a);
}
float4 extract(Output i):SV_TARGET {
    float2 d=settings.xy;
    return float4((emission(i.uv+d*float2(-1,-1))+emission(i.uv+d*float2(1,-1))+
        emission(i.uv+d*float2(-1,1))+emission(i.uv+d*float2(1,1)))*.25,0);
}
float4 blur(Output i):SV_TARGET {
    float3 value=source.SampleLevel(linearClamp,i.uv,0).rgb*.227027027;
    value+=(source.SampleLevel(linearClamp,i.uv+settings.xy*1.384615385,0).rgb+
            source.SampleLevel(linearClamp,i.uv-settings.xy*1.384615385,0).rgb)*.316216216;
    value+=(source.SampleLevel(linearClamp,i.uv+settings.xy*3.230769231,0).rgb+
            source.SampleLevel(linearClamp,i.uv-settings.xy*3.230769231,0).rgb)*.070270270;
    return float4(value,0);
}
float4 compose(Output i):SV_TARGET {
    float4 original=source.SampleLevel(linearClamp,i.uv,0);
    float3 halo=glow.SampleLevel(linearClamp,i.uv,0).rgb;
    return float4(sqrt(max(original.rgb*original.rgb+halo*settings.z,0)),original.a);
}
'''


class MaterialGlow:
    def __init__(self,renderer):
        self.r=renderer;self.owned=[];self.targets=[];self.size=None;self.closed=False;self.draws=0
        try:
            code=compile_shader(b'vs',b'vs_5_0',SOURCE);self.vs=self.create(12,[P,c.c_size_t,P],code,len(code),None)
            self.ps={}
            for name in('extract','blur','compose'):
                code=compile_shader(name.encode(),b'ps_5_0',SOURCE);self.ps[name]=self.create(15,[P,c.c_size_t,P],code,len(code),None)
            self.sampler=self.create(23,[P],c.byref(SamplerDesc(21,3,3,3,0,1,1,(F*4)(0,0,0,0),0,0)))
        except Exception:self.close();raise

    def create(self,index,types,*args):
        pointer=self.r.create(index,types,*args);self.owned.append(pointer);return pointer

    def release(self,pointer):
        self.owned.remove(pointer);self.r.resources.remove(pointer);self.r.probe.release(pointer)

    def prepare(self):
        r=self.r;size=(r.width,r.height)
        if size==self.size:return
        r.context(110,[])
        for target in self.targets:
            for pointer in reversed(target):self.release(pointer)
        self.targets=[];self.size=size
        w,h=max(1,r.width//4),max(1,r.height//4)
        for width,height in((w,h),(w,h),size):
            texture=self.create(5,[P,P],c.byref(TextureDesc(width,height,1,1,10,1,0,0,40,0,0)),None)
            self.targets.append((texture,self.create(9,[P,P],texture,None),self.create(7,[P,P],texture,None)))
        self.small=(w,h)

    def pass_(self,name,target,views,width,height,settings):
        r=self.r;r.context(110,[])
        r.context(33,[U,P,P],1,c.byref(target[1]),None);r.context(44,[U,P],1,c.byref(Viewport(0,0,width,height,0,1)))
        r.context(43,[P],r.rasters[False]);r.context(24,[U],4)
        r.context(11,[P,P,U],self.vs,None,0);r.context(9,[P,P,U],self.ps[name],None,0)
        r.context(8,[U,U,P],0,len(views),(P*len(views))(*views));r.context(10,[U,U,P],0,1,c.byref(self.sampler))
        constants=r.temporary(r.buffer(np.asarray(settings,dtype='<f4'),4));r.context(16,[U,U,P],0,1,c.byref(constants))
        r.context(13,[U,U],3,0);self.draws+=1

    def apply(self,texture):
        self.prepare();r=self.r;w,h=self.small
        view=r.temporary(r.create(7,[P,P],texture,None))
        self.pass_('extract',self.targets[0],[view],w,h,(1/r.width,1/r.height,0,0))
        # One shared display condition, never item/race/dye dependent.
        for radius in(1.,2.):
            self.pass_('blur',self.targets[1],[self.targets[0][2]],w,h,(radius/w,0,0,0))
            self.pass_('blur',self.targets[0],[self.targets[1][2]],w,h,(0,radius/h,0,0))
        self.pass_('compose',self.targets[2],[view,self.targets[0][2]],r.width,r.height,(0,0,PREVIEW_GLOW_STRENGTH,0))
        return self.targets[2][0]

    def close(self):
        if self.closed:return
        self.closed=True;self.r.context(110,[])
        for pointer in self.owned.copy()[::-1]:self.release(pointer)
