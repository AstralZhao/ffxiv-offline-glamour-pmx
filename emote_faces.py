"""Bounded authored facial cues, evaluated independently of draw count.

Only proved ordinary cfxf additive sources are admitted here. Lip channels,
context overrides and manual-expression priorities remain separate.
"""
import math
import numpy as np
from ff14_readonly import Sheet
from animation_mapping import MotionLines
from facial_animation import static_plan,curve_plan,combine_layers


def smooth(progress):
    p=np.float32(min(1.,max(0.,progress)))
    return float(np.float32(np.float32(3.)-np.float32(p+p))*np.float32(p*p))


def backend_ease(age,span):
    if not span:return 1.
    seconds=np.float32(age/30.);duration=np.float32(span/30.)
    return smooth(float(np.float32(seconds*np.float32(1./duration))))


class FaceRules:
    def __init__(self,pack):
        self.lines=MotionLines(pack.read('chara/xls/animation/MotionLineTable.mlt'))
        motion=Sheet(pack,'MotionTimeline');blend=Sheet(pack,'MotionTimelineBlendTable')
        if(motion.header.column_hash,motion.header.fixed_size)!=('12260494',8):
            raise NotImplementedError('配套表情动作元数据布局尚未核对')
        if(blend.header.column_hash,blend.header.fixed_size,blend.header.columns)!=('69213275',8,tuple((3,i)for i in range(6))):
            raise NotImplementedError('配套表情过渡表布局尚未核对')
        self.motion={r.value(0):bytes(r.data[:8])for r in motion.rows()}
        active=[tuple(r.value(i)for i in range(6))for r in blend.rows()if any(r.value(i)for i in range(6))]
        if not active or active[0][:2]!=(0,0):raise ValueError('配套表情过渡表缺少默认项')
        self.blends={(r[1],r[0]):r[2:]for r in active}

    def transition(self,left,right):
        return (self.blends.get((left,right))or self.blends.get((0,right))or
            self.blends.get((left,0))or self.blends[(0,0)])[0]

    def check(self,name,*,continuous=False,channel=17):
        row=self.motion.get(name)
        if channel==19:
            if(not name.startswith('cfxl_')or self.lines.flags(name)!=0x229 or
                row!=bytes((0,0,0,0,35,0,0,0))):
                raise NotImplementedError('此配套口型的动作通道尚未接入')
            return row
        if(not name.startswith('cfxf_')or self.lines.flags(name)!=0x1eb or row is None or
            (row!=bytes(8)if continuous else row[4]not in(0,21)or row[7]not in(6,8,10,12,14))):
            raise NotImplementedError('此配套表情的动作通道尚未接入')
        return row


