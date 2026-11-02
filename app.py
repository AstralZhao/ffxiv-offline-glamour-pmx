"""Local-only interactive wardrobe Demo using the existing CN GPU renderer."""
from __future__ import annotations

import argparse
import base64
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import hashlib
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from io import BytesIO
import json
import math
from pathlib import Path
import secrets
import sqlite3
import struct
import subprocess
import sys
import threading
import time
from urllib.parse import parse_qs,urlparse
import webbrowser

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
SLOTS={'Head':'头部','Body':'上装','Gloves':'手部','Legs':'下装','Feet':'脚部',
    'Ears':'耳饰','Neck':'项链','Wrists':'手镯','FingerR':'右戒指','FingerL':'左戒指'}
GROUPS={'防具':['Head','Body','Gloves','Legs','Feet'],'饰品':['Ears','Neck','Wrists','FingerR','FingerL']}
GAME=Path('G:/Games/FF14/Final Fantasy XIV/最终幻想XIV')
DEFAULT_PRESET=GAME/'game/My Games/FINAL FANTASY XIV - A Realm Reborn/test.dat'
EXAMPLE={'Head':52424,'Body':52620,'Gloves':52430,'Legs':52629,'Feet':42675}


def open_app(url):
    edge=Path('C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe')
    if edge.is_file():
        subprocess.Popen([str(edge),'--app='+url,'--new-window','--window-size=1440,1000',
            '--no-first-run','--no-default-browser-check','--user-data-dir='+str(ROOT/'artifacts/demo/app-browser')])
    else:webbrowser.open(url)


def database():
    conn=sqlite3.connect((ROOT/'artifacts/cn-data/catalog.sqlite').as_uri()+'?mode=ro',uri=True)
    conn.row_factory=sqlite3.Row
    return conn


def empty_outfit():return {slot:{'item_id':None,'first':0,'second':0}for slot in SLOTS}


def parse_state(raw):
    if not isinstance(raw,dict):raise ValueError('搭配数据格式不正确')
    equipment=raw.get('equipment',{})
    if not isinstance(equipment,dict)or set(equipment)-set(SLOTS):raise ValueError('存在未接入的装备部位')
    result=empty_outfit()
    for slot,choice in equipment.items():
        if not isinstance(choice,dict)or set(choice)-{'item_id','first','second'}:raise ValueError('装备数据格式不正确')
        item=choice.get('item_id');first=choice.get('first',0);second=choice.get('second',0)
        if item is not None and(type(item)is not int or not 1<=item<=1000000):raise ValueError('装备编号不正确')
        if any(type(v)is not int or not 0<=v<=254 for v in(first,second)):raise ValueError('染剂编号不正确')
        if item is None and(first or second):raise ValueError('空部位不能染色')
        result[slot]={'item_id':item,'first':first,'second':second}
    yaw=raw.get('yaw',0);zoom=raw.get('zoom',1);visible=raw.get('head_visible',True)
    pitch=raw.get('pitch',0);pan=raw.get('pan',0)
    effects_visible=raw.get('effects_visible',True)
    if type(yaw)not in(int,float)or not math.isfinite(yaw)or not -3600<=yaw<=3600:raise ValueError('观察角度不正确')
    if type(zoom)not in(int,float)or not math.isfinite(zoom)or not .25<=zoom<=4:raise ValueError('缩放范围不正确')
    if type(visible)is not bool:raise ValueError('头饰显示状态不正确')
    if type(effects_visible)is not bool:raise ValueError('特效显示状态不正确')
    if type(pitch)not in(int,float)or not math.isfinite(pitch)or not -65<=pitch<=65:raise ValueError('俯仰角度不正确')
    if type(pan)not in(int,float)or not math.isfinite(pan)or not -1<=pan<=1:raise ValueError('相机平移范围不正确')
    return {'equipment':result,'head_visible':visible,'effects_visible':effects_visible,'yaw':float(yaw),'zoom':float(zoom),'pitch':float(pitch),'pan':float(pan)}


def equipment_rows(slot,query='',offset=0,limit=60,*,grouped=False,model_set=None,library=None,favorites_only=False,tags=(),favorite_snapshot=None):
    if slot not in SLOTS:raise ValueError('部位尚未接入原型')
    if not 0<=offset<=50000 or not 1<=limit<=100:raise ValueError('分页范围不正确')
    if model_set is not None and(type(model_set)is not int or not 0<model_set<=65535):raise ValueError('装备模型目录不正确')
    from catalogue import equipment_rows as rows
    with closing(database())as conn:
        if library is not None:return library.equipment_rows(conn,slot,query,offset,limit,grouped=grouped,model_set=model_set,
            preferred_item=EXAMPLE.get(slot,0),favorites_only=favorites_only,tags=tags,favorite_snapshot=favorite_snapshot)
        return rows(conn,slot,query,offset,limit,grouped=grouped,model_set=model_set,preferred_item=EXAMPLE.get(slot,0))


