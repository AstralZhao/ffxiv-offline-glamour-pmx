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
from runtime_files import application_files,native_static_assets,ROOT


from source_session import sha256 as digest


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
        self.live_root=self.root/'live'/uuid.uuid4().hex;self.live_directories=set()
        code,profiles=application_files();native=native_static_assets()
        ui_only={'qt_client','qt_theme','qt_runtime','qt_settings','launch','client','settings','catalogue','catalogue_cache',
            'equipment_library','equipment_tag_features','user_guide','icons','gpu_selection','effect_clock'}
        self.identity={'format':3,'client':(pack.game_root/'game/ffxivgame.ver').read_text().strip(),
            'numpy_version':np.__version__,
            'executable_sha256':digest((pack.game_root/'game/ffxiv_dx11.exe').read_bytes()),
            'files':{str(p.relative_to(ROOT)).replace('\\','/'):digest(p.read_bytes())for p in code+profiles+native if p.stem not in ui_only}}

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
        pack=self.pack.fork()if hasattr(self.pack,'fork')else SqPack(self.pack.game_root)
        pack.indexes=self.pack.indexes;pack.synonyms=self.pack.synonyms
        if packet_path.is_file():
            try:
                packet=read_packet(packet_path)
                if packet['identity']!=identity:raise ValueError('Character cache identity changed')
                from source_session import stamp
                signatures=packet.get('source_file_identity',{})
                unchanged=bool(signatures)
                for relative,expected in signatures.items():
                    file=(pack.game_root/relative).resolve()
                    if not file.is_relative_to(pack.game_root)or not file.is_file()or list(stamp(file))!=expected:
                        unchanged=False;break
                shader_sources={}
                names=()if unchanged else packet['resources']
                for name in names:
                    raw=pack.read(name)
                    if digest(raw)!=packet['resources'][name]:raise ValueError('Character cache source changed: '+name)
                    if name in packet['package_paths']:shader_sources[name]=raw
                if not unchanged:
                    # A moved installation/resource pack is fully hashed once.
                    # Persist its verified file identities for later launches.
                    packet['source_file_identity']=self.source_signatures(pack,packet['resources'])
                    temporary=packet_path.with_name(packet_path.name+'.'+uuid.uuid4().hex+'.tmp')
                    try:write_packet(temporary,packet);temporary.replace(packet_path)
                    finally:
                        if temporary.exists():temporary.unlink()
            except (ValueError,KeyError,OSError,zipfile.BadZipFile):packet=None
            if packet is not None:
                scene=self.materialize(packet,path,key,pack,shader_sources)
                candidate=PresetStaticCharacter.__new__(PresetStaticCharacter)
                candidate.pose_path=Path(scene.pose['source_assembly']['path']).with_name('pose.json');candidate.hair_scenes=self.hair
                candidate.base=scene;candidate.closed=False;candidate.outfit=DemoOutfit(scene)
                candidate.resource_hashes=dict(packet['resources'])
                candidate.preparation_identity={'key':key,'disk_cache_hit':True,'identity':identity};disk_hit=True
        if not disk_hit:
            from appearance_preview import material_preview
            candidate=None
            for entry in reversed(self.entries.values()):
                candidate=material_preview(entry['character'],path)
                if candidate is not None:break
            if candidate is None:
                from hair_preview import hair_preview
                live=self.live_root/key;self.live_directories.add(live)
                for entry in reversed(self.entries.values()):
                    candidate=hair_preview(entry['character'],path,live)
                    if candidate is not None:break
            if candidate is None:
                from live_pose import geometry_preview
                for entry in reversed(self.entries.values()):
                    candidate=geometry_preview(entry['character'],path,live)
                    if candidate is not None:break
            if candidate is None:
                candidate=PresetStaticCharacter.from_preset(pack,path,store=self.store,hair_scenes=self.hair)
                original=candidate.outfit
                try:candidate.outfit=DemoOutfit(candidate.base)
                except Exception:candidate.close();raise
                original.close()
                try:self.save(packet_path,identity,candidate,pack,path)
                except Exception:candidate.close();raise
        entry={'character':candidate,'accessories':Accessories(candidate),'scenes':OrderedDict()}
        self.entries[memory_key]=entry
        return entry,{'memory_hit':False,'disk_hit':disk_hit,'key':key,'material_only':candidate.preparation_identity.get('material_only',False),
            'live_pose':candidate.preparation_identity.get('live_pose',False),'hair_swap':candidate.preparation_identity.get('hair_swap',False)}

    def save(self,target,identity,character,pack,path):
        scene=character.base;assembly_path=Path(scene.pose['source_assembly']['path'])
        from prepared_programs import prepare_programs,compact_packages
        prepare_programs(scene)
        assembly=json.loads(assembly_path.read_bytes());assembly['preset']=dict(scene.pose['preset']);record=assembly['bind_geometry_bundle']
        bundle=(assembly_path.parent/record['file']).read_bytes()
        if digest(bundle)!=record['sha256']:raise ValueError('Reference cache source changed')
        known=getattr(character,'resource_hashes',None)
        if known is None:known=json.loads((character.pose_path.parent.parent/'manifest.json').read_bytes())['resource_hashes']
        resources={**known,**{k:v['sha256']for k,v in pack.read_log.items()}};character.resource_hashes=dict(resources)
        posed_record=scene.pose['customized_stance_geometry_bundle'];posed=(character.pose_path.parent/posed_record['file']).read_bytes()
        if digest(posed)!=posed_record['sha256']:raise ValueError('Pose cache source changed')
        scene_data={k:v for k,v in vars(scene).items()if k not in('pack','packages')}
        def portable(text):
            for old,new in ((str(path),'${PRESET}'),(str(pack.game_root),'${GAME}'),(str(ROOT),'${APP}')):text=text.replace(old,new)
            return text
        # Only the pose/report graphs contain workstation paths. Walking every
        # shader instruction/binding here makes persistent loads needlessly slow.
        scene_data['pose']=strings(scene_data['pose'],portable)
        source_files=self.source_signatures(pack,resources)
        packet={'identity':identity,'resources':resources,'source_file_identity':source_files,'scene':scene_data,'package_paths':list(scene.packages),
            'assembly':strings(assembly,portable),'reference_bundle':bundle,'pose_bundle':posed,'selected_packages':compact_packages(scene)}
        temporary=target.with_name(target.name+'.'+uuid.uuid4().hex+'.tmp')
        try:write_packet(temporary,packet);temporary.replace(target)
        finally:
            if temporary.exists():temporary.unlink()

    def source_signatures(self,pack,resources):
        from source_session import stamp
        files={pack.locate(name)[0]for name in resources};files.update(pack.indexes)
        return {str(p.relative_to(pack.game_root)).replace('\\','/'):list(stamp(p))for p in files}

    def persist_saved(self,path,character):
        key,identity=self.key(path)
        if import_preset(path)['parameters']!=character.base.pose['preset']['parameters']:
            entry,_=self.acquire(path)
            prepared=entry['character']
            self.save(self.root/(key+'.cache'),identity,prepared,prepared.base.pack,Path(path))
            self.trim(character)
            return True
        # Saving a matching live preview prepares its next cold-process load.
        # The interactive preview itself never waits for archive compression.
        self.save(self.root/(key+'.cache'),identity,character,character.base.pack,Path(path))
        return True

    def materialize(self,packet,path,key,pack,shader_sources):
        folder=self.root/'materialized'/key/digest(str(path).encode())[:16];folder.mkdir(parents=True,exist_ok=True)
        def current(text):
            for old,new in (('${PRESET}',str(path)),('${GAME}',str(pack.game_root)),('${APP}',str(ROOT))):text=text.replace(old,new)
            return text
        data=packet['scene'];data['pose']=strings(data['pose'],current);assembly=strings(packet['assembly'],current)
        # Reparse the few verified SHPK byte streams directly. Serializing their
        # millions of expanded dictionary fields costs more than source parsing.
        from prepared_programs import LazyPackage
        data['packages']={name:LazyPackage(packet['selected_packages'][name],pack,name,packet['resources'][name])for name in packet['package_paths']}
        assembly['preset']['path']=str(path);assembly['bind_geometry_bundle']['file']='reference.npz'
        (folder/'reference.npz').write_bytes(packet['reference_bundle'])
        (folder/'posed.npz').write_bytes(packet['pose_bundle']);data['pose']['customized_stance_geometry_bundle']['file']='posed.npz'
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
        self.clean_live()

    def clean_live(self):
        root=getattr(self,'live_root',None)
        if root is None:return
        retained={Path(c.pose_path).parent.resolve()for e in self.entries.values()if hasattr(c:=e['character'],'pose_path')}
        for folder in list(self.live_directories):
            resolved=folder.resolve()
            if resolved in retained:continue
            if not resolved.is_relative_to(root.resolve())or resolved==root.resolve():raise ValueError('Preview cleanup outside owned session')
            try:
                for name in('assembly.json','pose.json','reference.npz','posed.npz'):(resolved/name).unlink(missing_ok=True)
                if resolved.exists():resolved.rmdir()
                self.live_directories.discard(folder)
            except OSError:pass
        try:root.rmdir()
        except OSError:pass

    def close(self):
        for entry in self.entries.values():entry['character'].close()
        self.entries.clear()
        self.clean_live()