class EmbeddedFaces:
    """Immutable small cue plan; seeking never retains a sequence of poses.

    The preview uses authored absolute time and the proved native transition
    and backend ease curves. It does not reconstruct a game's varying frame
    request history or claim identical transition-boundary pixels.
    """
    def __init__(self,character,graph,body_name,library,rules,primary_path,*,continuous=False,channel=17,_events=None,_preview=False,_plans=None):
        events=[e for e in graph.events if e['tag']=='C010']if _events is None else _events
        if not events:raise NotImplementedError('此动作没有配套表情')
        if(len(events)>64 or not _preview and(len(graph.actors)!=1 or graph.face_library is not None or
            'C009'in graph.pending(body_name))):
            raise NotImplementedError('此配套表情的上下文或身体时间线尚未接入')
        cues=[];plans={}if _plans is None else _plans;previous=None
        for e in sorted(events,key=lambda e:(e['start_frame'],e['id'])):
            dynamic=channel==19 or e['flags']==0 or e['sample_end']!=1.
            invalid=(e['actor_start']or e['track_start']or e['track_lua']or e['sample_start']!=0. or e['duration_frames']<=0)
            if channel==19:
                invalid=invalid or not continuous or e['flags']not in(0,1)or not 0<e['sample_end']<=65535 or int(e['sample_end'])!=e['sample_end']
            elif dynamic:
                invalid=invalid or not continuous or e['flags']not in(0,1)or not 0<e['sample_end']<=65535 or int(e['sample_end'])!=e['sample_end']
            else:invalid=invalid or e['flags']!=1 or e['sample_end']!=1.
            if invalid:
                raise NotImplementedError('此配套表情的采样或条件时间线尚未接入')
            start=e['start_frame'];end=start+e['duration_frames'];name=e['name']
            metadata=rules.check(name,continuous=dynamic,channel=channel)
            category=metadata[4]
            from_neutral=previous is None
            if previous is None:
                transition=rules.transition(0,category)
            else:
                # New ordinary requests replace a still-retained stopping
                # leaf without another entrance. Once it has retired, enter
                # from neutral. The exact retirement boundary stays guarded.
                if not _preview and(start<=previous['start']or start<previous['start']+previous['transition']):
                    raise NotImplementedError('此配套表情的中断或间隔调度尚未接入')
                if start>previous['end']:
                    if not _preview and start==previous['end']+previous['stop']:
                        raise NotImplementedError('此配套表情的中断或间隔调度尚未接入')
                    from_neutral=start>=previous['end']+previous['stop']if _preview else start>previous['end']+previous['stop']
                    transition=rules.transition(0,category)if from_neutral else 0
                else:
                    transition=previous['end']-start
                    if not _preview and end<previous['end']:raise NotImplementedError('此配套表情的交叠退出尚未接入')
                    # Native packet0x1000 (MotionTimeline byte7 bit3) suppresses
                    # crossfade only while the old same-name source exists.
                    if metadata[7]&8 and previous['name']==name:transition=0
            if name not in plans:
                options={'channel':channel}if channel==19 else{}
                plan=(curve_plan if dynamic else static_plan)(character,name,library=library,primary_paths=(primary_path,),**options)
                if not dynamic and not plan.constant:raise NotImplementedError('此配套表情包含连续面部轨道')
                if not _preview and(plan.metadata['source_pending_rules']or plan.metadata['source_face_library']is not None):
                    raise NotImplementedError('此配套表情来源还包含其他时间线控制')
                plans[name]=plan
            # Whole ordinary C00917/19 requests reset the constructed backend
            # with source28b1428 through1ff220, including new handover controls
            # and repeated source names. Keep the authored tree transition.
            extra={'backend_transition':0}
            if dynamic:
                frames=plans[name].metadata['frames']-1
                if(e['sample_end']>frames or not(e['flags']&1)and(e['duration_frames']!=frames or e['sample_end']!=frames)):
                    raise NotImplementedError('此连续表情的来源范围或寿命尚未接入')
                rate=float(np.float32(np.float32(e['sample_end'])/np.float32(e['duration_frames'])))if e['flags']&1 else 1.
                crop=np.float32(np.float32(frames-e['sample_end'])/np.float32(30.))
                limit=float(np.float32(np.float32(plans[name].metadata['duration_seconds'])-crop))
                extra.update(sample_rate=rate,sample_limit=limit)
            cues.append({'id':e['id'],'start':start,'end':end,'transition':transition,'plan':plans[name],
                'name':name,'stop':rules.transition(2,category),'continuous':dynamic,'from_neutral':from_neutral,
                'source_order':e.get('_source_order',e['id']),**extra})
            previous=cues[-1]
        self.cues=tuple(cues);self.stop=rules.transition(2,21)
        self.nbytes=sum(p.nbytes for p in plans.values())+len(cues)*64
        if self.nbytes>4*1024*1024:raise NotImplementedError('此动作的配套表情计划过大')
        self.event_ids=frozenset(e['id']for e in events)

    def layers(self,seconds,*,_ordered=False):
        if not math.isfinite(seconds)or seconds<0:raise ValueError('配套表情时间不正确')
        frame=seconds*30.;current=None
        for i,cue in enumerate(self.cues):
            if cue['start']<=frame:current=i
            else:break
        if current is None:return ()
        cue=self.cues[current];age=frame-cue['start'];span=cue['transition']
        weight=smooth(age/span)if span else 1.
        stop=cue.get('stop',self.stop)
        fade=1.-smooth((frame-cue['end'])/stop)if frame>=cue['end']and stop else float(frame<cue['end'])
        layers=[];order=[]
        def append(value,weight):
            elapsed=()
            if value.get('continuous'):
                source_time=(frame-value['start'])/30.*value.get('sample_rate',1.)
                elapsed=(min(source_time,value.get('sample_limit',math.inf)),)
            layers.append((value['plan'],weight,*elapsed))
            order.append((value['start'],value.get('source_order',value.get('id',0))))
        if current and weight<1. and not cue.get('from_neutral',False):
            old=self.cues[current-1];old_age=frame-old['start']
            ease=backend_ease(old_age,old.get('backend_transition',old['transition']))
            append(old,(1.-weight)*ease*fade)
        # Control ease is separate from tree weight. Its reached native
        # configuration may reset it to zero duration after construction.
        append(cue,weight*backend_ease(age,cue.get('backend_transition',span))*fade)
        result=tuple((key,(v[0],float(np.clip(v[1],0.,1.)),*v[2:]))for key,v in zip(order,layers)if v[1]>0.)
        return result if _ordered else tuple(v for _,v in result)

    def sample(self,seconds):return combine_layers(self.layers(seconds))


