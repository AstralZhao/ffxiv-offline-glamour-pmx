"""One owned GPU-only static-character layer for changing equipment VFX time."""
import ctypes as c

from d3d11_debug import P,U,TextureDesc


class CharacterLayer:
    def __init__(self,renderer):
        self.r=renderer;self.targets={};self.key=None;self.scene=None;self.hits=0;self.captures=0

    def invalidate(self):self.key=None;self.scene=None

    def matches(self,scene,key):return self.scene is scene and self.key==key and bool(self.targets)

    def prepare(self):
        r=self.r;descriptions={}
        for name,source,fmt in(('color',r.color,10),('depth',r.depth,40)):
            desc=(U*11)();r.probe.method(source,10,None,P)(source,c.byref(desc));words=list(desc)
            if words[:4]!=[r.width,r.height,1,1]or words[4]!=fmt or words[5:7]!=[1,0]:
                raise ValueError('Static character snapshot requires reviewed color/depth layouts')
            descriptions[name]=words
        if self.targets and all(self.targets[n][1]==tuple(d)for n,d in descriptions.items()):return
        self.close();created={}
        try:
            for name,words in descriptions.items():
                copy=words.copy();copy[7:]=[0,0,0,0]
                # Same R32 family as the owned D32 depth target, no view bound.
                if name=='depth':copy[4]=39
                target=r.create(5,[P,P],c.byref(TextureDesc(*copy)),None)
                created[name]=(target,tuple(words))
        except Exception:
            for target,_ in created.values():r.resources.remove(target);r.probe.release(target)
            raise
        self.targets=created

    def capture(self,scene,key):
        self.invalidate();self.prepare();r=self.r
        for name,source in(('color',r.color),('depth',r.depth)):
            r.context(47,[P,P],self.targets[name][0],source)
        # Gbuffers remain untouched by the VFX/post consumers. Every base
        # redraw invalidates this key BEFORE it can modify those layers.
        self.lighting_identity=dict(getattr(r,'last_lighting_identity',{}))
        self.scene=scene;self.key=key;self.captures+=1

    def restore(self):
        if self.key is None:raise ValueError('Static character layer is invalid')
        r=self.r;r.context(110,[])
        for name,destination in(('color',r.color),('depth',r.depth)):
            r.context(47,[P,P],destination,self.targets[name][0])
        self.hits+=1;r.last_lighting_identity=dict(self.lighting_identity)

    def close(self):
        self.invalidate()
        for target,_ in reversed(list(self.targets.values())):
            self.r.resources.remove(target);self.r.probe.release(target)
        self.targets={}
