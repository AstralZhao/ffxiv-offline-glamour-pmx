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
    if type(yaw)not in(int,float)or not math.isfinite(yaw)or not -3600<=yaw<=3600:raise ValueError('观察角度不正确')
    if type(zoom)not in(int,float)or not math.isfinite(zoom)or not .65<=zoom<=2.5:raise ValueError('缩放范围不正确')
    if type(visible)is not bool:raise ValueError('头饰显示状态不正确')
    if type(pitch)not in(int,float)or not math.isfinite(pitch)or not -65<=pitch<=65:raise ValueError('俯仰角度不正确')
    if type(pan)not in(int,float)or not math.isfinite(pan)or not -1<=pan<=1:raise ValueError('相机平移范围不正确')
    return {'equipment':result,'head_visible':visible,'yaw':float(yaw),'zoom':float(zoom),'pitch':float(pitch),'pan':float(pan)}


def equipment_rows(slot,query='',offset=0,limit=60):
    if slot not in SLOTS:raise ValueError('部位尚未接入原型')
    if not 0<=offset<=50000 or not 1<=limit<=100:raise ValueError('分页范围不正确')
    query=query[:80].strip()
    with closing(database())as conn:
        where='slot=?';args=['FingerL'if slot=='FingerR'else slot]
        if query:
            where+=' AND (name LIKE ? ESCAPE \'\\\' OR CAST(item_id AS TEXT)=?)'
            args += ['%'+query.replace('\\','\\\\').replace('%','\\%').replace('_','\\_')+'%',query]
        total=conn.execute('SELECT count(*) FROM equipment WHERE '+where,args).fetchone()[0]
        rows=conn.execute('SELECT item_id,name,dye_count,level,category_name FROM equipment WHERE '+where+
            ' ORDER BY (item_id=?) DESC,item_id DESC LIMIT ? OFFSET ?',[*args,EXAMPLE.get(slot,0),limit,offset]).fetchall()
    return {'items':[dict(r)for r in rows],'total':total,'offset':offset,'more':offset+len(rows)<total}


