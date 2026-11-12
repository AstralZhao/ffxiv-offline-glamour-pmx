"""Resolve a two-times-per-axis scene into the physical preview dimensions."""
import ctypes as c
from d3d11_debug import P,U,F,Viewport,TextureDesc,compile_shader
from mesh_sampling_probe import SamplerDesc

SOURCE=b"""
struct Output {float4 position:SV_POSITION;float2 uv:TEXCOORD;};
Output vs(uint id:SV_VertexID) {
    Output o;o.uv=float2((id<<1)&2,id&2);
    o.position=float4(o.uv*float2(2,-2)+float2(-1,1),0,1);return o;
}
Texture2D<float4> source:register(t0);SamplerState linearClamp:register(s0);
float4 ps(Output i):SV_TARGET {return source.SampleLevel(linearClamp,i.uv,0);}
"""


class SupersamplingResolve:
    def __init__(self,renderer):
        self.r=renderer;self.owned=[];self.targets=[];self.size=None
        self.source=None;self.source_srv=None
        try:
            for entry,target,index in ((b'vs',b'vs_5_0',12),(b'ps',b'ps_5_0',15)):
                raw=compile_shader(entry,target,SOURCE)
                setattr(self,entry.decode(),self.keep(renderer.create(index,[P,c.c_size_t,P],raw,len(raw),None)))
            self.sampler=self.keep(renderer.create(23,[P],c.byref(SamplerDesc(20,3,3,3,0,1,1,(F*4)(0,0,0,0),0,0))))
        except Exception:self.close();raise

    def keep(self,value):
        self.owned.append(value);return value

    def release(self,value):
        self.owned.remove(value);self.r.resources.remove(value);self.r.probe.release(value)

    def prepare(self,size):
        if self.size==size:return
        self.r.context(110,[])
        allocated=[]
        try:
            texture=self.keep(self.r.create(5,[P,P],c.byref(TextureDesc(*size,1,1,10,1,0,0,40,0,0)),None));allocated.append(texture)
            view=self.keep(self.r.create(9,[P,P],texture,None));allocated.append(view)
        except Exception:
            for value in allocated[::-1]:self.release(value)
            raise
        for value in self.targets[::-1]:self.release(value)
        self.targets=allocated;self.size=size

    def apply(self,texture,size):
        self.prepare(size);r=self.r
        if self.source is None or self.source.value!=texture.value:
            view=self.keep(r.create(7,[P,P],texture,None))
            if self.source_srv is not None:self.release(self.source_srv)
            self.source=texture;self.source_srv=view
        r.context(110,[])
        r.context(33,[U,P,P],1,c.byref(self.targets[1]),None)
        r.context(44,[U,P],1,c.byref(Viewport(0,0,*size,0,1)))
        r.context(43,[P],r.rasters[False]);r.context(35,[P,P,U],None,None,0xffffffff)
        r.context(17,[P],None);r.context(24,[U],4)
        r.context(11,[P,P,U],self.vs,None,0);r.context(9,[P,P,U],self.ps,None,0)
        r.context(8,[U,U,P],0,1,c.byref(self.source_srv));r.context(10,[U,U,P],0,1,c.byref(self.sampler))
        r.context(13,[U,U],3,0);r.context(110,[])
        return self.targets[0]

    def close(self):
        self.r.context(110,[])
        for value in self.owned[::-1]:self.r.resources.remove(value);self.r.probe.release(value)
        self.owned=[];self.targets=[];self.source=None;self.source_srv=None;self.size=None