class DemoEngine:
    """All native GPU and SQLite outfit work stays on one dedicated thread."""
    def __init__(self,game,preset,*,preset_directory=None,native_hwnd=None):
        self.game=game.resolve();self.default_preset=preset.resolve();self.character=None;self.renderer=None
        self.preset_directory=Path(preset_directory)if preset_directory is not None else ROOT/'presets'
        self.rendered_parameters=None;self.appearance_is_preview=False;self.characters=None
        self.native_hwnd=native_hwnd;self.effect_frame=30.
        self.item_icons=None  # Resource indexes are opened on the worker below.
        self.pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix='WardrobeGPU')
        self.lock=threading.RLock();self.status='loading';self.message='正在读取国服资源与外观预设…'
        self.images=OrderedDict();self.presets={};self.last=None;self.error=None;self.scene_cache=OrderedDict()
        self.data=ROOT/'artifacts/demo';self.data.mkdir(parents=True,exist_ok=True)
        self.token=secrets.token_urlsafe(24);self.ready=self.pool.submit(self.initialize)

    def image_record(self,image):
        if image is None:return None  # Native presentation has no CPU image.
        output=BytesIO();image.save(output,format='PNG');raw=output.getvalue();key=hashlib.sha256(raw).hexdigest()
        with self.lock:
            self.images[key]=raw;self.images.move_to_end(key)
            while len(self.images)>20:self.images.popitem(last=False)
        return '/api/image/'+key+'.png'

    def register_preset(self,path,identifier=None):
        from preset_character import import_preset
        path=self.library.path(Path(path).name)
        record=import_preset(path);p=record['parameters'];key=hashlib.sha256(str(path.resolve()).encode()).hexdigest()[:24]
        if identifier is not None:key=identifier
        if (p['race'],p['gender'],p['body_type'])!=(6,1,1)or p['tribe']not in(11,12):
            raise ValueError('原型当前支持敖龙族女性的两个部族')
        entry={'id':key,'name':record.get('comment')or path.stem,'file_name':path.name,
            'clan':'晨曦之民'if p['tribe']==11 else'暮晖之民','face':p['face'],'hair':p['hair'],
            'parameters':p,'sha256':record['source_sha256'],'app_owned':True}
        with self.lock:self.presets[key]=(path,entry)
        return entry

    def refresh_presets(self):
        entries={key:(path,entry)for key,path,entry in self.library.scan()}
        with self.lock:self.presets=entries

    def initialize(self):
        try:
            from icons import ItemIcons
            self.item_icons=ItemIcons(self.game)
            from ff14_readonly import SqPack
            from prepared_preset_store import PreparedPresetStore
            from static_hair_scene import StaticHairScenes
            from render_preset_materials import PresetMaterialRenderer
            from static_material_inputs import camera_payload
            native_hwnd=self.native_hwnd
            class PreviewRenderer(PresetMaterialRenderer):
                zoom=1.;pitch=0.;pan=0.
                def __init__(self,*args,**kwargs):
                    self.context_functions={};self.geometry_buffers=OrderedDict();self.geometry_pointers=set();self.geometry_bytes=0
                    self.native_surface=None;self.material_glow=None;self.material_glow_enabled=True
                    super().__init__(*args,**kwargs)
                    if native_hwnd:
                        from native_surface import NativeSurface
                        self.native_surface=NativeSurface(self,native_hwnd)

                def texture(self,format_,bind,usage=0,cpu=0):
                    if format_==10 and bind==32:bind=40
                    return super().texture(format_,bind,usage,cpu)

                def finish_scene_frame(self):
                    output=self.color
                    if self.material_glow_enabled:
                        if self.material_glow is None:
                            from material_glow import MaterialGlow
                            self.material_glow=MaterialGlow(self)
                        output=self.material_glow.apply(self.color)
                    if self.native_surface:
                        self.native_surface.present(output)
                        return None,None
                    original=self.color
                    try:self.color=output;return super().finish_scene_frame()
                    finally:self.color=original

                def resize_native(self,width,height):
                    from d3d11_debug import P
                    if not self.native_surface:return
                    width=max(64,int(width));height=max(64,int(height));scale=min(1.,2048/max(width,height))
                    width=max(64,round(width*scale));height=max(64,round(height*scale))
                    if(width,height)==(self.width,self.height):return
                    self.context(110,[])
                    # Drop the SRV first so the previous FP16 target can die.
                    self.native_surface.release(self.native_surface.source_srv)
                    self.native_surface.source_srv=None;self.native_surface.source=None
                    old=[self.color,self.rtv,self.depth,self.dsv]+[p for target in self.gbuffers for p in target]
                    for pointer in old:self.resources.remove(pointer);self.probe.release(pointer)
                    self.gbuffers=[];self.width=width;self.height=height
                    self.color=self.texture(10,40);self.rtv=self.create(9,[P,P],self.color,None)
                    self.depth=self.texture(40,64);self.dsv=self.create(10,[P,P],self.depth,None)
                    self.native_surface.resize(width,height)

                def close(self):
                    if self.native_surface:self.native_surface.close()
                    if self.material_glow:self.material_glow.close()
                    return super().close()

                def context(self,index,types,*arguments,result=None):
                    key=(index,tuple(types),result)
                    if key not in self.context_functions:
                        self.context_functions[key]=self.probe.method(self.probe.context,index,result,*types)
                    return self.context_functions[key](self.probe.context,*arguments)

                def buffer(self,array,bind):
                    if bind not in(1,2):return super().buffer(array,bind)
                    key=(bind,array.dtype.str,array.shape,hashlib.sha256(array.tobytes()).digest())
                    if key in self.geometry_buffers:
                        self.geometry_buffers.move_to_end(key);return self.geometry_buffers[key][0]
                    pointer=super().buffer(array,bind)
                    self.geometry_buffers[key]=(pointer,array.nbytes);self.geometry_pointers.add(pointer.value);self.geometry_bytes+=array.nbytes
                    while self.geometry_bytes>64*1024*1024 and len(self.geometry_buffers)>1:
                        _,(old,size)=self.geometry_buffers.popitem(last=False)
                        self.geometry_pointers.remove(old.value);self.resources.remove(old);self.probe.release(old);self.geometry_bytes-=size
                    return pointer

                def temporary(self,resource):
                    if resource.value in self.geometry_pointers:return resource
                    return super().temporary(resource)

                def dynamic_buffer(self,array,bind):
                    # Animated shape vertices must not fill the immutable
                    # character geometry cache with a new entry every frame.
                    return super().buffer(array,bind)

                def camera_inputs(self,scene,yaw):
                    if (self.zoom,self.pitch,self.pan)==(1,0,0):return super().camera_inputs(scene,yaw)
                    # A real orbit camera over the same source scene. Only
                    # presentation changes; native materials/rig stay intact.
                    import numpy as np
                    low,high=scene.bounds.min(axis=0),scene.bounds.max(axis=0)
                    center=(low+high)/2;focus=center.copy()
                    focus[1]+=(high[1]-low[1])*(.28*(1-1/self.zoom)+self.pan)
                    y,p=np.deg2rad((yaw,self.pitch));c,s=np.cos(y),np.sin(y);cp,sp=np.cos(p),np.sin(p)
                    ry=np.array(((c,0,s),(0,1,0),(-s,0,c)));rx=np.array(((1,0,0),(0,cp,-sp),(0,sp,cp)))
                    view=np.eye(4);view[:3,:3]=rx@ry;view[:3,3]=-view[:3,:3]@focus+(0,0,-3)
                    radius=max((high[1]-low[1])/2,np.max(high-low)/2*self.height/self.width)*1.12/self.zoom
                    projection=np.eye(4);projection[0,0]=self.height/self.width/radius;projection[1,1]=1/radius;projection[2,2]=-1/20
                    return camera_payload(view,projection),view

                def draw_scene_extras(self,scene,camera,view):
                    self.last_effect_draws=[]
                    if not getattr(scene,'effect_samples',()):return
                    from effect_renderer import EffectRenderer
                    if not hasattr(self,'effect_renderer'):self.effect_renderer=EffectRenderer(self,scene.pack)
                    self.last_effect_draws=self.effect_renderer.draw(scene,scene.effect_samples,camera,view)

                def render_plan(self,scene,mesh,role):
                    if role!='material'or not mesh['shader_path'].endswith('/characterlegacy.shpk'):
                        return super().render_plan(scene,mesh,role)
                    from shader_programs import equipment_pass
                    clip=self.native_material_alpha_clip()if self.native_fringe_states else False
                    keys=mesh['material']['shader_keys'];count=mesh['vertices']['weights'].shape[1]
                    key=('demo_gear_plan',mesh['shader_path'],tuple(sorted(keys.items())),count,role,clip)
                    if key not in self.cache:self.cache[key]=equipment_pass(scene.packages[mesh['shader_path']],keys,count,role,alpha_clip=clip)
                    return self.cache[key]

                def draw_mesh(self,scene,mesh,shared,view):
                    if self.material_glow_enabled and mesh['plan'].get('role')=='gbuffer_color':
                        import numpy as np
                        instance=np.frombuffer(shared['g_InstanceParameter'],dtype='<f4').copy().reshape(11,4)
                        # Native PS already adds authored emission to RGB.
                        # EnvParameter.w selects its emission/color ratio for
                        # the offscreen alpha channel consumed by preview bloom.
                        instance[1,3]=1
                        shared={**shared,'g_InstanceParameter':instance.tobytes()}
                    if any(value['name']=='g_LightDirection'for stage in('vertex','pixel')for value in mesh['plan'][stage]['constants']):
                        from legacy_lighting import source_direction,camera_direction
                        key=('demo_null_scene_legacy_direction',str(scene.pack.game_root))
                        if key not in self.cache:self.cache[key]=source_direction(scene.pack)
                        shared={**shared,'g_LightDirection':camera_direction(self.cache[key],view)}
                    return super().draw_mesh(scene,mesh,shared,view)

                def draw_forward_transparent(self,scene,mesh,shared,view):
                    if not self.material_glow_enabled or not mesh['shader_path'].endswith('/characterglass.shpk'):
                        return super().draw_forward_transparent(scene,mesh,shared,view)
                    from d3d11_debug import P,U
                    if not hasattr(self,'glass_glow_blends'):
                        # Preserve the opaque emission mask beneath glass.
                        self.glass_glow_blends=[self.create(20,[P],(U*66)(0,0,*([*values,7]+[0,1,0,1,1,0,1,15]*7)))
                            for values in([1,1,3,1,1,2,1],[1,5,6,1,2,6,1])]
                    for mode,blend in enumerate(self.glass_glow_blends):
                        self.context(35,[P,P,U],blend,None,0xffffffff)
                        selected={**mesh,'extra_constants':{**mesh.get('extra_constants',{}),
                            'g_GlassOffscreenParam':struct.pack('<4f',mode,0,0,0)}}
                        self.draw_mesh(scene,selected,shared,view)
            self.pack=SqPack(self.game);self.store=PreparedPresetStore(self.pack);self.hair=StaticHairScenes()
            from character_cache import CharacterCache
            self.characters=CharacterCache(self.pack,self.store,self.hair)
            from gpu_selection import preferred_adapter
            self.renderer=PreviewRenderer(640,840,preferred_adapter())
            from presets import PresetLibrary
            from appearance_options import AppearanceOptions
            self.library=PresetLibrary(self.preset_directory,self.game)
            self.library.seed(self.default_preset,default=True)
            if not self.library.metadata.get('legacy_migration_complete'):
                paths=sorted(self.default_preset.parent.glob('FFXIV_CHARA_*.dat'))+sorted((self.data/'presets').glob('*.dat'))
                for path in paths:
                    try:self.library.seed(path)
                    except ValueError:continue
                self.library.metadata['legacy_migration_complete']=True;self.library.save_index()
            self.refresh_presets();self.appearance_options=AppearanceOptions(self.pack,self.store,ROOT)
            if not self.presets:raise ValueError('没有找到可用的敖龙族女性预设，请导入原生DAT预设')
            self.active_preset=next(iter(self.presets));self.load(self.active_preset)
            with self.lock:self.status='ready';self.message='可以开始搭配了'
        except Exception as error:
            with self.lock:self.status='error';self.error=str(error);self.message='初始化未完成'
            self.write_error(error)

    def write_error(self,error):
        import traceback
        with(self.data/'demo.log').open('a',encoding='utf8')as log:
            log.write(time.strftime('%Y-%m-%d %H:%M:%S')+' '+repr(error)+'\n'+traceback.format_exc()+'\n')

    def load(self,key):
        if key not in self.presets:raise ValueError('预设不存在')
        return self.load_preset_path(key,self.presets[key][0])

    def load_preset_path(self,key,path):
        if key not in self.presets:raise ValueError('预设不存在')
        started=time.perf_counter();entry,cache=self.characters.acquire(path);candidate=entry['character']
        previous=self.character;old_accessories=getattr(self,'accessories',None);old_scenes=self.scene_cache
        self.character=candidate;self.accessories=entry['accessories'];self.scene_cache=entry['scenes']
        retained=self.last['state']if self.last else{}
        try:result=self.render(retained)
        except Exception:
            self.character=previous;self.accessories=old_accessories;self.scene_cache=old_scenes;self.characters.trim(previous);raise
        self.active_preset=key
        self.rendered_parameters=dict(candidate.base.pose['preset']['parameters']);self.appearance_is_preview=False
        self.characters.trim(candidate)
        self.last_character_load={**cache,'seconds':time.perf_counter()-started}
        return {**result,'preset':self.presets[key][1],'appearance':self.appearance_state(),'character_cache':self.last_character_load}

    def render(self,raw,*,effect_frame=None):
        from preset_outfit import CHOICES
        state=parse_state(raw);started=time.perf_counter()
        frame=self.effect_frame if effect_frame is None else effect_frame
        if type(frame)not in(int,float)or not math.isfinite(frame)or not 0<=frame<=1e9:raise ValueError('特效预览帧不正确')
        key=json.dumps([state['equipment'],state['head_visible'],state['effects_visible']],sort_keys=True)
        if key in self.scene_cache:
            scene=self.scene_cache[key];self.scene_cache.move_to_end(key)
        else:
            chosen={s:CHOICES[s](**state['equipment'][s])for s in CHOICES}
            scene,info=self.character.resolve(chosen['Gloves'],feet=chosen['Feet'],body=chosen['Body'],legs=chosen['Legs'],
                head=chosen['Head'],head_visible=state['head_visible'])
            scene=self.accessories.resolve(scene,state,info)
            # Keep the preset framing as a minimum, expanding only for actual
            # referenced geometry such as wings. Dye changes reuse this scene.
            import numpy as np
            low=scene.bounds.min(axis=0).copy();high=scene.bounds.max(axis=0).copy()
            for mesh in scene.meshes:
                if not len(mesh['indices']):continue
                visible=mesh['posed_positions'][np.unique(mesh['indices'])]
                low=np.minimum(low,visible.min(axis=0));high=np.maximum(high,visible.max(axis=0))
            scene.bounds=np.asarray((low,high));self.scene_cache[key]=scene
            while len(self.scene_cache)>6:self.scene_cache.popitem(last=False)
        if scene.pending_meshes or scene.omitted:raise NotImplementedError('当前装备含尚未接入的可见部件')
        if getattr(scene,'effect_frame',None)!=frame:
            self.prepare_effects(scene,enabled=state['effects_visible'],frame=frame)
        self.renderer.zoom=state['zoom'];self.renderer.pitch=state['pitch'];self.renderer.pan=state['pan']
        image,_=self.renderer.draw_scene(scene,state['yaw'])
        self.effect_frame=frame
        url=self.image_record(image)
        result={'image':url,'state':state,'seconds':round(time.perf_counter()-started,3),
            'visible_meshes':len(scene.meshes),'renderer':'native-d3d11-3d','scene_cache_entries':len(self.scene_cache),
            'shader_families':sorted({mesh['material']['shader']for mesh in scene.meshes}),
            'notices':sorted({mesh['preview_notice']for mesh in scene.meshes if mesh.get('preview_notice')}|set(scene.effect_notices)),
            'equipment_effects':scene.effect_summaries,'effect_draws':self.renderer.last_effect_draws,'effect_frame':frame}
        with self.lock:self.last=result
        return result

    def prepare_effects(self,scene,*,enabled=True,frame=30.):
        scene.effect_samples=[];scene.effect_summaries=[];scene.effect_notices=[]
        if not enabled or not self.character.outfit.equipment_effects_enabled:
            scene.effect_frame=frame;return
        from effect_sample import prepare,UnsupportedEffect
        outfit=self.character.outfit
        worlds=scene.skinning@outfit.bind
        bones={name:worlds[index]for name,index in outfit.names.items()}
        sources={(mesh['slot'],mesh['equipment_effect_source'].path):mesh['equipment_effect_source']
            for mesh in scene.meshes if mesh.get('equipment_effect_source')is not None}
        if sources and not hasattr(outfit,'effect_bind_points'):
            from effect_binding import EffectBindPoints
            outfit.effect_bind_points=EffectBindPoints(scene.pack,outfit.base.pose['identity']['race_sex_id'])
        for (slot,path),effect in sources.items():
            samples,pending=prepare(effect,bones,frame,bind_points=outfit.effect_bind_points)
            if samples:
                from effect_renderer import EffectRenderer
                if not hasattr(self.renderer,'effect_renderer'):self.renderer.effect_renderer=EffectRenderer(self.renderer,scene.pack)
                accepted=[];pending=list(pending)
                for sample in samples:
                    try:self.renderer.effect_renderer.plan(sample)
                    except UnsupportedEffect as error:pending.append(str(error))
                    else:accepted.append(sample)
                samples=accepted;pending=tuple(sorted(set(pending)))
            scene.effect_samples.extend({**s,'effect_path':path}for s in samples)
            scene.effect_summaries.append({**effect.summary(),'slot':slot,'preview_frame':frame,
                'sampled_particles':len(samples),'pending':list(pending),'preview_policy':'sampled-normal-scheduler-stable-probability-zero-random-offsets',
                'particles_rendered':bool(samples),'effect_complete':False})
            if outfit.effect_bind_points.sha256:
                scene.effect_summaries[-1]['attachment_source']={'path':outfit.effect_bind_points.path,'sha256':outfit.effect_bind_points.sha256}
            if effect.missing:scene.effect_notices.append('装备附加特效文件在本机缺失。')
            elif samples:
                scene.effect_notices.append('装备特效：'+('部分粒子尚未接入。'if pending else'尚未完成完整特效还原。'))
            else:scene.effect_notices.append('装备主体已显示，附加粒子特效尚未绘制。')
        scene.effect_frame=frame

    def import_dat(self,raw,name):
        path,key=self.library.add(raw,name);self.refresh_presets()
        try:return {**self.load(key),'imported':True,'model_loaded':True}
        except Exception as error:
            # The DAT copy was successfully committed before model loading.
            # Preserve that result and the previous model; do not report the
            # whole import as a failure or silently substitute an appearance.
            self.write_error(error)
            return {'imported':True,'model_loaded':False,'preset':self.presets[key][1],
                'preview_error':str(error),'appearance':self.appearance_state()}

    def describe_appearance(self,key,tribe=None):
        from presets import decode
        if key not in self.presets:raise ValueError('本地预设不存在')
        path,_=self.presets[key];record=decode(path.read_bytes());p=record['parameters']
        tribe=p['tribe']if tribe is None else tribe
        if tribe not in(11,12):raise ValueError('当前支持敖龙族女性的两个部族')
        return {**record,'id':key,'file_name':path.name,'directory':str(self.library.root),
            'fields':self.appearance_options.fields(tribe),'appearance':self.appearance_state(key),
            'scope':'国服敖龙族女性；发型列表来自当前创建菜单'}

    def appearance_changes(self,payload):
        from presets import decode,change_bytes,digest
        key=payload['id']
        if key not in self.presets:raise ValueError('本地预设不存在')
        path,_=self.presets[key];old=path.read_bytes();changes=payload['changes']
        if digest(old)!=payload['sha256']:raise ValueError('这份预设已被其他窗口或程序修改，请重新打开编辑')
        proposed=change_bytes(old,changes)
        self.appearance_options.validate(decode(old)['parameters'],decode(proposed)['parameters'])
        return key,path,proposed

    def preview_appearance(self,payload):
        from ff14_readonly import safe_output
        from presets import digest
        key,_,proposed=self.appearance_changes(payload)
        # This is a content-addressed preparation input, outside the user's
        # selectable preset library. The selected DAT is never written.
        path=safe_output(self.data/'appearance-previews'/(digest(proposed)+'.dat'),self.game)
        if path.exists():
            if path.read_bytes()!=proposed:raise ValueError('外观预览缓存文件已改变')
        else:
            temporary=path.with_name('.'+secrets.token_hex(16)+'.tmp')
            try:temporary.write_bytes(proposed);temporary.replace(path)
            finally:
                if temporary.exists():temporary.unlink()
        result=self.load_preset_path(key,path)
        self.appearance_is_preview=self.rendered_parameters!=self.presets[key][1]['parameters']
        return {**result,'appearance':self.appearance_state()}

    def save_appearance(self,payload):
        key,path,_=self.appearance_changes(payload)
        self.library.replace(path,payload['changes'],payload['sha256']);self.refresh_presets()
        if key==getattr(self,'active_preset',None):self.appearance_is_preview=False
        return {'preset':self.presets[key][1],'appearance':self.appearance_state(key),'model_updated':False}

    def appearance_state(self,key=None):
        active=getattr(self,'active_preset',None);key=active if key is None else key
        entry=self.presets.get(key);rendered=self.rendered_parameters if key==active else None
        return {'rendered_parameters':rendered,'is_preview':self.appearance_is_preview if key==active else False,
            'model_matches_file':bool(entry and rendered==entry[1]['parameters'])}

    def run(self,operation,payload):
        if self.status!='ready'and(operation not in('preset','import','appearance_save','appearance_preview')or self.renderer is None):
            raise ValueError('角色还在准备，请稍后再试')
        try:
            if operation=='render':return self.render(payload)
            if operation=='preset':result=self.load(payload['id'])
            elif operation=='import':result=self.import_dat(base64.b64decode(payload['data'],validate=True),payload.get('name',''))
            elif operation=='appearance_preview':result=self.preview_appearance(payload)
            elif operation=='appearance_save':result=self.save_appearance(payload)
            else:raise ValueError('未知操作')
            if result.get('model_loaded')is False or self.character is None:return result
            with self.lock:self.status='ready';self.error=None;self.message='可以开始搭配了'
            return result
        except Exception as error:self.write_error(error);raise

    def bootstrap(self):
        with closing(database())as conn:
            dyes=[dict(r)for r in conn.execute('SELECT stain_id,name,color,shade,sort_order,metallic FROM dyes ORDER BY shade,sort_order,stain_id')]
            counts={r['slot']:r['n']for r in conn.execute('SELECT slot,count(*) n FROM equipment GROUP BY slot')if r['slot']in SLOTS}
        with self.lock:
            return {'application':'ff14-wardrobe-demo','status':self.status,'message':self.message,'error':self.error,'token':self.token,
                'presets':[r[1]for r in self.presets.values()],'preset_id':getattr(self,'active_preset',None),
                'last':self.last,'slots':SLOTS,'groups':GROUPS,'counts':counts,'dyes':dyes,'example':EXAMPLE,
                'scope':'敖龙族女性 · 全量装备目录 · 原生双染色','game_directory':str(self.game),
                'version':'0.1 Demo','saved':self.saved(),'preset_directory':str(self.preset_directory.resolve()),
                'appearance':self.appearance_state(),'can_select_preset':self.renderer is not None and bool(self.presets)}

    def saved(self):
        path=self.data/'outfits.json'
        return json.loads(path.read_bytes())if path.exists()else[]

    def save(self,payload):
        name=payload.get('name','').strip()[:32]
        if not name:raise ValueError('请为搭配起个名字')
        state=parse_state(payload['state']);rows=self.saved()
        row={'id':secrets.token_hex(6),'name':name,'preset_id':self.active_preset,'state':state,
            'time':time.strftime('%Y-%m-%d %H:%M')}
        rows=[row,*rows][:30];path=self.data/'outfits.json';temp=path.with_suffix('.tmp')
        temp.write_text(json.dumps(rows,ensure_ascii=False,indent=2)+'\n',encoding='utf8');temp.replace(path)
        return rows

    def close_gpu(self):
        if self.characters:self.characters.close()
        elif self.character:self.character.close()
        if self.renderer:self.renderer.close()