class DemoEngine:
    """All native GPU and SQLite outfit work stays on one dedicated thread."""
    def __init__(self,game,preset):
        self.game=game.resolve();self.default_preset=preset.resolve();self.character=None;self.renderer=None
        self.pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix='WardrobeGPU')
        self.lock=threading.RLock();self.status='loading';self.message='正在读取国服资源与外观预设…'
        self.images=OrderedDict();self.presets={};self.last=None;self.error=None;self.scene_cache=OrderedDict()
        self.data=ROOT/'artifacts/demo';self.data.mkdir(parents=True,exist_ok=True)
        self.token=secrets.token_urlsafe(24);self.ready=self.pool.submit(self.initialize)

    def image_record(self,image):
        output=BytesIO();image.save(output,format='PNG');raw=output.getvalue();key=hashlib.sha256(raw).hexdigest()
        with self.lock:
            self.images[key]=raw;self.images.move_to_end(key)
            while len(self.images)>20:self.images.popitem(last=False)
        return '/api/image/'+key+'.png'

    def register_preset(self,path):
        from preset_character import import_preset
        record=import_preset(path);p=record['parameters'];key=hashlib.sha256(str(path.resolve()).encode()).hexdigest()[:24]
        if (p['race'],p['gender'],p['body_type'])!=(6,1,1)or p['tribe']not in(11,12):
            raise ValueError('原型当前支持敖龙族女性的两个部族')
        entry={'id':key,'name':record.get('comment')or path.stem,'file_name':path.name,
            'clan':'晨曦之民'if p['tribe']==11 else'暮晖之民','face':p['face'],'hair':p['hair']}
        with self.lock:self.presets[key]=(path,entry)
        return entry

    def initialize(self):
        try:
            from ff14_readonly import SqPack
            from prepared_preset_store import PreparedPresetStore
            from static_hair_scene import StaticHairScenes
            from render_preset_materials import PresetMaterialRenderer
            from static_material_inputs import camera_payload
            class PreviewRenderer(PresetMaterialRenderer):
                zoom=1.;pitch=0.;pan=0.
                def __init__(self,*args,**kwargs):
                    self.context_functions={};self.geometry_buffers=OrderedDict();self.geometry_pointers=set();self.geometry_bytes=0
                    super().__init__(*args,**kwargs)

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
            self.pack=SqPack(self.game);self.store=PreparedPresetStore(self.pack);self.hair=StaticHairScenes()
            self.renderer=PreviewRenderer(640,840,1)
            paths=[self.default_preset]
            paths+=sorted(self.default_preset.parent.glob('FFXIV_CHARA_*.dat'))
            paths+=sorted((self.data/'presets').glob('*.dat'))
            seen=set()
            for path in paths:
                if not path.is_file()or path.resolve()in seen:continue
                seen.add(path.resolve())
                try:self.register_preset(path)
                except ValueError:
                    if path==self.default_preset:raise
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
        from preset_static_character import PresetStaticCharacter
        from accessories import Accessories
        if key not in self.presets:raise ValueError('预设不存在')
        candidate=PresetStaticCharacter.from_preset(self.pack,self.presets[key][0],store=self.store,hair_scenes=self.hair)
        candidate.outfit.preview_restricted_items=True
        previous=self.character;old_accessories=getattr(self,'accessories',None);old_scenes=self.scene_cache
        self.character=candidate;self.accessories=Accessories(candidate);self.scene_cache=OrderedDict()
        try:result=self.render(parse_state({}))
        except Exception:
            candidate.close();self.character=previous;self.accessories=old_accessories;self.scene_cache=old_scenes;raise
        self.active_preset=key
        if previous is not None:previous.close()
        return {**result,'preset':self.presets[key][1]}

    def render(self,raw):
        from preset_outfit import CHOICES
        state=parse_state(raw);started=time.perf_counter()
        key=json.dumps([state['equipment'],state['head_visible']],sort_keys=True)
        if key in self.scene_cache:
            scene=self.scene_cache[key];self.scene_cache.move_to_end(key)
        else:
            chosen={s:CHOICES[s](**state['equipment'][s])for s in CHOICES}
            scene,info=self.character.resolve(chosen['Gloves'],feet=chosen['Feet'],body=chosen['Body'],legs=chosen['Legs'],
                head=chosen['Head'],head_visible=state['head_visible'])
            scene=self.accessories.resolve(scene,state,info);self.scene_cache[key]=scene
            while len(self.scene_cache)>6:self.scene_cache.popitem(last=False)
        if scene.pending_meshes or scene.omitted:raise NotImplementedError('当前装备含尚未接入的可见部件')
        self.renderer.zoom=state['zoom'];self.renderer.pitch=state['pitch'];self.renderer.pan=state['pan']
        image,_=self.renderer.draw_scene(scene,state['yaw'])
        url=self.image_record(image)
        result={'image':url,'state':state,'seconds':round(time.perf_counter()-started,3),
            'visible_meshes':len(scene.meshes),'renderer':'native-d3d11-3d','scene_cache_entries':len(self.scene_cache)}
        with self.lock:self.last=result
        return result

    def import_dat(self,raw,name):
        if len(raw)!=212:raise ValueError('请导入游戏保存的原生212字节DAT外观预设')
        directory=self.data/'presets';directory.mkdir(exist_ok=True)
        key=hashlib.sha256(raw).hexdigest();path=directory/(key+'.dat')
        if not path.exists():path.write_bytes(raw)
        entry=self.register_preset(path)
        return self.load(entry['id'])

    def run(self,operation,payload):
        if self.status!='ready'and(operation not in('preset','import')or self.renderer is None):
            raise ValueError('角色还在准备，请稍后再试')
        try:
            if operation=='render':return self.render(payload)
            if operation=='preset':result=self.load(payload['id'])
            elif operation=='import':result=self.import_dat(base64.b64decode(payload['data'],validate=True),payload.get('name',''))
            else:raise ValueError('未知操作')
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
                'version':'0.1 Demo','saved':self.saved()}

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
        if self.character:self.character.close()
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
            if route.path=='/api/equipment':
                q=parse_qs(route.query)
                return self.send(200,equipment_rows(q.get('slot',['Head'])[0],q.get('q',[''])[0],int(q.get('offset',['0'])[0])))
            if route.path.startswith('/api/image/'):
                key=route.path.rsplit('/',1)[-1].removesuffix('.png')
                with engine.lock:data=engine.images.get(key)
                return self.send(200,data,'image/png')if data else self.send(404,{'error':'预览已更新'})
            files={'/':('index.html','text/html; charset=utf-8'),'/app.js':('app.js','text/javascript; charset=utf-8'),'/style.css':('style.css','text/css; charset=utf-8')}
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
            operation={'/api/render':'render','/api/preset':'preset','/api/import':'import'}.get(route)
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
