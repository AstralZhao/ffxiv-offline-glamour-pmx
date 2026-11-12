"""Bounded source TMLB graph, including per-animation PAP timelines."""
import math,re,struct

# Audio is intentionally excluded from emote playback. Other unseen events
# remain explicit pending rules; they must not silently disappear.
AUDIO=frozenset(('C042','C053','C063','C143','C197'))
# Optional attachment/visual tracks have their own future integration point.
# They do not prevent the actor's body and authored facial tracks playing.
OPTIONAL_VISUALS=frozenset(('C012','C173','C031','C043','C094','C198'))
LABELS={'C010':'动作配套表情','C012':'动作特效','C173':'动作特效','C031':'附属模型动作',
    'C043':'附属武器','C094':'显隐变化','C198':'附属模型','manual-face-priority':'手动与配套表情组合'}


class TimelineGraph:
    def __init__(self,raw,*,character_only=False,retain_unlinked_animations=False):
        if not isinstance(raw,bytes)or not 12<=len(raw)<=4*1024*1024 or raw[:4]!=b'TMLB':raise ValueError('动作内嵌时间线格式不正确')
        length,count=struct.unpack_from('<II',raw,4)
        if length!=len(raw)or not 1<=count<=8192:raise ValueError('动作内嵌时间线长度或数量越界')
        entries=[];at=12
        for _ in range(count):
            if at+8>len(raw):raise ValueError('动作内嵌节点截断')
            tag,n=struct.unpack_from('<4sI',raw,at)
            if n<8 or at+n>len(raw):raise ValueError('动作内嵌节点长度越界')
            try:name=tag.decode('ascii')
            except UnicodeError:raise ValueError('动作内嵌节点类型不正确')from None
            if not re.fullmatch(r'TM[A-Z]{2}|C[0-9]{3}',name):raise ValueError('动作内嵌节点类型不正确')
            entries.append((name,at+8,raw[at+8:at+n]));at+=n
        self.nodes={};self.face_library=None;actors=None;root=None
        def links(base,payload,offset):
            if len(payload)<offset+8:raise ValueError('动作内嵌引用截断')
            relative,n=struct.unpack_from('<II',payload,offset)
            if n==0:return ()
            where=base+relative
            if n>count or not at<=where<=len(raw)-2*n:raise ValueError('动作内嵌引用越界')
            result=struct.unpack_from('<'+'H'*n,raw,where)
            if len(set(result))!=len(result):raise ValueError('动作内嵌引用重复')
            return result
        def string(base,relative,*,library=False):
            where=base+relative
            if not at<=where<len(raw):raise ValueError('动作内嵌片段名越界')
            end=raw.find(b'\0',where,min(len(raw),where+193))
            if end<=where:raise ValueError('动作内嵌片段名缺少结束符')
            value=raw[where:end].decode('ascii')
            pattern='[a-z0-9_]+(?:/[a-z0-9_]+)*'if library else'[a-z0-9_]+'
            if not re.fullmatch(pattern,value):raise ValueError('动作内嵌片段名不正确')
            return value
        for tag,base,payload in entries:
            if tag=='TMPP':
                if len(payload)!=4 or self.face_library is not None:raise ValueError('动作内嵌TMPP长度或数量不正确')
                self.face_library=string(base,struct.unpack('<i',payload)[0],library=True)
                continue
            if tag=='TMAL':
                if actors is not None or len(payload)!=8:raise ValueError('动作内嵌角色列表不唯一')
                actors=links(base,payload,0);continue
            if len(payload)<4:raise ValueError('动作内嵌节点编号截断')
            identifier,start=struct.unpack_from('<Hh',payload)
            if identifier in self.nodes:raise ValueError('动作内嵌节点编号重复')
            node={'tag':tag,'id':identifier,'start_frame':start}
            if tag=='TMDH':
                if root is not None or len(payload)!=8:raise ValueError('动作内嵌根不唯一')
                root=identifier;node['length_frames']=struct.unpack_from('<h',payload,4)[0]
            elif tag=='TMAC':
                if len(payload)!=20:raise ValueError('动作内嵌角色节点长度不正确')
                node['children']=links(base,payload,12)
            elif tag=='TMTR':
                if len(payload)!=16:raise ValueError('动作内嵌轨道节点长度不正确')
                node['children']=links(base,payload,4);node['lua']=struct.unpack_from('<I',payload,12)[0]
            else:
                node['payload']=payload
                if tag in('C009','C010'):
                    if len(payload)!=(16 if tag=='C009'else 32):raise ValueError('动作内嵌动画节点长度不正确')
                    # Both original C009/C010 controller initializers and their
                    # full preparation methods consume unsigned 16-bit start
                    # and duration. Keep the authored 32-bit field for review;
                    # its upper half is not part of the controller lifetime.
                    node['start_frame']=struct.unpack_from('<H',payload,2)[0]
                    node['duration_frames']=struct.unpack_from('<H',payload,4)[0]
                    node['duration_field_raw']=struct.unpack_from('<I',payload,4)[0]
                    node['name']=string(base,struct.unpack_from('<I',payload,12 if tag=='C009'else 24)[0])
                    if tag=='C010':
                        node['flags']=struct.unpack_from('<I',payload,12)[0]
                        node['sample_start'],node['sample_end']=struct.unpack_from('<ff',payload,16)
                        if not all(math.isfinite(node[k])for k in('sample_start','sample_end')):raise ValueError('动作内嵌采样时间非有限')
            self.nodes[identifier]=node
        if root is None or actors is None:raise ValueError('动作内嵌根或角色列表缺失')
        self.root=self.nodes[root];self.actors=actors;self.events=[];seen={root}
        def child(identifier,tag):
            if identifier not in self.nodes or self.nodes[identifier]['tag']!=tag or identifier in seen:raise ValueError('动作内嵌引用类型或归属不正确')
            seen.add(identifier);return self.nodes[identifier]
        for actor_id in actors:
            actor=child(actor_id,'TMAC')
            for track_id in actor['children']:
                track=child(track_id,'TMTR')
                for event_id in track['children']:
                    event=self.nodes.get(event_id)
                    if event is None or not event['tag'].startswith('C')or event_id in seen:raise ValueError('动作内嵌事件引用不正确')
                    seen.add(event_id)
                    self.events.append({**event,'actor_id':actor_id,'track_id':track_id,
                        'actor_start':actor['start_frame'],'track_start':track['start_frame'],'track_lua':track['lua']})
        # Some authored streams retain an unreferenced empty track or unused
        # footstep-only track. Keep that fact explicit. Never admit an orphan
        # visual/animation branch as though it belonged to the active graph.
        self.unused_audio_nodes=[]
        for identifier in set(self.nodes)-seen:
            node=self.nodes[identifier]
            if node['tag']=='TMTR'and all(i not in seen and self.nodes.get(i,{}).get('tag')in AUDIO for i in node['children']):
                self.unused_audio_nodes.extend((identifier,*node['children']))
        seen.update(self.unused_audio_nodes)
        # Authored PAPs can retain an unlinked prop/effect track (e.g. the
        # fist/kama battle02 sources). Standalone actor preview does not run
        # these inactive branches. Keep them recorded; never salvage an
        # unowned body or facial animation by treating it as a prop.
        self.unused_visual_nodes=[]
        if character_only:
            for identifier in set(self.nodes)-seen:
                node=self.nodes[identifier]
                if(node['tag']=='TMTR'and all(i not in seen and
                    self.nodes.get(i,{}).get('tag','').startswith('C')and
                    self.nodes[i]['tag']not in('C009','C010')for i in node['children'])):
                    self.unused_visual_nodes.extend((identifier,*node['children']))
            seen.update(self.unused_visual_nodes)
            inactive=[i for i in set(self.nodes)-seen if self.nodes[i]['tag'].startswith('C')and self.nodes[i]['tag']not in('C009','C010')]
            self.unused_visual_nodes.extend(inactive);seen.update(inactive)
        self.unused_animation_nodes=[]
        if retain_unlinked_animations:
            for identifier in set(self.nodes)-seen:
                node=self.nodes[identifier]
                if(node['tag']=='TMTR'and all(i not in seen and self.nodes.get(i,{}).get('tag')in('C009','C010')for i in node['children'])):
                    self.unused_animation_nodes.extend((identifier,*node['children']))
            seen.update(self.unused_animation_nodes)
        if seen!=set(self.nodes):raise ValueError('动作内嵌存在未归属的可见控制节点')

    def omitted_visuals(self):
        return(tuple(e for e in self.events if e['tag']in OPTIONAL_VISUALS)+
            tuple({**self.nodes[i],'inactive':True}for i in self.unused_visual_nodes if self.nodes[i]['tag'].startswith('C')and self.nodes[i]['tag']not in AUDIO))

    def pending(self,body_name,*,resolved=(),omit_visuals=False):
        resolved=frozenset(resolved)
        if not resolved.issubset({e['id']for e in self.events if e['tag']=='C010'}):raise ValueError('配套表情已接入节点不正确')
        result=set();body=[e for e in self.events if e['tag']=='C009']
        if(len(body)!=1 or body[0]['name']!=body_name or body[0]['start_frame']or body[0]['actor_start']or body[0]['track_start']
            or body[0]['duration_frames']<=0 or struct.unpack_from('<I',body[0]['payload'],8)[0]):result.add('C009')
        skipped=OPTIONAL_VISUALS if omit_visuals else frozenset()
        result.update(e['tag']for e in self.events if e['tag']not in AUDIO|skipped|{'C009'}and e['id']not in resolved)
        if any(e['track_lua']for e in self.events if e['tag']not in AUDIO|skipped):result.add('TMTR-Lua')
        return tuple(sorted(result))


def pap_timeline(raw,header,clip_name,*,character_only=False):
    at=header['footer_offset'];selected=None
    for i,clip in enumerate(header['clips']):
        if at+12>len(raw)or raw[at:at+4]!=b'TMLB':raise ValueError('PAP内嵌时间线截断')
        length=struct.unpack_from('<I',raw,at+4)[0]
        if not 12<=length<=4*1024*1024 or at+length>len(raw):raise ValueError('PAP内嵌时间线长度越界')
        if clip['name']==clip_name:
            if selected is not None:raise ValueError('PAP内嵌片段重复')
            selected=TimelineGraph(raw[at:at+length],character_only=character_only)
        at+=length
        if i<len(header['clips'])-1:
            padding=(-at)%4
            if any(raw[at:at+padding]):raise ValueError('PAP内嵌时间线填充不正确')
            at+=padding
    if at!=len(raw)and(len(raw)-at>3 or any(raw[at:])):raise ValueError('PAP内嵌尾部数据尚未接入')
    if selected is None:raise ValueError('PAP没有选定片段的内嵌时间线')
    return selected
