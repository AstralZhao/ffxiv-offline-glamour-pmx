"""D3D11 GPU-only presentation into an application-owned HWND.

Character materials still render to their original FP16 target. Only final
display conversion is performed here: sample/clamp RGB into a linear BGRA8
swap chain. There is no staging texture, Map, image encoder or HTTP frame.
"""
import ctypes as c
import uuid

from d3d11_debug import P,U,F,Viewport,check,compile_shader


class SwapChainDesc(c.Structure):
    _fields_=[(name,U)for name in('width','height','refresh_n','refresh_d','format','scanline','scaling',
        'samples','quality','usage','buffers')]+[('hwnd',P),('windowed',c.c_int),('effect',U),('flags',U)]


DISPLAY=b'''
struct Output { float4 position:SV_POSITION; float2 uv:TEXCOORD; };
Output vs(uint id:SV_VertexID) {
    Output o; o.uv=float2((id<<1)&2,id&2);
    o.position=float4(o.uv*float2(2,-2)+float2(-1,1),0,1);return o;
}
Texture2D<float4> source:register(t0);SamplerState linearClamp:register(s0);
float4 ps(Output i):SV_TARGET { return float4(saturate(source.SampleLevel(linearClamp,i.uv,0).rgb),1); }
'''


class NativeSurface:
    def __init__(self,renderer,hwnd,*,present_enabled=True):
        self.renderer=renderer;self.hwnd=int(hwnd);self.chain=P();self.rtv=None;self.back=None
        self.source=None;self.source_srv=None;self.owned=[];self.present_count=0;self.closed=False
        self.width=self.height=0
        self.present_enabled=present_enabled;self.needs_present=True;self.last_present_status=None
        device,adapter,factory=P(),P(),P()
        def iid(value):return c.create_string_buffer(uuid.UUID(value).bytes_le)
        method=renderer.probe.method
        try:
            check(method(renderer.probe.device,0,c.c_long,P,P)(renderer.probe.device,
                iid('54ec77fa-1377-44e6-8c32-88fd5f44c84c'),c.byref(device)),'Native DXGI device')
            check(method(device,7,c.c_long,P)(device,c.byref(adapter)),'Native DXGI adapter')
            check(method(adapter,6,c.c_long,P,P)(adapter,iid('7b7166ec-21c7-44ae-b21a-c9ae321ae369'),c.byref(factory)),'Native DXGI factory')
            desc=SwapChainDesc(renderer.width,renderer.height,0,1,87,0,0,1,0,32,2,P(self.hwnd),1,0,0)
            check(method(factory,10,c.c_long,P,P,P)(factory,renderer.probe.device,c.byref(desc),c.byref(self.chain)),'Create native swap chain')
            check(method(factory,8,c.c_long,P,U)(factory,P(self.hwnd),2),'Disable native Alt-Enter')
            for entry,stage,index in((b'vs',b'vs_5_0',12),(b'ps',b'ps_5_0',15)):
                code=compile_shader(entry,stage,DISPLAY)
                setattr(self,entry.decode(),self.create(index,[P,c.c_size_t,P],code,len(code),None))
            from mesh_sampling_probe import SamplerDesc
            self.sampler=self.create(23,[P],c.byref(SamplerDesc(21,3,3,3,0,1,1,(F*4)(0,0,0,0),0,0)))
            self.acquire_backbuffer(renderer.width,renderer.height)
        except Exception:self.close();raise
        finally:
            for pointer in(factory,adapter,device):renderer.probe.release(pointer)

    def create(self,index,types,*arguments):
        value=self.renderer.create(index,types,*arguments);self.owned.append(value);return value

    def release(self,pointer):
        if pointer is None:return
        self.renderer.resources.remove(pointer);self.owned.remove(pointer);self.renderer.probe.release(pointer)

    def acquire_backbuffer(self,width,height):
        iid=c.create_string_buffer(uuid.UUID('6f15aaf2-d208-4e89-9ab4-489535d34f9c').bytes_le);self.back=P()
        check(self.renderer.probe.method(self.chain,9,c.c_long,U,P,P)(self.chain,0,iid,c.byref(self.back)),'Native backbuffer')
        self.rtv=self.create(9,[P,P],self.back,None);self.width=width;self.height=height

    def resize(self,width,height):
        if(width,height)==(self.width,self.height):return
        self.renderer.context(110,[])
        self.release(self.rtv);self.rtv=None;self.renderer.probe.release(self.back);self.back=None
        try:
            check(self.renderer.probe.method(self.chain,13,c.c_long,U,U,U,U,U)(self.chain,2,width,height,87,0),'Resize native swap chain')
        except Exception:
            self.acquire_backbuffer(self.width,self.height)
            raise
        self.acquire_backbuffer(width,height)
        self.needs_present=True

    def present(self,texture):
        r=self.renderer
        if self.source is None or self.source.value!=texture.value:
            self.release(self.source_srv)
            self.source_srv=self.create(7,[P,P],texture,None);self.source=texture
        self.needs_present=True
        return self.present_last()

    def present_last(self):
        if self.closed or not self.present_enabled or self.source_srv is None:return False
        r=self.renderer
        r.context(110,[])
        r.context(33,[U,P,P],1,c.byref(self.rtv),None)
        r.context(44,[U,P],1,c.byref(Viewport(0,0,self.width,self.height,0,1)))
        r.context(43,[P],r.rasters[False]);r.context(24,[U],4)
        r.context(11,[P,P,U],self.vs,None,0);r.context(9,[P,P,U],self.ps,None,0)
        r.context(8,[U,U,P],0,1,c.byref(self.source_srv));r.context(10,[U,U,P],0,1,c.byref(self.sampler))
        r.context(13,[U,U],3,0)
        status=r.probe.method(self.chain,8,c.c_long,U,U)(self.chain,1,0)
        check(status,'Present native character')
        # DXGI_STATUS_OCCLUDED is a positive status, not a displayed frame.
        # Retain the GPU source so an expose/restore can present it again.
        self.last_present_status=status&0xffffffff;self.needs_present=status!=0
        self.present_count+=1
        return status==0

    def recover(self):
        if self.closed or not self.present_enabled or self.source_srv is None:return False
        if self.last_present_status==0x087a0001:
            # Test visibility cheaply while fully covered. Do not repeatedly
            # draw the full viewport until DXGI can display it again.
            status=self.renderer.probe.method(self.chain,8,c.c_long,U,U)(self.chain,0,1)
            check(status,'Test native preview visibility')
            if status&0xffffffff==0x087a0001:return False
        return self.present_last()

    def close(self):
        if self.closed:return
        self.closed=True
        if getattr(self.renderer,'probe',None):self.renderer.context(110,[])
        for pointer in self.owned.copy()[::-1]:self.release(pointer)
        self.renderer.probe.release(self.back);self.back=None
        self.renderer.probe.release(self.chain);self.chain=P()
