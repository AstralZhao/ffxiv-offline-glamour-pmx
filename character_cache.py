"""Checked portable static character packets, plus bounded process reuse."""
from collections import OrderedDict
import hashlib
from io import BytesIO
import json
from pathlib import Path
from types import SimpleNamespace
import uuid
import zipfile

import numpy as np
from ff14_readonly import SqPack,safe_output
from preset_character import import_preset
from preset_static_character import PresetStaticCharacter
from runtime_files import application_files,ROOT


def digest(raw):return hashlib.sha256(raw).hexdigest()


def write_packet(path,value):
    with zipfile.ZipFile(path,'x',compression=zipfile.ZIP_DEFLATED,compresslevel=1)as archive:
        counter=0
        def encode(obj):
            nonlocal counter
            if isinstance(obj,(np.ndarray,np.generic,bytes)):
                name='data/'+str(counter);counter+=1
                if isinstance(obj,(np.ndarray,np.generic)):
                    output=BytesIO();np.save(output,np.asarray(obj),allow_pickle=False);raw=output.getvalue()
                    kind='scalar'if isinstance(obj,np.generic)else'array'
                else:raw=obj;kind='bytes'
                archive.writestr(name,raw);return {'type':kind,'file':name,'sha256':digest(raw)}
            if isinstance(obj,dict):return {'type':'dict','items':[[encode(k),encode(v)]for k,v in obj.items()]}
            if isinstance(obj,tuple):return {'type':'tuple','items':[encode(v)for v in obj]}
            if isinstance(obj,list):return [encode(v)for v in obj]
            if obj is None or type(obj)in(str,int,float,bool):return obj
            raise TypeError('Unsupported cached value: '+type(obj).__name__)
        archive.writestr('packet.json',json.dumps(encode(value),ensure_ascii=False,allow_nan=False,separators=(',',':')))


def read_packet(path):
    with zipfile.ZipFile(path)as archive:
        entries=archive.infolist()
        if len(entries)>20000 or sum(e.file_size for e in entries)>512*1024**2:raise ValueError('Character cache exceeds bounds')
        def decode(obj):
            if isinstance(obj,list):return [decode(v)for v in obj]
            if not isinstance(obj,dict):return obj
            kind=obj['type']
            if kind=='dict':return {decode(k):decode(v)for k,v in obj['items']}
            if kind=='tuple':return tuple(decode(v)for v in obj['items'])
            if kind not in('array','scalar','bytes'):raise ValueError('Unknown cache value')
            raw=archive.read(obj['file'])
            if digest(raw)!=obj['sha256']:raise ValueError('Character cache member changed')
            if kind=='bytes':return raw
            value=np.load(BytesIO(raw),allow_pickle=False)
            return value[()]if kind=='scalar'else value
        return decode(json.loads(archive.read('packet.json')))


def strings(value,replace):
    if isinstance(value,str):return replace(value)
    if isinstance(value,dict):return {strings(k,replace):strings(v,replace)for k,v in value.items()}
    if isinstance(value,tuple):return tuple(strings(v,replace)for v in value)
    if isinstance(value,list):return [strings(v,replace)for v in value]
    return value


