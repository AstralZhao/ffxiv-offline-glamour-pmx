"""Authored C031 weapon-local animation and C174 attachment states.

Character-only preview explicitly includes retained ancillary command tracks;
it does not recreate the game's actor scheduling or create extra objects.
"""
import math,struct,hashlib,re
import numpy as np
from ff14_readonly import Sheet
from emote_timeline import TimelineGraph
from default_stance_probe import pap_header
from havok_probe import TagReader,read_skeleton
from spline_animation import SplineAnimation
from weapon_pose import bind_deltas


POSITION_STATES=(1,0,2,3,4,5)


def check_binding(name,model,root):
    # Original books retain the addressed weapon namespace in their PAP.
    # Accept that exact model identity, never a different weapon's namespace.
    if name not in(root,f'w{model:04}_0:mdl:{root}'):
        raise ValueError('武器动作骨架绑定身份不一致')


def commands(graph):
    result=[];owned={e['id']for e in graph.events}
    for e in graph.events:
        if e['tag']in('C031','C174'):
            result.append({**e,'start_frame':e['start_frame']+e['actor_start']+e['track_start']})
    # These retained tracks are specifically requested for standalone weapon
    # animation; never activate their unknown siblings or orphan animations.
    for i in graph.unused_visual_nodes:
        track=graph.nodes[i]
        if track['tag']!='TMTR':continue
        for child in track['children']:
            e=graph.nodes[child]
            if e['tag']in('C031','C174')and child not in owned:
                result.append({**e,'start_frame':e['start_frame']+track['start_frame']});owned.add(child)
    return sorted(result,key=lambda e:(e['start_frame'],e['id']))


def attachment_state(events,frame,target,original):
    state=original;resolved=[]
    for e in events:
        if e['tag']!='C174':continue
        p=e['payload']
        if len(p)!=32:raise ValueError('C174武器挂接事件长度不正确')
        duration,unknown,initial,kind,final,delay,reserved=struct.unpack_from('<7i',p,4)
        if kind!=target or frame<e['start_frame']:continue
        if unknown or reserved or duration<0 or delay or not 0<=initial<6 or not 0<=final<6:
            raise NotImplementedError('此武器挂接事件的附加参数尚未接入')
        if frame<e['start_frame']+duration:
            state=POSITION_STATES[initial]
        else:state=POSITION_STATES[final]
        resolved.append(e['id'])
    return state,resolved


class WeaponClip:
    def __init__(self,pack,model,body,event,keys,*,_cue=None):
        p=event['payload']
        if len(p)!=16:raise ValueError('C031武器动画事件长度不正确')
        first,unknown,identifier,target=struct.unpack_from('<iiHH',p,4)
        if first!=1 or unknown or target not in(0,1):raise NotImplementedError('此附属对象动画参数尚未接入')
        if identifier not in keys:raise ValueError('武器动画表引用不存在')
        self.key=keys[identifier];self.target=target;self.event=event
        if not re.fullmatch(r'weapon/[a-z0-9_/]+',self.key)or'..'in self.key.split('/'):
            raise ValueError('武器动画表路径不正确')
        timeline_path='chara/action/'+self.key+'.tmb'
        timeline=TimelineGraph(pack.read(timeline_path),character_only=True,retain_unlinked_animations=True)
        events=[e for e in timeline.events if e['tag']=='C010'and e['name'].startswith('cbbw_')]
        self.segments=[];self.decoder=None;self.nbytes=0
        self.source={'timeline':timeline_path,'event':event['id'],'provider':'no-skeletal-channel'}
        if not events:return
        if len(events)>8:raise NotImplementedError('独立武器动画片段数量超过预算')
        if len(events)>1 and _cue is None:
            self.segments=[WeaponClip(pack,model,body,event,keys,_cue=cue)for cue in sorted(events,key=lambda e:(e['start_frame'],e['id']))]
            self.nbytes=sum(v.nbytes for v in self.segments)
            return
        cue=_cue or events[0]
        if cue['sample_start']or cue['flags']not in(0,1)or cue['actor_start']or cue['track_start']or cue['duration_frames']<=0:
            raise NotImplementedError('此武器动画的时间控制尚未接入')
        self.cue=cue
        path=f'chara/weapon/w{model:04}/animation/a0001/wp_common/resident/weapon.pap'
        try:raw=pack.read(path)
        except FileNotFoundError:
            # Pinned AWT/source census distinguishes an originally static
            # weapon from missing downloaded resources of an animated one.
            from weapon_stance import profile,checked_read
            data=profile();record=data['weapons'].get(str(model),{})
            if record.get('mode')!='reference-no-resident-animation':raise
            checked_read(pack,data,'chara/xls/animation/animation_work_table-weapon.awt')
            self.source.update(provider='reference-no-resident-animation',requested_clip=cue['name'])
            return
        header=pap_header(raw)
        if header['model_type']!=3 or header['model_id']!=model:raise ValueError('武器动作原始模型身份不一致')
        clips=[c for c in header['clips']if c['name']==cue['name']and not c['is_face']]
        if not clips:
            self.source.update(provider='source-has-no-requested-clip',path=path,requested_clip=cue['name'])
            return
        if len(clips)!=1:raise ValueError('武器动作片段重复')
        containers=[v for v in TagReader(raw[header['havok_offset']:header['footer_offset']]).read()if v['__type__']=='hkaAnimationContainer']
        if len(containers)!=1:raise ValueError('武器动作动画容器不唯一')
        index=clips[0]['binding_index'];container=containers[0]
        if not 0<=index<len(container['bindings'])or index>=len(container['animations']):raise ValueError('武器动作绑定索引越界')
        binding=container['bindings'][index];animation=binding['animation']
        if animation is not container['animations'][index]or binding['blendHint']!=0:raise NotImplementedError('此武器动作混合方式尚未接入')
        self.skeleton={'bones':read_skeleton(pack.read(f'chara/weapon/w{model:04}/skeleton/base/b0001/skl_w{model:04}b0001.sklb'))['bones']}
        check_binding(binding['originalSkeletonName'],model,self.skeleton['bones'][0]['name'])
        self.decoder=SplineAnimation(animation)
        tracks=binding['transformTrackToBoneIndices']
        if len(tracks)!=self.decoder.tracks or len(set(tracks))!=len(tracks)or any(not 0<=i<len(self.skeleton['bones'])for i in tracks):raise ValueError('武器动画骨骼轨道越界或重复')
        block_sizes=[]
        for i in range(len(self.decoder.offsets)):
            block=self.decoder.block(i)
            block_sizes.append(sum(np.asarray(c[2]).nbytes+(np.asarray(c[1]).nbytes if c[1]is not None else 0)for track in block for c in track)+len(block)*768)
        self.binding={'blendHint':0,'transformTrackToBoneIndices':tuple(tracks)}
        self.extra=None
        from weapon_skeleton import extra_skeletons
        if(model,body)in extra_skeletons(pack.read('chara/xls/extraskl/extra_weapon.eslb')):
            self.extra={'bones':read_skeleton(pack.read(f'chara/weapon/w{model:04}/skeleton/parts/p{body:04}/skl_w{model:04}p{body:04}.sklb'))['bones']}
        self.nbytes=len(self.decoder.raw)+sum(sorted(block_sizes)[-2:])+len(self.skeleton['bones'])*512
        if self.nbytes>4*1024*1024:raise NotImplementedError('独立武器动画超过内存预算')
        self.source={'path':path,'sha256':hashlib.sha256(raw).hexdigest(),'clip':cue['name'],'timeline':timeline_path,'event':event['id']}
        selected={e['id']for e in events}
        self.source['omitted_channels']=sorted({e.get('name',e['tag'])for e in timeline.events if e['id']not in selected})

    def sample(self,frame):
        if self.segments:
            value=None
            for segment in self.segments:
                candidate=segment.sample(frame)
                if candidate is not None:
                    value=candidate;self.source=segment.source;self.skeleton=segment.skeleton;self.extra=segment.extra
            return value
        if self.decoder is None:return None
        age=frame-self.event['start_frame']-self.cue['start_frame']
        if age<0:return None
        rate=self.cue['sample_end']/self.cue['duration_frames']if self.cue['flags']&1 else 1.
        seconds=min(self.decoder.duration,age/30.*rate)
        return bind_deltas(self.skeleton,self.binding,self.decoder.sample(seconds),self.extra)