class CombinedFaces:
    """Proved face-before-lips case; preserve native creation order and gates."""
    def __init__(self,face,lips):
        self.face=face;self.lips=lips
        self.cues=tuple(sorted((*face.cues,*lips.cues),key=lambda v:(v['start'],v['id'])))
        self.event_ids=face.event_ids|lips.event_ids;self.nbytes=face.nbytes+lips.nbytes
        if len(self.cues)>64 or self.nbytes>4*1024*1024:raise NotImplementedError('此动作的配套表情计划过大')

    def layers(self,seconds):return self.face.layers(seconds)+self.lips.layers(seconds)
    def sample(self,seconds):return combine_layers(self.layers(seconds))


def embedded_faces(character,graph,body_name,library,rules,primary_path,*,continuous=False):
    events=[e for e in graph.events if e['tag']=='C010']
    lips=[e for e in events if e['name'].startswith('cfxl_')]
    if not lips:return EmbeddedFaces(character,graph,body_name,library,rules,primary_path,continuous=continuous)
    faces=[e for e in events if e not in lips]
    if faces:
        # Do not treat arbitrary channels as independent. This admitted case
        # has one earlier static face which permits lip requests and survives
        # every lip exit; it needs no unproved cross-channel stop/reentry.
        if(len(faces)!=1 or faces[0]['flags']!=1 or faces[0]['start_frame']>=min(e['start_frame']for e in lips)
            or not rules.check(faces[0]['name'])[7]&4
            or faces[0]['start_frame']+faces[0]['duration_frames']<max(e['start_frame']+e['duration_frames']for e in lips)+rules.transition(2,35)):
            raise NotImplementedError('此整脸与口型通道的交接尚未接入')
    lip=EmbeddedFaces(character,graph,body_name,library,rules,primary_path,continuous=continuous,channel=19,_events=lips)
    if not faces:return lip
    face=EmbeddedFaces(character,graph,body_name,library,rules,primary_path,_events=faces)
    return CombinedFaces(face,lip)


class CharacterFaces:
    """Standalone preview of readable actor facial tracks, without scene gates."""
    def __init__(self,groups,notices=()):
        self.groups=tuple(groups);self.notices=tuple(notices)
        self.cues=tuple(c for g in groups for c in g.cues)
        self.event_ids=frozenset(i for g in groups for i in g.event_ids)
        plans={id(c['plan']):c['plan']for c in self.cues}
        self.nbytes=sum(p.nbytes for p in plans.values())+len(self.cues)*64
        if len(self.cues)>64 or self.nbytes>4*1024*1024:raise NotImplementedError('此动作的配套表情计划过大')

    def layers(self,seconds):
        if len(self.groups)==1:return self.groups[0].layers(seconds)
        layers=[v for g in self.groups for v in g.layers(seconds,_ordered=True)]
        return tuple(v for _,v in sorted(layers,key=lambda v:v[0]))

    def sample(self,seconds):return combine_layers(self.layers(seconds))


def character_faces(character,graph,body_name,library,rules,primary_path):
    """Use the established plan where possible; isolate scene-only blockers.

    Resource, skeleton, partial and buffer checks stay in the source compiler.
    A missing facial cue does not discard other readable facial tracks.
    """
    try:return CharacterFaces((embedded_faces(character,graph,body_name,library,rules,primary_path,continuous=True),))
    except (NotImplementedError,FileNotFoundError):pass
    body=next((e for e in graph.events if e['tag']=='C009'and e['name']==body_name),None)
    actor=body['actor_id']if body else graph.actors[0]
    origin=sum(body[k]for k in('actor_start','track_start','start_frame'))if body else 0
    events=[]
    for order,e in enumerate(graph.events):
        if e['tag']!='C010'or e['actor_id']!=actor or not e['name'].startswith(('cfxf_','cfxl_')):continue
        events.append({**e,'start_frame':max(0,sum(e[k]for k in('actor_start','track_start','start_frame'))-origin),
            'actor_start':0,'track_start':0,'track_lua':0,'flags':e['flags']&1,'_source_order':order})
    groups=[];notices=[]
    for channel,prefix in((17,'cfxf_'),(19,'cfxl_')):
        # At a shared timestamp the last authored request in this channel is
        # the selected standalone preview. Scene conditions are not executed.
        selected={e['start_frame']:e for e in events if e['name'].startswith(prefix)}
        selected=list(selected.values())
        if not selected:continue
        options=dict(continuous=True,channel=channel,_preview=True)
        context=graph.face_library or library
        try:
            groups.append(EmbeddedFaces(character,graph,body_name,context,rules,primary_path,_events=selected,**options))
        except (NotImplementedError,FileNotFoundError):
            plans={}
            for e in selected:
                try:groups.append(EmbeddedFaces(character,graph,body_name,context,rules,primary_path,_events=[e],_plans=plans,**options))
                except (NotImplementedError,FileNotFoundError)as exc:notices.append(f"{e['name']}：{exc}")
    if not groups:
        if notices:raise NotImplementedError('；'.join(dict.fromkeys(notices)))
        return None
    return CharacterFaces(groups,notices)