class CharacterCache:
    def __init__(self,pack,store,hair,root=None,*,limit=4):
        self.pack,self.store,self.hair=pack,store,hair
        self.root=safe_output((Path(root)if root else ROOT/'artifacts/demo/character-cache')/'index.json',pack.game_root).parent
        self.entries=OrderedDict();self.limit=limit
        code,profiles=application_files()
        ui_only={'qt_client','qt_theme','qt_runtime','qt_settings','launch','client','settings','catalogue','catalogue_cache',
            'equipment_library','equipment_tag_features','user_guide','icons','gpu_selection','effect_clock'}
        self.identity={'format':1,'client':(pack.game_root/'game/ffxivgame.ver').read_text().strip(),
            'numpy_version':np.__version__,
            'executable_sha256':digest((pack.game_root/'game/ffxiv_dx11.exe').read_bytes()),
            'files':{str(p.relative_to(ROOT)).replace('\\','/'):digest(p.read_bytes())for p in code+profiles if p.stem not in ui_only}}

    def key(self,path):
        preset=import_preset(path)
        identity={**self.identity,'preset_sha256':preset['source_sha256']}
        return digest(json.dumps(identity,sort_keys=True).encode()),identity

    def acquire(self,path):
        path=Path(path).resolve();key,identity=self.key(path);memory_key=(str(path),key)
        if memory_key in self.entries:
            self.entries.move_to_end(memory_key);entry=self.entries[memory_key]
            return entry,{'memory_hit':True,'disk_hit':True,'key':key}
        from accessories import Accessories
        from equipment import DemoOutfit
        packet_path=self.root/(key+'.cache');disk_hit=False
        # This packet owns its own resource journal even when indexes are shared.
        pack=SqPack(self.pack.game_root);pack.indexes=self.pack.indexes;pack.synonyms=self.pack.synonyms
        if packet_path.is_file():
            try:
                packet=read_packet(packet_path)
                if packet['identity']!=identity:raise ValueError('Character cache identity changed')
                shader_sources={}
                for name,expected in packet['resources'].items():
                    raw=pack.read(name)
                    if digest(raw)!=expected:raise ValueError('Character cache source changed: '+name)
                    if name in packet['package_paths']:shader_sources[name]=raw
            except (ValueError,KeyError,OSError,zipfile.BadZipFile):packet=None
            if packet is not None:
                scene=self.materialize(packet,path,key,pack,shader_sources)
                candidate=PresetStaticCharacter.__new__(PresetStaticCharacter)
                candidate.pose_path=Path(scene.pose['source_assembly']['path']).with_name('pose.json');candidate.hair_scenes=self.hair
                candidate.base=scene;candidate.closed=False;candidate.outfit=DemoOutfit(scene)
                candidate.preparation_identity={'key':key,'disk_cache_hit':True,'identity':identity};disk_hit=True
        if not disk_hit:
            candidate=PresetStaticCharacter.from_preset(pack,path,store=self.store,hair_scenes=self.hair)
            original=candidate.outfit
            try:candidate.outfit=DemoOutfit(candidate.base)
            except Exception:candidate.close();raise
            original.close()
            try:self.save(packet_path,identity,candidate,pack,path)
            except Exception:candidate.close();raise
        entry={'character':candidate,'accessories':Accessories(candidate),'scenes':OrderedDict()}
        self.entries[memory_key]=entry
        return entry,{'memory_hit':False,'disk_hit':disk_hit,'key':key}

    def save(self,target,identity,character,pack,path):
        scene=character.base;assembly_path=Path(scene.pose['source_assembly']['path'])
        assembly=json.loads(assembly_path.read_bytes());record=assembly['bind_geometry_bundle']
        bundle=(assembly_path.parent/record['file']).read_bytes()
        if digest(bundle)!=record['sha256']:raise ValueError('Reference cache source changed')
        manifest=json.loads((character.pose_path.parent.parent/'manifest.json').read_bytes())
        resources={**manifest['resource_hashes'],**{k:v['sha256']for k,v in pack.read_log.items()}}
        scene_data={k:v for k,v in vars(scene).items()if k not in('pack','packages')}
        def portable(text):
            for old,new in ((str(path),'${PRESET}'),(str(pack.game_root),'${GAME}'),(str(ROOT),'${APP}')):text=text.replace(old,new)
            return text
        # Only the pose/report graphs contain workstation paths. Walking every
        # shader instruction/binding here makes persistent loads needlessly slow.
        scene_data['pose']=strings(scene_data['pose'],portable)
        packet={'identity':identity,'resources':resources,'scene':scene_data,'package_paths':list(scene.packages),
            'assembly':strings(assembly,portable),'reference_bundle':bundle}
        temporary=target.with_name(target.name+'.'+uuid.uuid4().hex+'.tmp')
        try:write_packet(temporary,packet);temporary.replace(target)
        finally:
            if temporary.exists():temporary.unlink()

    def materialize(self,packet,path,key,pack,shader_sources):
        folder=self.root/'materialized'/key/digest(str(path).encode())[:16];folder.mkdir(parents=True,exist_ok=True)
        def current(text):
            for old,new in (('${PRESET}',str(path)),('${GAME}',str(pack.game_root)),('${APP}',str(ROOT))):text=text.replace(old,new)
            return text
        data=packet['scene'];data['pose']=strings(data['pose'],current);assembly=strings(packet['assembly'],current)
        # Reparse the few verified SHPK byte streams directly. Serializing their
        # millions of expanded dictionary fields costs more than source parsing.
        from shader_feasibility import inspect_shpk
        data['packages']={name:inspect_shpk(shader_sources[name],True)[1]['_package']for name in packet['package_paths']}
        assembly['preset']['path']=str(path);assembly['bind_geometry_bundle']['file']='reference.npz'
        (folder/'reference.npz').write_bytes(packet['reference_bundle'])
        raw=json.dumps(assembly,ensure_ascii=False).encode();assembly_path=folder/'assembly.json';assembly_path.write_bytes(raw)
        data['pose']['preset']['path']=str(path)
        data['pose']['source_assembly'].update(path=str(assembly_path),sha256=digest(raw))
        pose_raw=json.dumps(data['pose'],ensure_ascii=False).encode();(folder/'pose.json').write_bytes(pose_raw)
        data['identity'].update(pose_report_sha256=digest(pose_raw),reference_report_sha256=digest(raw))
        return SimpleNamespace(**data,pack=pack)

    def trim(self,active):
        while len(self.entries)>self.limit:
            key=next(k for k,v in self.entries.items()if v['character']is not active)
            self.entries.pop(key)['character'].close()

    def close(self):
        for entry in self.entries.values():entry['character'].close()
        self.entries.clear()
