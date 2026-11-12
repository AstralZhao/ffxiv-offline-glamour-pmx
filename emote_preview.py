"""First offline emote player: source single-body timelines, bounded clips."""
from collections import OrderedDict
from dataclasses import replace
import copy,hashlib,json,math,re,struct
from pathlib import Path
import numpy as np
from ff14_readonly import Sheet
from cn_pap_load_table import PapLoadTable
from default_stance_probe import pap_header
from havok_probe import TagReader,read_skeleton
from spline_animation import SplineAnimation
from prepare_fixed_stance import normal_local_pose,world_pose
from prepare_preset_customization import bundle_arrays
from preset_customization import body_world
from static_shared_idle import local_shared_pose,identity_shared_binding


def selection(value):
    if value is None:return None
    if not isinstance(value,dict)or set(value)-{'id','time','phase'}:raise ValueError('情感动作参数不正确')
    identifier=value.get('id');time=value.get('time',0.)
    if type(identifier)is not int or not 0<identifier<65536:raise ValueError('情感动作编号不正确')
    if type(time)not in(int,float)or not math.isfinite(time)or not 0<=time<=3600:raise ValueError('情感动作时间不正确')
    if 'phase'in value and value['phase']!='end':raise ValueError('情感动作阶段不正确')
    return {'id':identifier,'time':float(time),**({'phase':'end'}if 'phase'in value else{})}


def catalogue(pack):
    from emote_resources import LAYOUTS
    sheets={name:Sheet(pack,name)for name in('Emote','ActionTimeline','EmoteMode')}
    for name,sheet in sheets.items():
        if(sheet.header.column_hash,sheet.header.fixed_size)!=LAYOUTS[name]:raise ValueError('情感动作数据表布局尚未核对')
    actions={r.id:r.value(6)for r in sheets['ActionTimeline'].rows()};result=[]
    modes={r.id:(r.value(0),r.value(1))for r in sheets['EmoteMode'].rows()}
    emotes={r.id:r for r in sheets['Emote'].rows()}
    for row in sheets['Emote'].rows():
        name=row.value(0);key=actions.get(row.value(1),'')
        if not name or not key:continue
        if not re.fullmatch('[a-z0-9_/]+',key)or '..'in key.split('/'):raise ValueError('情感动作路径不正确')
        facial=key.startswith('facial/')
        mode=modes.get(row.value(12));start_key=None;end_key=None;end_id=None
        if mode and mode[0]==row.id:
            start_key=actions.get(row.value(2))or None
            end_row=emotes.get(mode[1])
            if end_row is not None and mode[1]:end_key=actions.get(end_row.value(1))or None;end_id=mode[1]
        for path in(start_key,end_key):
            if path and(not re.fullmatch('[a-z0-9_/]+',path)or'..'in path.split('/')):raise ValueError('动作阶段路径不正确')
        result.append({'id':row.id,'name':name,'icon':row.value(20),'key':key,'facial':facial,
            'start_key':start_key,'end_key':end_key,'end_id':end_id,'persistent':bool(mode and mode[0]==row.id),
            'notice':'叠加到当前站姿或身体动作'if facial else'播放身体及已支持的配套表情；动作物品和特效暂不显示'})
    return sorted(result,key=lambda x:(x['facial'],x['id']))


def timeline(raw,*,facial=False):
    """One actor animation plus optional attachment/FX and excluded audio."""
    from emote_timeline import TimelineGraph,OPTIONAL_VISUALS,AUDIO
    # Keep unsupported-event errors explicit even for streams lacking the
    # required actor hierarchy; malformed known streams still fail validation.
    if len(raw)>=20 and raw[:4]==b'TMLB'and raw[12:16].startswith(b'C'):
        raise NotImplementedError('此动作的时间线事件尚未接入')
    graph=TimelineGraph(raw);animations=[e for e in graph.events if e['tag']=='C010']
    if(len(graph.actors)!=1 or len(animations)!=1 or
        any(e['tag']not in OPTIONAL_VISUALS|AUDIO|{'C010'}for e in graph.events)):
        raise NotImplementedError('此动作的角色或动画轨道组合尚未接入')
    event=animations[0]
    if graph.root['start_frame']or event['start_frame']or event['actor_start']or event['track_start']or event['track_lua']:
        raise NotImplementedError('此动作的延迟或轨道组合尚未接入')
    start=event['sample_start'];end=event['sample_end'];flags=event['flags']
    if any(event['payload'][8:12])or flags!=(0x08000001 if facial else 0)or start!=0 or end<=0:
        raise NotImplementedError('此动作的分层或表情控制尚未接入')
    if facial and end!=1.:raise NotImplementedError('此表情不是单帧定格轨道')
    # C010 AnimationEnd is a clip-frame control, not an unconditional lifetime.
    # This path admits Flags==0 only: play the PAP's authored duration. Enabled
    # time control still fails the flags guard above until its mapping is added.
    return event['name'],math.inf


