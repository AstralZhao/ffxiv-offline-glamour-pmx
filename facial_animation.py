"""Selected-face additive tracks; source mapping and bounded curve plans."""
import hashlib,json,math,struct
from pathlib import Path
import numpy as np
from havok_probe import TagReader,read_skeleton
from spline_animation import SplineAnimation
from animation_blend import additive
from facial_parameters import name_hash
from geometry_probe import local_matrix


def load(character,key):
    from emote_preview import timeline
    from emote_timeline import TimelineGraph
    if not key.startswith('facial/pose/'):raise NotImplementedError('当前先支持定格面部表情')
    tmb=character.base.pack.read('chara/action/'+key+'.tmb');name,_=timeline(tmb,facial=True)
    return load_declared(character,name,library=TimelineGraph(tmb).face_library)


def load_declared(character,name,*,library=None,primary_paths=()):
    plan=static_plan(character,name,library=library,primary_paths=primary_paths)
    return {**plan.metadata,'locals':combine_static(((plan,1.),))}


class FacialPlan:
    """Compact source TRS and actual partial indices; no geometry/Havok graph."""
    __slots__=('metadata','base','sample','indices','targets','constant','nbytes')

    def __init__(self,metadata,base,sample,indices,targets,constant):
        self.metadata=dict(metadata);self.base=base;self.sample=sample
        self.indices=np.asarray(indices,dtype=np.int32);self.targets=tuple(targets)
        self.constant=bool(constant)
        for value in(self.base,self.sample,self.indices):value.setflags(write=False)
        self.nbytes=sum(v.nbytes for v in(self.base,self.sample,self.indices))+len(self.targets)*8


def sample_payload(sample):
    result=np.zeros((len(sample['translation']),3,4))
    result[:,0,:3]=sample['translation'];result[:,1]=sample['rotation']
    result[:,2,:3]=sample['scale'];result[:,2,3]=1
    return result


class FacialCurvePlan(FacialPlan):
    """One finite source control; immutable compressed bytes, two decoded blocks."""
    __slots__=('decoder','duration')

    def __init__(self,*args,decoder,block_budget):
        super().__init__(*args);self.decoder=decoder;self.duration=decoder.duration
        self.nbytes+=len(decoder.raw)+block_budget

    def at(self,seconds):
        if not math.isfinite(seconds)or seconds<0:raise ValueError('连续表情时间不正确')
        # Original ordinary max-cycles-one control retains the final sample
        # after its source endpoint, including during the exit fade.
        return sample_payload(self.decoder.sample(min(float(seconds),self.duration)))


def combine_layers(layers):
    """Apply source additive layers in control insertion order at given weights.

    Event scheduling and final controller weights are supplied separately.
    This function does not infer them from an event's age or duration.
    """
    layers=tuple(layers)
    if not layers:return {}
    if len(layers)>64:raise ValueError('面部表情叠加层过多')
    first=layers[0][0]
    if not isinstance(first,FacialPlan):raise ValueError('面部表情计划不正确')
    result=first.base;targets={}
    for layer in layers:
        if len(layer)not in(2,3):raise ValueError('面部表情叠加参数不正确')
        plan,weight=layer[:2]
        if(not isinstance(plan,FacialPlan)or plan.metadata['face_path']!=first.metadata['face_path']
            or not np.array_equal(plan.base,first.base)):
            raise ValueError('面部表情来源骨架不一致')
        if len(layer)==3:
            if not isinstance(plan,FacialCurvePlan):raise ValueError('面部表情不是连续来源')
            sample=plan.at(layer[2])
        else:
            if isinstance(plan,FacialCurvePlan):raise ValueError('连续表情缺少采样时间')
            sample=plan.sample
        result=additive(result,sample,plan.indices,hint=1,weight=weight)
        for source,target in plan.targets:
            if target in targets and targets[target]!=source:raise ValueError('面部表情目标骨骼冲突')
            targets[target]=source
    return {target:local_matrix({'translation':result[i,0,:3],'rotation':result[i,1],'scale':result[i,2,:3]})
        for target,i in targets.items()}


combine_static=combine_layers


def static_plan(character,name,*,library=None,primary_paths=()):
    """Compile a static face delta from its declared resource context.

    Automatic scheduling/overlap weights are deliberately separate from this
    source and skeleton compiler; callers cannot infer an event's weight here.
    """
    return _compile(character,name,library=library,primary_paths=primary_paths,continuous=False)


def curve_plan(character,name,*,library=None,primary_paths=(),channel=17):
    return _compile(character,name,library=library,primary_paths=primary_paths,continuous=True,channel=channel)


