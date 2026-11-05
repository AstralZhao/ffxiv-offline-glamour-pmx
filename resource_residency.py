"""Transactional asset-cache ownership for the memory-only network mode.

Run on the renderer thread. ClearState unbinds before releasing application
references; D3D11 itself retains resources still needed by queued GPU commands.
"""
class OwnedCache(dict):
    def __init__(self,renderer,values=()):
        super().__init__(values);self.renderer=renderer;self.groups={};self.pending={};self.used=set();self.original=set();self.collecting=False
    def begin(self):self.collecting=True;self.used.clear();self.pending.clear();self.original=set(self)
    def __contains__(self,key):
        exists=super().__contains__(key)
        if self.collecting:
            if exists:self.used.add(key)
            else:self.pending[key]={id(p)for p in self.renderer.resources}
        return exists
    def __getitem__(self,key):
        if self.collecting:self.used.add(key)
        return super().__getitem__(key)
    def __setitem__(self,key,value):
        before=self.pending.pop(key,None)
        if before is not None:self.groups[key]={id(p):p for p in self.renderer.resources if id(p)not in before}
        # Track each owned COM reference, not its native address. The driver
        # may return the same interface address with another AddRef, and
        # addresses of released objects may be reused for unrelated objects.
        if any(p is value for p in self.renderer.resources):self.groups.setdefault(key,{})[id(value)]=value
        if self.collecting:self.used.add(key)
        super().__setitem__(key,value)
    def finish(self,success):
        discard=set(self)-self.used if success else set(self)-self.original
        removed=set()
        for key in discard:
            removed.update(self.groups.pop(key,{}));dict.__delitem__(self,key)
        self.collecting=False;self.pending.clear();return removed
    def owned(self):return set().union(*self.groups.values())if self.groups else set()


class Residency:
    def __init__(self,renderer):
        self.r=renderer;renderer.cache=OwnedCache(renderer,renderer.cache);renderer.programs=OwnedCache(renderer,renderer.programs)
        self.caches=(renderer.cache,renderer.programs);self.active_scene=None;self.collecting=False;self.geometry_used=set()
        self.old_effect=None;self.old_geometry=set();self.released=0;self.transitions=0

    def begin(self,scene):
        if self.active_scene is scene:return False
        if self.collecting:raise RuntimeError('Overlapping memory residency transition')
        self.collecting=True;self.geometry_used.clear();self.old_geometry=set(self.r.geometry_buffers)
        for cache in self.caches:cache.begin()
        self.old_effect=getattr(self.r,'effect_renderer',None)
        if self.old_effect is not None:del self.r.effect_renderer
        return True

    def geometry(self,key):
        if self.collecting:self.geometry_used.add(key)

    def finish(self,scene,success):
        if not self.collecting:return
        r=self.r;discard=set()
        for cache in self.caches:discard.update(cache.finish(success))
        current_effect=getattr(r,'effect_renderer',None)
        retired=self.old_effect if success else current_effect
        if not success:
            if self.old_effect is not None:r.effect_renderer=self.old_effect
            elif hasattr(r,'effect_renderer'):del r.effect_renderer
        if retired is not None:discard.update(getattr(retired,'_owned_gpu',{}))
        wanted=self.geometry_used if success else self.old_geometry
        for key in list(r.geometry_buffers):
            if key in wanted:continue
            pointer,size=r.geometry_buffers.pop(key);discard.add(id(pointer));r.geometry_bytes-=size;r.geometry_pointers.discard(pointer.value)
        for key,(_,geometry)in list(r.geometry_identities.items()):
            if geometry not in r.geometry_buffers:r.geometry_identities.pop(key)
        effect=getattr(r,'effect_renderer',None)
        protected=set().union(*(cache.owned()for cache in self.caches))|{id(p)for p,_ in r.geometry_buffers.values()}|set(getattr(effect,'_owned_gpu',{}))
        r.context(110,[]);released_before=self.released
        for pointer in list(reversed(r.resources)):
            if id(pointer)in discard and id(pointer)not in protected:
                r.resources.remove(pointer);r.probe.release(pointer);self.released+=1
        # One submission per retired outfit, never on orbit/animation frames.
        # D3D11 defers destruction; covered/non-presenting windows otherwise
        # can retain a queue of retired allocations despite balanced COM refs.
        if self.released!=released_before:r.context(111,[])
        r.source_hashes.clear();r.source_hash_bytes=0
        if success:self.active_scene=scene;self.transitions+=1
        self.collecting=False;self.old_effect=None

    def metrics(self):
        return {'transitions':self.transitions,'released_GPU_references':self.released,
            'GPU_asset_cache_entries':sum(len(cache)for cache in self.caches),'geometry_bytes':self.r.geometry_bytes,
            'live_GPU_references':len(self.r.resources)}

    def detach(self):
        """After native close, break back-references without waiting for GC."""
        r=self.r;self.active_scene=None;self.old_effect=None
        for cache in self.caches:cache.clear();cache.groups.clear();cache.pending.clear();cache.renderer=None
        for name in('effect_renderer','material_glow','character_layer','native_surface'):
            if hasattr(r,name):setattr(r,name,None)
        r.pending_effect_draw=None;r.post_camera=None;r.memory_residency=None
        r.geometry_buffers.clear();r.geometry_identities.clear();self.r=None


def prune_effect_resources(renderer):
    """Discard bookkeeping for frame-temporary or replaced COM references."""
    effect=getattr(renderer,'effect_renderer',None)
    if effect is not None and hasattr(effect,'_owned_gpu'):
        live={id(pointer)for pointer in renderer.resources}
        effect._owned_gpu={token:pointer for token,pointer in effect._owned_gpu.items()if token in live}


def release_inactive_cpu(engine,scene):
    """The current scene owns selected immutable meshes; discard look-back caches."""
    for key,value in list(engine.scene_cache.items()):
        if value is not scene:engine.scene_cache.pop(key)
    outfit=engine.character.outfit
    wanted={mesh['shader_path']for mesh in engine.character.base.meshes+scene.meshes}
    for packages in(engine.character.base.packages,scene.packages):
        for name in list(packages):
            if name not in wanted:packages.pop(name)
    for name in('models','prepared','attachments','material_program_cache','material_animation_sources','items'):
        value=getattr(outfit,name,None)
        if value is not None:value.clear()
    if getattr(outfit,'head_parts',None) is not None:outfit.head_parts.cache.clear()
    engine.accessories.prepared.clear()
    if hasattr(engine.pack,'release_idle'):engine.pack.release_idle()