class WeaponControls:
    def __init__(self,pack,clip,models):
        self.events=commands(clip['embedded']);self.parts={};self.notices=[];self.nbytes=0;self.resolved_ids=set()
        supported=[];targets={v['slot_index']for v in models}
        for e in self.events:
            if e['tag']!='C174':supported.append(e);continue
            if len(e['payload'])!=32:raise ValueError('C174武器挂接事件长度不正确')
            duration,unknown,initial,target,final,delay,reserved=struct.unpack_from('<7i',e['payload'],4)
            if target in targets and not unknown and not reserved and duration>=0 and not delay and 0<=initial<6 and 0<=final<6:
                supported.append(e);self.resolved_ids.add(e['id'])
            else:self.notices.append('此C174附属对象或挂接参数尚未接入')
        self.events=supported
        animation_events=[e for e in self.events if e['tag']=='C031']
        keys={}
        if animation_events:
            from emote_resources import LAYOUTS
            sheet=Sheet(pack,'WeaponTimeline')
            if(sheet.header.column_hash,sheet.header.fixed_size)!=LAYOUTS['WeaponTimeline']:
                raise ValueError('武器动画数据表布局尚未核对')
            keys={r.id:r.value(0)for r in sheet.rows()}
            if any(len(e['payload'])!=16 for e in animation_events):raise ValueError('C031武器动画事件长度不正确')
        for model in models:
            target=model['slot_index'];records=[]
            for e in animation_events:
                if struct.unpack_from('<H',e['payload'],14)[0]!=target:continue
                try:
                    records.append(WeaponClip(pack,model['model_set'],model['body_id'],e,keys));self.resolved_ids.add(e['id'])
                except(NotImplementedError,FileNotFoundError)as exc:self.notices.append(str(exc))
            self.parts[model['slot'],model['part']]=records
            self.nbytes+=sum(v.nbytes for v in records)
        if self.nbytes>8*1024*1024:raise NotImplementedError('动作独立武器动画超过内存预算')

    def sample(self,model,frame):
        if not math.isfinite(frame)or frame<0:raise ValueError('武器动作时间不正确')
        state,ids=attachment_state(self.events,frame,model['slot_index'],model['attachment']['state'])
        active=None;deltas=None
        for candidate in self.parts.get((model['slot'],model['part']),()):
            values=candidate.sample(frame)
            if values is not None:active=candidate;deltas=values
        return state,deltas,active,ids
