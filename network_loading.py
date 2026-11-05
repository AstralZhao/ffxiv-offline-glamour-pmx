"""Current-scene resource plans and bounded parallel original-byte reads.

Only workers read/decode SqPack. All D3D calls remain on the renderer thread.
No speculative outfits, next pages, unreferenced material textures or maps.
"""
from collections import deque
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
import struct

from transfer_progress import current,bind
from ff14_readonly import SqPack,read_exact

INPUT_BUDGET=32*1024**2


def selected_inputs(renderer,scene):
    from preview_lighting import SOURCE_PROFILE,NATIVE_PROFILE,PROFILE_PATH
    import json
    selected={}
    def add(path,kind='source',effect=False):
        key=('reviewed_reflection',SOURCE_PROFILE)if kind=='reflection'else('source',path)
        # Inspection must not start OwnedCache ownership groups early.
        if not dict.__contains__(renderer.cache,key):selected.setdefault(key,(path,kind,effect))
    for mesh in renderer.meshes_for_draw(scene):
        if getattr(scene,'scope','body')=='all'or renderer.native_fringe_states:
            roles=[]
            if not mesh['overlay']and not mesh.get('forward_transparent'):
                if renderer.native_fringe_states or'gbuffer_color'in mesh['plans']:roles.append('material')
                roles.append('gbuffer_color'if renderer.native_fringe_states or'gbuffer_color'in mesh['plans']else'direct')
            if mesh['overlay']:roles.append(mesh['overlay'])
            if mesh['transparent']and not mesh['overlay']:roles.append('direct')
        else:roles=['direct']
        for role in roles:
            chosen={**mesh,'plan':renderer.render_plan(scene,mesh,role)}
            for resource in chosen['plan']['pixel']['resources']:
                name=resource['name'];path=renderer.source_texture_path(scene,chosen,name)
                if path is not None:add(path)
                elif name=='g_SamplerReflectionArray'and renderer.lighting in(SOURCE_PROFILE,NATIVE_PROFILE):
                    add(json.loads(PROFILE_PATH.read_bytes())['texture']['path'],'reflection')
    for sample in getattr(scene,'effect_samples',()):
        for data in sample['textures'].values():
            if not data.get('screen_copy'):add(data['path'],effect=True)
    return list(selected.values())


def input_size(pack,path):
    dat,offset=pack.locate(path)
    if pack.memory_only:
        name=pack.network.relative(dat);raw=pack.network.read_range(name,offset,24)
    else:
        with pack.open_data(dat)as stream:stream.seek(offset);raw=read_exact(stream,24)
    header,kind,size,_,_,count=struct.unpack('<6I',raw)
    if kind not in(2,3,4)or not 24<=header<=16*1024**2 or not 0<size<=256*1024**2:
        raise ValueError('网络资源头部或大小不正确')
    return size


def parallel_inputs(pack,paths,*,workers=None,budget=INPUT_BUDGET):
    """Yield original decoded inputs with a bounded look-ahead window.

    A single input larger than the window runs alone under the existing memory
    admission guard. Finally drains running reads before a scene is retired.
    """
    from network_resources import CONNECTIONS
    workers=CONNECTIONS if workers is None else workers;meter=current()
    from network_cancellation import check
    def fork():return pack.fork()if hasattr(pack,'fork')else SqPack(pack.game_root)
    def size(path):return input_size(fork(),path)
    def read(path):
        child=fork();raw=child.read(path)
        return raw,dict(child.read_log[path])
    with ThreadPoolExecutor(max_workers=workers,thread_name_prefix='WardrobeDownload',initializer=bind,initargs=(meter,))as pool:
        sizes=list(pool.map(size,paths))
        if meter:meter.plan(len(paths))
        pending=deque();index=0;reserved=0
        try:
            while index<len(paths)or pending:
                check()
                while index<len(paths)and len(pending)<workers:
                    n=sizes[index]
                    if pending and reserved+n>budget:break
                    future=pool.submit(read,paths[index]);pending.append((index,n,future));reserved+=n;index+=1
                at,n,future=pending.popleft();raw,journal=future.result();reserved-=n
                pack.read_log[paths[at]]=journal
                yield at,raw
        finally:
            for _,_,future in pending:future.cancel()


def prepare_scene_inputs(renderer,scene):
    inputs=selected_inputs(renderer,scene);paths=[r[0]for r in inputs];meter=current()
    with closing(parallel_inputs(scene.pack,paths))as stream:
        for index,raw in stream:
            from network_cancellation import check
            check()
            path,kind,effect=inputs[index];previous=getattr(renderer,'_effect_scope',None)
            if effect:renderer._effect_scope=getattr(renderer,'effect_renderer',None)
            try:
                if kind=='reflection':
                    from preview_lighting import source_reflection,SOURCE_PROFILE
                    from texture_probe import FORMATS
                    cube,renderer.reflection_identity=source_reflection(scene.pack,raw=raw)
                    key=('reviewed_reflection',SOURCE_PROFILE)
                    if key not in renderer.cache:
                        renderer.cache[key]=renderer.texture_data(cube['width'],cube['height'],cube['layers'],cube['mips'],
                            FORMATS[cube['format']][1],cube['payloads'],cube['pitches'],True)
                else:renderer.source_texture(scene.pack,path,raw=raw)
            finally:renderer._effect_scope=previous
            if meter:meter.item_ready()
    if meter:meter.preparing()