def _compile(character,name,*,library,primary_paths,continuous,channel=17):
    from animation_sources import facial_clip
    pose=character.base.pose;pack=character.base.pack
    records=[r for r in pose['skeletons']if r.get('est_table')=='faceskeletontemplate']
    if len(records)!=1:raise ValueError('面部表情骨架不唯一')
    face=records[0];raw=pack.read(face['path'])
    if hashlib.sha256(raw).hexdigest()!=face['sha256']:raise ValueError('表情骨架来源已改变')
    skeleton=read_skeleton(raw);parts=face['path'].split('/');rig,variant=parts[2],parts[5]
    raw,header,selected,source=facial_clip(pack,name,rig,variant,library=library,primary_paths=primary_paths,channel=channel)
    path=source['path'];name=selected['name']
    from emote_timeline import pap_timeline
    source_timeline=pap_timeline(raw,header,name)
    containers=[o for o in TagReader(raw[header['havok_offset']:header['footer_offset']]).read()if o['__type__']=='hkaAnimationContainer']
    if len(containers)!=1:raise ValueError('表情动画容器不唯一')
    container=containers[0];index=selected['binding_index']
    if not 0<=index<len(container['bindings'])or index>=len(container['animations']):raise ValueError('表情动画引用越界')
    binding=container['bindings'][index];animation=binding['animation']
    if animation is not container['animations'][index]or binding['blendHint']!=1:raise NotImplementedError('此表情的混合类型尚未接入')
    if animation.get('extractedMotion')or any(t.get('annotations')for t in animation.get('annotationTracks',())):
        raise NotImplementedError('此表情包含尚未接入的事件')
    decoder=SplineAnimation(animation)
    if not continuous and(decoder.duration>.034 or decoder.frames!=2):raise NotImplementedError('此表情的连续轨道尚未接入')
    if continuous:
        length=int(np.ceil(np.float32(np.float32(decoder.duration)*np.float32(30.))))
        events=source_timeline.events
        if(source_timeline.face_library is not None or source_timeline.pending(name)or len(events)!=1 or
            source_timeline.root['start_frame']or source_timeline.root['length_frames']!=length or
            events[0]['duration_frames']!=length or decoder.frames!=length+1):
            raise NotImplementedError('此连续表情的来源时间控制尚未接入')
    sample=decoder.sample(0.);tracks=binding['transformTrackToBoneIndices'];bones=skeleton['bones']
    if len(tracks)!=decoder.tracks or len(set(tracks))!=len(tracks)or any(not 0<=i<len(bones)for i in tracks):raise ValueError('表情骨骼轨道越界或重复')
    assembly=json.loads(Path(pose['source_assembly']['path']).read_bytes())
    partials=[r for r in assembly['source_skeleton_map']['partials']if r['path']==face['path']]
    if len(partials)!=1:raise ValueError('表情partial映射不唯一')
    partial=partials[0]['bones']
    if len(partial)!=len(bones):raise ValueError('表情partial大小不一致')
    mapped=[partial[i]['merged_bone_index']for i in tracks]
    inactive=set()
    for i,target in zip(tracks,mapped):
        identity=partial[i]
        if identity['source_bone_index']!=i or identity['bone_name']!=bones[i]['name']or identity['source_sha256']!=face['sha256']:
            raise ValueError('表情骨骼来源映射不一致')
        if not bones[i]['name'].startswith('j_f_')and bones[i]['name']!='j_ago':
            track=tracks.index(i)
            matrix=local_matrix({k:sample[k][track]for k in('translation','rotation','scale')})
            if not np.array_equal(matrix,np.eye(4)):raise NotImplementedError('表情轨道覆盖身体锚点')
            inactive.add(i)
    jobs=pose['customization']['skeletal_face']['selection']['jobs']
    if {job['bone_hash']for job in jobs}&{name_hash(bones[i]['name'])for i in tracks}:
        raise NotImplementedError('此表情与捏脸修正存在重叠，合成优先级尚未核对')
    base=np.asarray([[b['translation']+[0],b['rotation'],b['scale']+[1]]for b in bones],dtype=float)
    payload=sample_payload(sample)
    # An authored two-frame source can still move. Manual pose selection uses
    # frame0; automatic static scheduling must check constant before admitting it.
    # Check every decoded control, not a few sampled times. Equal endpoints
    # do not prove a cubic spline constant between them. Conservative rejection
    # of non-identical controls keeps continuous sources out of this path.
    constant=True;block_sizes=[]
    first_channels=None
    for block in range(len(decoder.offsets)):
        channels=decoder.block(block)
        block_sizes.append(sum(np.asarray(c[2]).nbytes+(np.asarray(c[1]).nbytes if c[1]is not None else 0)
            for track in channels for c in track)+len(channels)*3*256)
        if first_channels is None:
            first_channels=[[np.asarray(c[2] if c[1] is None else c[2][0]) for c in track] for track in channels]
        for index,(track, reference) in enumerate(zip(channels,first_channels)):
            for decoded,value in zip(track,reference):
                controls=np.asarray(decoded[2])
                if not np.array_equal(controls,np.broadcast_to(value,controls.shape)):
                    constant=False
                    if continuous and tracks[index]in inactive:
                        raise NotImplementedError('连续表情轨道覆盖身体锚点')
    metadata={**source,'path':path,'sha256':hashlib.sha256(raw).hexdigest(),'clip':name,
        'face_path':face['path'],'tracks':len(tracks),'blend_hint':1,'FPEB_disjoint':True,
        'duration_seconds':decoder.duration,'frames':decoder.frames,'channel':channel,
        'source_pending_rules':source_timeline.pending(name),'source_face_library':source_timeline.face_library}
    args=(metadata,base,payload,tracks,[(i,target)for i,target in zip(tracks,mapped)if i not in inactive],constant)
    return (FacialCurvePlan(*args,decoder=decoder,block_budget=sum(sorted(block_sizes)[-2:]))
        if continuous else FacialPlan(*args))