class Handler(BaseHTTPRequestHandler):
    server_version='WardrobeDemo/0.1'
    def log_message(self,*args):pass
    def send(self,status,data,kind='application/json; charset=utf-8'):
        if not isinstance(data,bytes):data=json.dumps(data,ensure_ascii=False).encode()
        self.send_response(status);self.send_header('Content-Type',kind);self.send_header('Content-Length',str(len(data)))
        self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff')
        self.end_headers()
        try:self.wfile.write(data)
        except(BrokenPipeError,ConnectionResetError):pass

    def host_ok(self):return self.headers.get('Host')in(self.server.address,'localhost:'+str(self.server.server_port))

    def do_GET(self):
        if not self.host_ok():return self.send(403,{'error':'仅供本机使用'})
        route=urlparse(self.path);engine=self.server.engine
        try:
            if route.path=='/api/bootstrap':return self.send(200,engine.bootstrap())
            if route.path=='/api/appearance':
                q=parse_qs(route.query);key=q.get('id',[getattr(engine,'active_preset',None)])[0]
                tribe=int(q['tribe'][0])if'tribe'in q else None
                return self.send(200,engine.pool.submit(engine.describe_appearance,key,tribe).result(timeout=180))
            if route.path=='/api/equipment':
                q=parse_qs(route.query)
                return self.send(200,equipment_rows(q.get('slot',['Head'])[0],q.get('q',[''])[0],int(q.get('offset',['0'])[0]),
                    grouped=q.get('view',['items'])[0]=='groups',model_set=int(q['model'][0])if'model'in q else None))
            if route.path.startswith('/api/icons/'):
                if engine.item_icons is None:return self.send(503,{'error':'装备图标正在准备'})
                name=route.path.rsplit('/',1)[-1]
                if not name.endswith('.png')or not name[:-4].isdigit():return self.send(404,{'error':'图标不存在'})
                try:data=engine.item_icons.png(int(name[:-4]))
                except(FileNotFoundError,NotImplementedError):return self.send(404,{'error':'图标暂不可用'})
                return self.send(200,data,'image/png')
            if route.path.startswith('/api/image/'):
                key=route.path.rsplit('/',1)[-1].removesuffix('.png')
                with engine.lock:data=engine.images.get(key)
                return self.send(200,data,'image/png')if data else self.send(404,{'error':'预览已更新'})
            files={'/':('index.html','text/html; charset=utf-8'),'/app.js':('app.js','text/javascript; charset=utf-8'),'/appearance.js':('appearance.js','text/javascript; charset=utf-8'),'/style.css':('style.css','text/css; charset=utf-8')}
            if route.path in files:
                name,mime=files[route.path];return self.send(200,(Path(__file__).parent/name).read_bytes(),mime)
            self.send(404,{'error':'页面不存在'})
        except Exception as error:self.send(400,{'error':str(error)})

    def do_POST(self):
        engine=self.server.engine
        if not self.host_ok()or self.headers.get('X-Demo-Token')!=engine.token:
            return self.send(403,{'error':'请从本机试衣页面操作'})
        origin=self.headers.get('Origin')
        if origin and origin!='http://'+self.server.address:return self.send(403,{'error':'来源不匹配'})
        try:
            length=int(self.headers.get('Content-Length','0'))
            if not 0<length<128*1024:raise ValueError('请求大小不正确')
            payload=json.loads(self.rfile.read(length));route=urlparse(self.path).path
            if route=='/api/save':return self.send(200,engine.pool.submit(engine.save,payload).result(timeout=180))
            if route=='/api/shutdown':
                self.send(200,{'closed':True});threading.Thread(target=self.server.shutdown,daemon=True).start();return
            operation={'/api/render':'render','/api/preset':'preset','/api/import':'import',
                '/api/appearance/preview':'appearance_preview','/api/appearance/save':'appearance_save'}.get(route)
            if operation is None:return self.send(404,{'error':'操作不存在'})
            result=engine.pool.submit(engine.run,operation,payload).result(timeout=180);self.send(200,result)
        except NotImplementedError as error:
            self.send(422,{'error':'这件装备或组合含原型尚未接入的规则，已保留上次成功预览。','detail':str(error)})
        except Exception as error:self.send(400,{'error':'未能完成这次操作，已保留上次成功预览。','detail':str(error)})


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--game',type=Path,default=GAME)
    p.add_argument('--preset',type=Path,default=DEFAULT_PRESET);p.add_argument('--port',type=int,default=0)
    p.add_argument('--no-browser',action='store_true');args=p.parse_args()
    # Existing native workers resolve source profiles relative to this project.
    import os
    os.chdir(ROOT)
    if not args.no_browser:
        runtime=ROOT/'artifacts/demo/runtime.json'
        try:
            import urllib.request
            previous=json.loads(runtime.read_bytes())['url'];parsed=urlparse(previous)
            if parsed.scheme=='http'and parsed.hostname=='127.0.0.1'and parsed.port:
                with urllib.request.urlopen(previous+'/api/bootstrap',timeout=1.5)as response:status=json.load(response)
                if status.get('application')=='ff14-wardrobe-demo':
                    open_app(previous);print(json.dumps({'url':previous,'reused':True}),flush=True);return
        except(OSError,ValueError,KeyError):pass
    engine=DemoEngine(args.game,args.preset);server=ThreadingHTTPServer(('127.0.0.1',args.port),Handler)
    server.daemon_threads=True;server.engine=engine;server.address='127.0.0.1:'+str(server.server_port)
    url='http://'+server.address
    (engine.data/'runtime.json').write_text(json.dumps({'url':url,'pid':os.getpid(),'local_only':True},indent=2)+'\n',encoding='utf8')
    print(json.dumps({'url':url,'local_only':True}),flush=True)
    if not args.no_browser:open_app(url)
    try:server.serve_forever(poll_interval=.2)
    except KeyboardInterrupt:pass
    finally:
        server.server_close();engine.pool.submit(engine.close_gpu).result(timeout=30);engine.pool.shutdown(wait=True)


if __name__=='__main__':main()