def character_timeline(graph,clips):
    """Extract the actor clip for standalone preview, excluding scene logic.

    Graph ownership and source PAP bounds remain validated by their parsers.
    Interaction waits, actor placement and props do not gate this preview.
    """
    available={v['name']for v in clips if not v['is_face']}
    order={actor:i for i,actor in enumerate(graph.actors)}
    candidates=[e for e in graph.events if e['tag']=='C010'and e['name']in available]
    if not candidates:raise NotImplementedError('此动作没有可读取的角色动画片段')
    event=min(candidates,key=lambda e:(order[e['actor_id']],e['actor_start']+e['track_start']+e['start_frame'],e['id']))
    return event,tuple(e for e in graph.events if e['id']!=event['id'])


def pap_route(pack,race,key,*,motion='bt_common'):
    plt=PapLoadTable(pack.read('chara/xls/animation/paploadtable.plt'));exact=[];wild=[]
    for i in range(plt.common_count):
        _,prefix,offset=struct.unpack_from('<III',plt.raw,plt.common+i*12);start=plt.names+offset;end=plt.raw.find(b'\0',start)
        if not 0<=start<end<=start+192:raise ValueError('动作路由名称越界')
        # PLT names repeat for each weapon motion prefix. Ordinary sheathed
        # emotes select bt_common before applying the race's sparse override.
        if plt.string(plt.prefix+prefix,plt.names)!=motion:continue
        name=plt.raw[start:end].decode('ascii')
        if name==key:exact.append(i)
        elif name.endswith('/*')and key.startswith(name[:-1]):wild.append((len(name),i))
    if len(exact)>1:raise ValueError('动作路由不唯一')
    index=exact[0]if exact else max(wild)[1]if wild else None
    model,variant,prefix=struct.unpack('<IHH',plt.packet(race,1,index))if index is not None else(race,1,None)
    if model not in range(101,1802,100)or not 0<variant<10000:raise NotImplementedError('此动作的骨架路由尚未接入')
    group=plt.string(plt.prefix+prefix,plt.names)if prefix is not None else motion
    return model,f'chara/human/c{model:04}/animation/a{variant:04}/{group}/{key}.pap'


def check_binding_name(name,source,skeleton):
    root=skeleton['bones'][0]['name']
    if name!=root and not(name.startswith(f'c{source:04}_')and name.rsplit(':',1)[-1]==root):
        raise ValueError('动作原始骨架绑定不一致')


