"""Source-authored LOCAL maps, selected using original motion-line flags."""
import numpy as np
import struct
from havok_probe import TagReader,read_skeleton
from static_local_mapper import local_mapping
from geometry_probe import local_matrix


class MotionLines:
    def __init__(self,raw):
        if not isinstance(raw,bytes)or not 32<=len(raw)<=65536 or raw[:4]!=b'tlm\0':
            raise ValueError('动作线路表格式不正确')
        if not 0<raw[9]<=128:raise ValueError('动作线路表数量越界')
        self.rows=[];at=32
        for _ in range(raw[9]):
            if at+17>len(raw):raise ValueError('动作线路表记录不完整')
            count=raw[at+16];end=at+17+16*count
            if end>len(raw):raise ValueError('动作线路表名称越界')
            patterns=[]
            for start in range(at+17,end,16):
                packet=raw[start:start+16]
                if b'\0'not in packet:raise ValueError('动作线路表名称缺少结束符')
                patterns.append(packet.split(b'\0')[0].decode('ascii').removeprefix('^'))
            self.rows.append((struct.unpack_from('<I',raw,at+8)[0],patterns));at=end
        if at!=len(raw):raise ValueError('动作线路表包含未接入的数据')

    def flags(self,name):
        if not isinstance(name,str)or not name or not name.isascii()or '\0'in name:
            raise ValueError('动作片段名不正确')
        for flags,patterns in self.rows:
            for pattern in patterns:
                if len(name)>=len(pattern)and all(c=='.'or c==n for c,n in zip(pattern,name)):
                    return flags
        raise NotImplementedError('没有匹配此片段的动作线路')


class BodyMappingPlan:
    """Compile once per clip; do not retain a Havok object graph per frame."""
    def __init__(self,source_raw,target_raw,binding,line_flags):
        if any(struct.unpack_from('<I',raw,4)[0]not in(0x31333030,0x31333031)for raw in(source_raw,target_raw)):
            raise NotImplementedError('此骨架版本的动画映射头尚未接入')
        source_id=struct.unpack_from('<I',source_raw,20)[0];slots=struct.unpack_from('<4I',target_raw,24)
        source=read_skeleton(source_raw);target=read_skeleton(target_raw)
        if slots.count(source_id)>1:raise ValueError('来源骨架映射槽重复')
        self.bones=[b['name']for b in target['bones']]
        self.tracks=np.asarray(binding['transformTrackToBoneIndices'],dtype=np.int32)
        if len(set(self.tracks))!=len(self.tracks)or np.any(self.tracks<0)or np.any(self.tracks>=len(source['bones'])):
            raise ValueError('动画映射轨道越界或重复')
        # The original selector returns null when neither dictionary has the
        # source ID. Native control then returns the original binding indices
        # and samples without a mapper. Require identical addressed identities,
        # including ancestors; never substitute a nearest/name-only retarget.
        self.direct=source_id not in slots
        if self.direct:
            self.dictionary=None;review=set(map(int,self.tracks))
            for index in list(review):
                while index>=0:
                    review.add(index);index=source['bones'][index]['parent']
            for index in review:
                if(index>=len(target['bones'])or(source['bones'][index]['name'],source['bones'][index]['parent'])
                    !=(target['bones'][index]['name'],target['bones'][index]['parent'])):
                    raise NotImplementedError('无映射动作的轨道与目标骨架不一致')
            return
        source_objects=TagReader(source_raw[source['havok_payload_offset']:]).read()
        primary_source=next(o for o in source_objects if o['__type__']=='hkaAnimationContainer')['skeletons'][0]
        objects=TagReader(target_raw[target['havok_payload_offset']:]).read()
        primary=next(o for o in objects if o['__type__']=='hkaAnimationContainer')['skeletons'][0]
        roots=[o for o in objects if o['__type__']=='hkRootLevelContainer']
        if len(roots)!=1:raise ValueError('动画映射根不唯一')
        variants=[v for v in roots[0]['namedVariants']if v['className']=='hkaSkeletonMapper']
        active=[i for i,value in enumerate(slots)if value!=0xffffffff and value>>16 not in(4,5)]
        if len(variants)!=2*len(active):raise NotImplementedError('动画映射字典排列尚未接入')
        dictionaries={};seen=set()
        # Loader consumes successive pairs: first Dict2 (+E8), then Dict1 (+D8).
        for position,slot in enumerate(active):
            if slots[slot]in seen:raise ValueError('动画映射字典来源重复')
            seen.add(slots[slot]);pair=variants[position*2:position*2+2]
            if any(v['name']!=str(slot)for v in pair):raise ValueError('动画映射字典与头部顺序不一致')
            dictionaries[slots[slot]]={2:pair[0]['variant']['mapping'],1:pair[1]['variant']['mapping']}
        # Native bit0 tries Dict1, falling back to Dict2. Both serialized
        # entries are required here; sparse/one-sided dictionaries stay pending.
        self.dictionary=1 if line_flags&1 else 2
        mapping=dictionaries[source_id][self.dictionary];a=mapping['skeletonA']
        if mapping['skeletonB']is not primary:raise NotImplementedError('动画映射目标不是当前主骨架')
        if([b['name']for b in a['bones']]!=[b['name']for b in source['bones']]
            or a['parentIndices']!=[b['parent']for b in source['bones']]
            or not np.allclose(a['referencePose'],primary_source['referencePose'],atol=2e-6,rtol=0)):
            raise ValueError('动画映射来源与原始骨架不一致')
        if(mapping['mappingType']!=1 or mapping['chainMappings']or mapping['unmappedBones']or mapping['keepUnmappedLocal']!=1):
            raise NotImplementedError('此动作需要复杂骨架映射')
        self.reference=np.asarray(primary_source['referencePose'],dtype='<f4').copy()
        self.entries=[];seen=set()
        for entry in mapping['simpleMappings']:
            ia,ib=entry['boneA'],entry['boneB']
            if not 0<=ia<len(self.reference)or not 0<=ib<len(self.bones)or ib in seen:raise ValueError('动画映射端点越界或重复')
            if source['bones'][ia]['name']!=self.bones[ib]:raise NotImplementedError('动画映射改变骨骼名称')
            self.entries.append((ia,ib,np.asarray(entry['aFromBTransform'],dtype='<f4').copy()));seen.add(ib)
        if seen!=set(range(len(self.bones))):raise NotImplementedError('动画映射未覆盖目标骨架')

    def apply(self,sample,local,names):
        if self.direct:
            from prepare_fixed_stance import normal_local_pose
            return normal_local_pose(local,self.bones,names,{'blendHint':0,'transformTrackToBoneIndices':self.tracks},sample)
        values=self.reference.copy()
        values[self.tracks,:3]=sample['translation'];values[self.tracks,4:8]=sample['rotation'];values[self.tracks,8:11]=sample['scale']
        result=local.copy();lookup={name:i for i,name in enumerate(names)}
        for ia,ib,transform in self.entries:
            value=local_mapping(values[ia],transform)
            result[lookup[self.bones[ib]]]=local_matrix({'translation':value[:3],'rotation':value[4:8],'scale':value[8:11]})
        return result