class EmotePreview:
    def __init__(self,character):
        self.character=character;self.pack=character.base.pack;self.rows=catalogue(self.pack)
        self.by_id={r['id']:r for r in self.rows};self.clips=OrderedDict();self.expressions=OrderedDict();self.current=None
        path=Path(character.base.pose['source_assembly']['path']);raw=path.read_bytes()
        if hashlib.sha256(raw).hexdigest()!=character.base.pose['source_assembly']['sha256']:raise ValueError('动作基础骨架资料已改变')
        reference=bundle_arrays(json.loads(raw),path,'bind_geometry_bundle')
        self.reference={k:reference[k]for k in('skeleton_names','skeleton_parents','skeleton_local')}
        posed=bundle_arrays(character.base.pose,character.pose_path,'customized_stance_geometry_bundle')
        self.posed={'skeleton_face_local':posed['skeleton_face_local']}
        race=character.base.pose['identity']['race_sex_id']
        self.body_count=len(read_skeleton(self.pack.read(f'chara/human/c{race:04}/skeleton/base/b0001/skl_c{race:04}b0001.sklb'))['bones'])

    def trim(self,identifier=None):
        active=self.current[1].emote if self.current else None
        path=active['source_path']if active and active['id']==identifier else None
        for key in list(self.clips):
            keep=self.clips[key]['source_path']==path if path else key==identifier
            if not keep:self.clips.pop(key)
        if identifier is None:self.current=None

    def configure_weapons(self,choices):
        signature=json.dumps(choices or{},sort_keys=True)
        if getattr(self,'weapon_choice_signature',None)==signature:return
        self.weapon_motion=None
        if choices and choices.get('MainHand',{}).get('item_id'):
            from weapons import validate_rows
            from weapon_stance import motion_prefix
            core=self.character.outfit
            rows=validate_rows(core.database,choices,core.stains)
            self.weapon_motion=motion_prefix(rows,self.pack.read('chara/xls/weapontype/motion.wtd'))
        self.weapon_choice_signature=signature

    def requires_weapon(self,identifier,*,key=None):
        row=self.by_id.get(identifier)
        if not row or row['facial']:return False
        path_key=key or row['key']
        if not hasattr(self,'weapon_requirements'):self.weapon_requirements={}
        if path_key not in self.weapon_requirements:
            _,path=pap_route(self.pack,self.character.base.pose['identity']['race_sex_id'],path_key)
            try:self.pack.read(path);self.weapon_requirements[path_key]=False
            except FileNotFoundError:self.weapon_requirements[path_key]=True
        return self.weapon_requirements[path_key]

    def clip(self,identifier,*,key=None):
        original_id=identifier
        needs_weapon=self.requires_weapon(identifier,key=key)
        motion=getattr(self,'weapon_motion',None)if needs_weapon else'bt_common'
        if needs_weapon and not motion:raise NotImplementedError('此动作需要武器，请先在主武器栏试穿对应职业的武器。')
        if key is not None:identifier=(identifier,key)
        if needs_weapon:identifier=(original_id,key,motion)
        if identifier in self.clips:self.clips.move_to_end(identifier);return self.clips[identifier]
        row=self.by_id.get(identifier[0]if isinstance(identifier,tuple)else identifier)
        if row is None:raise ValueError('情感动作不存在')
        if row['facial']:raise NotImplementedError('面部表情轨道正在适配，当前先支持身体动作')
        path_key=key or row['key']
        tmb=self.pack.read('chara/action/'+path_key+'.tmb')
        race=self.character.base.pose['identity']['race_sex_id'];source,path=pap_route(self.pack,race,path_key,motion=motion)
        try:raw=self.pack.read(path)
        except FileNotFoundError:
            if needs_weapon:raise NotImplementedError('当前武器职业没有此动作资源，请换用对应职业的武器。')from None
            raise
        header=pap_header(raw)
        if header['model_id']!=source or header['model_type']!=0:raise ValueError('动作文件骨架身份不一致')
        from emote_timeline import pap_timeline,TimelineGraph
        outer=TimelineGraph(tmb,character_only=True);event,outer_skipped=character_timeline(outer,header['clips']);name=event['name'];duration=math.inf
        clips=[v for v in header['clips']if v['name']==name and not v['is_face']]
        if len(clips)!=1:raise NotImplementedError('此动作的片段或面部组合尚未接入')
        embedded=pap_timeline(raw,header,name,character_only=True)
        containers=[o for o in TagReader(raw[header['havok_offset']:header['footer_offset']]).read()if o['__type__']=='hkaAnimationContainer']
        if len(containers)!=1:raise ValueError('动作容器不唯一')
        index=clips[0]['binding_index'];container=containers[0]
        if not 0<=index<len(container['bindings']):raise ValueError('动作片段绑定越界')
        binding=container['bindings'][index]
        if index>=len(container['animations'])or binding['animation']is not container['animations'][index]:
            raise ValueError('动作片段动画引用不一致')
        if binding['blendHint']!=0:raise NotImplementedError('此动作的加性混合尚未接入')
        animation=binding['animation']
        # Standalone actor preview consumes skeletal tracks. Extracted world
        # motion and annotation-triggered scene events are retained, not run.
        decoder=SplineAnimation(animation)
        skeleton=lambda r:f'chara/human/c{r:04}/skeleton/base/b0001/skl_c{r:04}b0001.sklb'
        source_raw=self.pack.read(skeleton(source));target_raw=self.pack.read(skeleton(race))
        source_skel=read_skeleton(source_raw);target_skel=read_skeleton(target_raw)
        check_binding_name(binding['originalSkeletonName'],source,source_skel)
        tracks=binding['transformTrackToBoneIndices']
        if len(tracks)!=decoder.tracks or len(set(tracks))!=len(tracks)or any(not 0<=i<len(source_skel['bones'])for i in tracks):
            raise ValueError('动作骨骼轨道越界或重复')
        # Validate all blocks once on selection, before accepting playback.
        for i in range(len(decoder.offsets)):decoder.block(i)
        # Keep the compact stream, not the temporary Havok object graph and
        # its expanded integer arrays alongside a second copy of the stream.
        binding={k:binding[k]for k in('blendHint','transformTrackToBoneIndices','originalSkeletonName')}
        mapping=None
        if source!=race and (race,source)not in((301,101),(1201,1101)):
            from animation_mapping import MotionLines,BodyMappingPlan
            if not hasattr(self,'motion_lines'):
                self.motion_lines=MotionLines(self.pack.read('chara/xls/animation/MotionLineTable.mlt'))
            mapping=BodyMappingPlan(source_raw,target_raw,binding,self.motion_lines.flags(name))
        automatic=None;face_notice=None
        if any(e['tag']=='C010'for e in embedded.events):
            from emote_faces import character_faces,FaceRules
            try:
                if not hasattr(self,'face_rules'):self.face_rules=FaceRules(self.pack)
                automatic=character_faces(self.character,embedded,name,outer.face_library,self.face_rules,path)
                if automatic and automatic.notices:face_notice='；'.join(automatic.notices)
            except (NotImplementedError,FileNotFoundError)as exc:face_notice=str(exc)
        pending=embedded.pending(name,resolved=automatic.event_ids if automatic else (),omit_visuals=True)
        omitted=tuple({'scope':scope,**e}for scope,graph in(('outer',outer),('embedded',embedded))for e in graph.omitted_visuals())
        result={'row':row,'decoder':decoder,'binding':binding,'source':source,'target':race,'source_raw':source_raw,
            'embedded':embedded,'face_library':outer.face_library,'pending_rules':pending,
            'automatic_faces':automatic,'face_notice':face_notice,
            'omitted_visuals':omitted,
            'standalone_preview':True,'outer_skipped':outer_skipped,
            'requires_weapon':needs_weapon,'weapon_motion':motion,
            'mapping':mapping,
            'weapon_plan_cache':OrderedDict(),
            'target_raw':target_raw,'bones':[b['name']for b in target_skel['bones']],
            'duration':min(duration,decoder.duration),'source_path':path,'source_sha256':hashlib.sha256(raw).hexdigest()}
        self.clips[identifier]=result
        self.trim_clip_budget()
        return result

    def trim_clip_budget(self):
        def size(v):return len(v['decoder'].raw)+(v['automatic_faces'].nbytes if v['automatic_faces']else 0)+sum(p.nbytes for p in v.get('weapon_plan_cache',{}).values())
        while len(self.clips)>4 or sum(size(v)for v in self.clips.values())>32*1024*1024:self.clips.popitem(last=False)

    def weapon_controls(self,clip,base):
        models=getattr(base,'weapon_models',())
        if not clip or not models:return None
        from emote_weapon_controls import WeaponControls,commands
        if not commands(clip['embedded']):return None
        key=tuple((v['slot'],v['part'],v['model_set'],v['body_id'])for v in models)
        plans=clip['weapon_plan_cache']
        if key not in plans:plans[key]=WeaponControls(self.pack,clip,models)
        plans.move_to_end(key)
        while len(plans)>2 or sum(v.nbytes for v in plans.values())>8*1024*1024:plans.popitem(last=False)
        self.trim_clip_budget()
        return plans[key]

    def expression(self,identifier):
        if identifier is None:return None
        if identifier not in self.expressions:
            row=self.by_id.get(identifier)
            if not row or not row['facial']:raise ValueError('面部表情编号不正确')
            from facial_animation import load
            self.expressions[identifier]={**load(self.character,row['key']),'id':identifier,'name':row['name']}
            while len(self.expressions)>16:self.expressions.popitem(last=False)
        self.expressions.move_to_end(identifier);return self.expressions[identifier]

    def ending(self,identifier):
        row=self.by_id.get(identifier)
        if not row or not row['persistent']or not row['end_key']or not row['end_id']:
            raise NotImplementedError('此动作没有独立的原始结束段，可直接恢复站姿')
        return self.clip(identifier,key=row['end_key'])

    def apply(self,base,choice,expression=None):
        ending=bool(choice and choice.get('phase')=='end')
        clip=(self.ending(choice['id'])if ending else self.clip(choice['id']))if choice else None
        time=min(choice['time'],clip['duration'])if choice else 0.
        duration=clip['duration']if clip else 0.;loop_start=None;stage='end'if ending else'single';present_time=time
        if clip and clip['row']['persistent']and not ending:
            row=clip['row'];start=None
            if row['start_key']:
                try:start=self.clip(choice['id'],key=row['start_key'])
                except (NotImplementedError,FileNotFoundError):pass
            loop_start=start['duration']if start else 0.
            duration=loop_start+clip['duration'];present_time=min(choice['time'],duration)
            if start and choice['time']<loop_start:
                clip=start;time=choice['time'];stage='start'
            else:
                time=(choice['time']-loop_start)%clip['duration'];stage='loop'
        face=self.expression(expression);signature=(id(base),choice['id']if choice else None,time,expression,stage,clip['source_path']if clip else None)
        if self.current is not None and self.current[0]==signature:return self.current[1],False
        automatic=clip.get('automatic_faces')if clip else None
        auto_locals=automatic.sample(time)if automatic and face is None else {}
        ref=self.reference;names=ref['skeleton_names'].tolist();parents=ref['skeleton_parents'];count=self.body_count
        outfit=self.character.outfit;params=self.character.base.pose['preset']['parameters']
        old=base.skinning@outfit.bind
        if clip:
            sample=clip['decoder'].sample(time);local=self.posed['skeleton_face_local'].copy();local[:count]=ref['skeleton_local'][:count]
            binding=clip['binding']
            if (clip['target'],clip['source'])==(301,101):
                local,_=local_shared_pose(clip['target_raw'],clip['source_raw'],binding,sample,local,names,target_race=301,source_race=101)
            elif (clip['target'],clip['source'])==(1201,1101):
                mapped_binding,_=identity_shared_binding(clip['target_raw'],clip['source_raw'],binding,target_race=1201,source_race=1101)
                local=normal_local_pose(local,clip['bones'],names,mapped_binding,sample)
            elif clip['source']!=clip['target']:
                local=clip['mapping'].apply(sample,local,names)
            else:
                local=normal_local_pose(local,clip['bones'],names,binding,sample)
            world,_=body_world(world_pose(local,parents),parents,names,self.character.base.pose['customization']['body'],params['race'])
        else:world=old.copy()
        for i in range(count,len(names)):
            p=int(parents[i]);relative=(face['locals'][i]if face and i in face['locals']else auto_locals[i]if i in auto_locals
                else np.linalg.solve(old[p],old[i])if p>=0 else old[i])
            world[i]=world[p]@relative if p>=0 else relative
        delta=world@np.linalg.inv(old);meshes=[]
        controls=self.weapon_controls(clip,base);weapon_states={};control_details=[]
        if controls:
            from weapon_attachment import attach_local
            for model in base.weapon_models:
                state,deltas,active,ids=controls.sample(model,time*30.)
                entry=self.character.weapons.attachments.entry(model['model_set'],model['slot_index'],state=state)
                matrix=world[outfit.names[entry['bone']]]@attach_local(entry)
                baseline=old[outfit.names[model['attachment']['bone']]]@attach_local(model['attachment'])
                weapon_states[model['slot'],model['part']]={'entry':entry,'matrix':matrix,'baseline':baseline,
                    'deltas':deltas,'active':active,'model':model}
                control_details.append({'slot':model['slot'],'part':model['part'],'state':state,'bone':entry['bone'],
                    'C174':ids,'C031':active.source if active else None})
        for mesh in base.meshes:
            joints=mesh.get('joint_matrices')
            if joints is None:joints=base.skinning[np.asarray(mesh['palette'])]
            anchor=mesh.get('weapon_attachment_bone')
            weapon=weapon_states.get((mesh['slot'],mesh.get('weapon_part')))
            if anchor and weapon:
                if weapon['active']:
                    locals_=np.asarray([weapon['deltas'][name]for name in mesh['weapon_bone_names']])
                else:locals_=mesh.get('weapon_local_matrices',np.repeat(np.eye(4)[None],len(mesh['palette']),axis=0))
                joints=weapon['matrix']@locals_
                moves=None
            elif anchor:moves=delta[outfit.names[anchor]]
            else:
                palette=np.asarray(mesh['palette']);owners=[]
                if np.any(palette>=len(names)):
                    item=mesh.get('attachment_item')or outfit.item_metadata(mesh['item_id'],mesh['slot']);attached=outfit.attachment(item)
                    reverse={v:k for k,v in attached['names'].items()}
                    for index in palette:
                        name=reverse[int(index)]
                        while name not in outfit.names:
                            name=attached['parents'][name]
                            if name is None:raise ValueError('动作装备骨骼未连接角色')
                        owners.append(outfit.names[name])
                    moves=delta[owners]
                else:moves=delta[palette]
            if moves is not None:joints=moves@joints
            joints.setflags(write=False);value={**mesh,'joint_matrices':joints}
            if anchor and weapon:
                # Export must follow the sampled grip/holster, not the old one.
                value['weapon_attachment_bone']=weapon['entry']['bone']
            # GPU skins all meshes. CPU positions only serve transparent
            # sorting; opaque bind geometry and buffers remain immutable.
            if mesh.get('transparent'):
                v=mesh['vertices'];points=np.column_stack((v['position'],np.ones(len(v))));posed=np.zeros((len(v),3))
                for i in range(v['weights'].shape[1]):
                    posed+=np.einsum('nij,nj->ni',joints[v['joints'][:,i]],points)[:,:3]*(v['weights'][:,i,None]/255.)
                value['posed_positions']=posed
            meshes.append(value)
        result=self.current[1]if self.current is not None and self.current[2]is base else copy.copy(base)
        result.meshes=meshes;result.skinning=delta@base.skinning;result.skinning.setflags(write=False);result.emote_base_scene=base
        if getattr(base,'visor_bones',None):
            parents=outfit.parents;result.visor_bones={}
            if any(name not in outfit.names for name in base.visor_bones):
                head=next(m for m in base.meshes if m['slot']=='Head')
                parents=outfit.attachment(outfit.item_metadata(head['item_id'],'Head'))['parents']
            for name,matrix in base.visor_bones.items():
                ancestor=name
                while ancestor not in outfit.names:
                    ancestor=parents.get(ancestor)
                    if ancestor is None:raise ValueError('动作头饰骨骼未连接角色')
                result.visor_bones[name]=delta[outfit.names[ancestor]]@matrix
        result.effect_frame=None
        result.simple_histories=getattr(result,'simple_histories',{})if self.current is not None and self.current[2]is base else{}
        result.weapon_effect_inputs=[]
        for v in getattr(base,'weapon_effect_inputs',()):
            weapon=weapon_states.get((v.slot,int(v.part)))
            if weapon:
                if weapon['active']:
                    from weapon_effects import posed_bones
                    active=weapon['active'];bones=posed_bones(active.skeleton,active.extra,weapon['deltas'])
                    bones=tuple((n,weapon['matrix']@m)for n,m in bones)
                else:
                    move=weapon['matrix']@np.linalg.inv(weapon['baseline']);bones=tuple((n,move@m)for n,m in v.bones)
                result.weapon_effect_inputs.append(replace(v,bones=bones,attachment_bone=weapon['entry']['bone']))
            else:result.weapon_effect_inputs.append(replace(v,bones=tuple((n,delta[outfit.names[v.attachment_bone]]@m)for n,m in v.bones))if v.attachment_bone else v)
        if controls:
            result.weapon_models=[{**v,'attachment':weapon_states[v['slot'],v['part']]['entry'],
                'local_pose':weapon_states[v['slot'],v['part']]['active'].source if weapon_states[v['slot'],v['part']]['active']else v['local_pose']}for v in base.weapon_models]
        result.emote={'id':choice['id'],'name':clip['row']['name'],'time':present_time,'duration':duration,
            'loop_start':loop_start,'stage':stage,'phase':'end'if ending else'play',
            'end_available':bool(clip['row']['persistent']and clip['row']['end_key']and clip['row']['end_id']),
            'end_emote_id':clip['row']['end_id']if ending else None,
            'pending_rules':tuple(sorted(set(clip['pending_rules'])|({'manual-face-priority'}if automatic and face else set()))),
            'omitted_visuals':tuple(sorted({e['tag']for e in clip['omitted_visuals']if not(controls and e['scope']=='embedded'and e['id']in controls.resolved_ids)})),
            'standalone_preview':clip.get('standalone_preview',False),
            'requires_weapon':clip.get('requires_weapon',False),
            'weapon_controls':control_details,'weapon_control_notices':controls.notices if controls else[],
            'automatic_faces':{'enabled':bool(automatic and face is None),'events':len(automatic.cues)if automatic else 0,
                'notice':clip.get('face_notice'),'clock':'authored-seconds; deterministic seek'},
            'source_path':clip['source_path'],'scope':'single-body-animation; authored facial cues; optional manual facial overlay'}if clip else None
        result.expression={k:v for k,v in face.items()if k!='locals'}if face else None
        self.current=(signature,result,base);return result,True
