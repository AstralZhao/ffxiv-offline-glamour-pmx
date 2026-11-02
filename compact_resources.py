"""Conservative hash-index selection and lossless SqPack block relocation.

All human/unknown chara entries are retained. Only positively classified
weapon/monster models/materials and their exclusive textures are excluded.
UI selection preserves all base/HQ/high-resolution icon IDs, across races.
"""
from collections import Counter,defaultdict,deque
import hashlib,json,re,struct,zipfile
from pathlib import Path

from ff14_readonly import SqPack,game_crc,read_exact

PATHS=re.compile(rb'(?:chara|vfx|common|shader)/[a-zA-Z0-9_./-]+\.[a-zA-Z0-9_]+')
NONHUMAN=re.compile(r'^/?mt_([wm]\d{4})(b\d{4})(?:_|\.)')


class SourceIndex:
    def __init__(self,pack,category,*,cancel=None):
        self.pack=pack;self.category=category
        paths=pack._index_paths(category+'/resource')
        if len(paths)!=1:raise ValueError('Compact export requires one base index per category: '+category)
        self.path=paths[0];self.entries=pack._load_index(self.path);self.synonyms=pack._load_synonyms(self.path)
        self.values=set(v for v in self.entries.values()if not v&1)|set(self.synonyms.values())
        self.alias_counts=Counter(v for v in self.entries.values()if not v&1);self.alias_counts.update(self.synonyms.values())
        self.sizes={};self.headers={};self.selected=set()
        self.split_entries=defaultdict(list)
        split_path=self.path.with_suffix('.index')
        if split_path.is_file():
            raw=split_path.read_bytes();at=struct.unpack_from('<I',raw,12)[0];start,size=struct.unpack_from('<II',raw,at+8)
            if size%16 or start+size>len(raw):raise ValueError('Invalid source directory index')
            for filename,folder,value,_ in struct.iter_unpack('<4I',raw[start:start+size]):
                if not value&1:self.split_entries[value].append((filename,folder))
        self.handles={i:self.path.with_suffix('.dat'+str(i)).open('rb')for i in{(v&14)>>1 for v in self.values}}
        self.source_stats={str(self.path):(self.path.stat().st_size,self.path.stat().st_mtime_ns)}
        if split_path.is_file():self.source_stats[str(split_path)]=(split_path.stat().st_size,split_path.stat().st_mtime_ns)
        for handle in self.handles.values():
            path=Path(handle.name);self.source_stats[str(path)]=(path.stat().st_size,path.stat().st_mtime_ns)
        groups=defaultdict(list)
        for value in self.values:groups[(value&14)>>1].append(value)
        for group,values in groups.items():
            values.sort(key=lambda v:(v&~15)*8);handle=self.handles[group];eof=Path(handle.name).stat().st_size
            for i,value in enumerate(values):
                if i%1000==0:
                    from resource_pack import check_cancel
                    check_cancel(cancel)
                offset=(value&~15)*8;end=(values[i+1]&~15)*8 if i+1<len(values)else eof
                if offset<2048 or end<=offset:raise ValueError('Invalid source resource boundary')
                handle.seek(offset);header=read_exact(handle,24);size,kind,raw,blocks,used,count=struct.unpack('<6I',header)
                if size<24 or size>1024**2 or offset+size>end:raise ValueError('Invalid source resource header')
                # The existing span, including all LOD/mip blocks and authored
                # padding, is copied intact. No image/model re-encoding.
                self.sizes[value]=end-offset;self.headers[value]=(size,kind,raw,blocks,used,count)

    def close(self):
        for handle in self.handles.values():handle.close()

    def handle(self,value):return self.handles[(value&14)>>1]

    def read(self,value):
        source=self.handle(value);offset=(value&~15)*8;header,kind,raw,_,_,count=self.headers[value]
        if kind==2:
            if count>65536 or 24+count*8>header:raise ValueError('Invalid standard source block table')
            source.seek(offset+24);blocks=struct.iter_unpack('<IHH',read_exact(source,count*8))
            result=b''.join(self.pack._block(source,offset+header+at)for at,_,_ in list(blocks))
        elif kind==3:return self.pack._model(source,offset,header,raw)
        elif kind==4:return self.pack._texture(source,offset,header,raw,count)
        else:raise ValueError('Unsupported decoded resource kind')
        if len(result)!=raw:raise ValueError('Decoded resource length differs')
        return result

    def model_materials(self,value):
        source=self.handle(value);offset=(value&~15)*8;header=self.headers[value][0]
        source.seek(offset);native=read_exact(source,208);declarations=struct.unpack_from('<H',native,200)[0]
        total=sum(struct.unpack_from('<11H',native,178))
        source.seek(offset+208);sizes=struct.unpack('<'+str(total)+'H',read_exact(source,total*2))
        parts=[]
        # Native MDL has declarations in stack, string/model metadata in the
        # following runtime group. Vertex and index groups are not needed here.
        for data_field,index_field,count_field in((112,156,178),(116,158,180)):
            first,count=struct.unpack_from('<H',native,index_field)[0],struct.unpack_from('<H',native,count_field)[0]
            if first+count>len(sizes):raise ValueError('Model metadata block table differs')
            at=offset+header+struct.unpack_from('<I',native,data_field)[0]
            for i in range(first,first+count):parts.append(self.pack._block(source,at));at+=sizes[i]
        stack=b''.join(parts);start=declarations*136
        if start+8>len(stack):raise ValueError('Model string header missing')
        size=struct.unpack_from('<I',stack,start+4)[0]
        if start+8+size>len(stack):raise ValueError('Model string table missing')
        return [s.decode('ascii')for s in stack[start+8:start+8+size].split(b'\0')if s.endswith(b'.mtrl')]

    def lookup(self,path):
        key=game_crc(path.encode());value=self.entries.get(key)
        if value is not None and value&1:value=self.synonyms.get((key,path))
        return value


def references(raw):return {m.group().decode('ascii').lower()for m in PATHS.finditer(raw)}


def select_resources(indexes,*,callback=None,cancel=None):
    from resource_pack import check_cancel,progress
    from audit_cn_resources import material_info
    chara=indexes['chara'];chara.selected=set(chara.values)
    kept_textures=set();removed_textures=set();dependencies=set();models_removed=0;materials_removed=0;material_count=0;opaque_kept=0;unknown_models=0
    source_missing=set();placeholders=set();processed=0
    nonhuman_names=defaultdict(set);folder_cache={};proofs=0;effect_folders={};effect_names={};effects_removed=0
    def nonhuman_root(match):
        main,body=match.groups();kind='weapon'if main[0]=='w'else'monster'
        return f'chara/{kind}/{main}/obj/body/{body}',main+body
    def material_proof(value,textures):
        for texture in textures:
            match=re.match(r'(chara/(?:weapon/w\d{4}|monster/m\d{4})/obj/body/b\d{4})/',texture)
            if not match:continue
            base=match[1]
            if base not in folder_cache:
                paths=[base+'/material',*(base+f'/material/v{i:04}'for i in range(256))]
                folder_cache[base]={game_crc(p.encode()):p for p in paths}
            for filename,folder in chara.split_entries.get(value,()):
                parent=folder_cache[base].get(folder)
                if parent:
                    for name in nonhuman_names.get(filename,()):
                        if chara.lookup(parent+'/'+name)==value:return True
        return False
    # Models first: establish exact nonhuman names from source string tables,
    # then verify any material exclusion against both original index formats.
    for value in sorted(chara.values,key=lambda v:(chara.headers[v][1]!=3,(v&14)>>1,(v&~15)*8)):
        check_cancel(cancel);processed+=1;kind=chara.headers[value][1]
        if processed%1000==0:progress(callback,'扫描角色与装备',processed,len(chara.values))
        if kind==3:
            try:names=chara.model_materials(value)
            except (ValueError,struct.error,UnicodeError):unknown_models+=1;continue
            if chara.alias_counts[value]==1 and names and all(NONHUMAN.match(name)for name in names):
                matched=[NONHUMAN.match(name)for name in names]
                roots=[nonhuman_root(m)for m in matched]
                if any(chara.lookup(root+'/model/'+stem+'.mdl')==value for root,stem in roots):
                    chara.selected.discard(value);models_removed+=1
                    for name in names:nonhuman_names[game_crc(name.lstrip('/').encode())].add(name.lstrip('/'))
                    for root,_ in roots:effect_folders[game_crc((root+'/vfx/eff').encode())]=root+'/vfx/eff'
        elif kind==2:
            raw=chara.read(value)
            if raw.startswith(b'XFVA')and chara.alias_counts[value]==1:
                if not effect_names:
                    effect_names={game_crc((name:=f'v{kind}{i:04}.avfx').encode()):name for kind in('w','m')for i in range(10000)}
                own=[]
                for filename,folder in chara.split_entries.get(value,()):
                    if filename in effect_names and folder in effect_folders:own.append(effect_folders[folder]+'/'+effect_names[filename])
                if any(chara.lookup(path)==value for path in own):
                    chara.selected.discard(value);effects_removed+=1;continue
            # All ordinary source metadata/PHYB/KDB/skeleton/animation files
            # remain present, including unclassified/new race structures.
            try:
                if len(raw)<16 or struct.unpack_from('<H',raw,4)[0]!=len(raw):raise ValueError('not material')
                material=material_info(raw)
            except (ValueError,struct.error,IndexError,UnicodeError):
                opaque_kept+=1;dependencies.update(references(raw));continue
            material_count+=1;textures=material['textures']
            excluded=chara.alias_counts[value]==1 and any(p.startswith(('chara/weapon/','chara/monster/'))for p in textures)and material_proof(value,textures)
            if excluded:
                chara.selected.discard(value);materials_removed+=1;proofs+=1;removed_textures.update(textures)
            else:
                kept_textures.update(textures);dependencies.update(textures);dependencies.update(references(raw))
    # Exact cross-material sharing wins over exclusion. Non-source textures are
    # kept conservatively; all human textures/unknown formats stay intact.
    texture_removed=0
    for path in removed_textures-kept_textures:
        if not path.startswith(('chara/weapon/','chara/monster/')):continue
        category=path.split('/')[0]
        if category!='chara':continue
        value=chara.lookup(path)
        if value is not None and chara.alias_counts[value]==1 and chara.headers[value][1]==4:chara.selected.discard(value);texture_removed+=1
    for category in('common','shader','exd'):indexes[category].selected=set(indexes[category].values)
    ui=indexes['ui'];icon_count=0
    # All player-race creation rows plus every supported equipment icon. No
    # gender/tribe/race menu is filtered to current renderer support.
    icon_ids=wardrobe_icons(chara.pack)if hasattr(chara,'pack')else range(1,1000001)
    for ordinal,identifier in enumerate(icon_ids,1):
        if ordinal%100==0:check_cancel(cancel);progress(callback,'收集全部种族与装备图标',ordinal,len(icon_ids))
        folder=identifier//1000*1000
        for path in(f'ui/icon/{folder:06}/{identifier:06}.tex',f'ui/icon/{folder:06}/{identifier:06}_hr1.tex',f'ui/icon/{folder:06}/hq/{identifier:06}.tex'):
            value=ui.lookup(path)
            if value is not None:ui.selected.add(value);icon_count+=1
    # Preserve the whole equipment-effect namespace, then follow its actual
    # string references (plus per-equipment AVFX retained in chara metadata).
    vfx=indexes['vfx']
    for number in range(10000):
        path=f'vfx/equipment/eff/ve{number:04}.avfx';value=vfx.lookup(path)
        if value is not None:vfx.selected.add(value);dependencies.add(path)
    pending=deque(sorted(dependencies));seen=set();restored=0
    while pending:
        check_cancel(cancel);path=pending.popleft()
        if path in seen:continue
        seen.add(path);category=path.split('/')[0]
        if category not in indexes:
            if '/'not in path:placeholders.add(path);continue
            raise ValueError('Source dependency needs an unreviewed category: '+path)
        index=indexes[category];value=index.lookup(path)
        if value is None:source_missing.add(path);continue
        if value not in index.selected:index.selected.add(value);restored+=1
        if index.headers[value][1]==2:
            raw=index.read(value)
            for child in sorted(references(raw)-seen):pending.append(child)
    for path in kept_textures:
        if '/'not in path:continue
        if not path or path.split('/')[0]not in indexes:raise ValueError('Unknown surviving material texture path: '+path)
        index=indexes[path.split('/')[0]];value=index.lookup(path)
        if value is not None and value not in index.selected:raise ValueError('Surviving material dependency was removed: '+path)
    return {'selection_version':1,'race_scope':'all-human-identities-and-unknown-chara-structures-preserved',
        'race_gender_clan_filters':False,'geometry_or_texture_quality_reduced':False,'materials_scanned':material_count,
        'weapon_monster_models_excluded':models_removed,'weapon_monster_materials_excluded':materials_removed,'material_exclusion_index_proofs':proofs,
        'weapon_monster_owned_effects_excluded':effects_removed,
        'exclusive_textures_excluded_before_closure':texture_removed,'opaque_metadata_retained':opaque_kept,'unclassified_models_retained':unknown_models,
        'icon_candidates_found':icon_count,'dependency_paths_checked':len(seen),'dependencies_restored':restored,
        'already_missing_source_path_count':len(source_missing),
        'already_missing_source_paths_sha256':hashlib.sha256(json.dumps(sorted(source_missing)).encode()).hexdigest(),
        'already_missing_source_paths':sorted(source_missing)[:128],'original_nonpath_texture_placeholders':sorted(placeholders),
        'categories':{k:{'source_resources':len(v.values),'kept_resources':len(v.selected),
            'source_span_bytes':sum(v.sizes.values()),'selected_span_bytes':sum(v.sizes[x]for x in v.selected)}for k,v in indexes.items()}}


def wardrobe_icons(pack):
    from build_cn_catalog import VerifiedData
    from pathlib import Path
    root=Path(__file__).resolve().parents[1]
    mapping=json.loads((root/'artifacts/cn-data/schema-mapping.json').read_bytes());data=VerifiedData(pack,mapping)
    slots=dict(data.rows('EquipSlotCategory'));supported={'Head','Body','Gloves','Legs','Feet','Ears','Neck','Wrists','FingerL','FingerR'}
    icons=set()
    for _,row in data.rows('Item'):
        parts={k for k,v in slots[row['EquipSlotCategory']].items()if v>0}
        if parts and parts<=supported and row['Icon']>0:icons.add(row['Icon'])
    for _,row in data.rows('CharaMakeCustomize'):
        if row['Icon']>0:icons.add(row['Icon'])
    # Some face/feature menu values are direct icon IDs rather than links to
    # CharaMakeCustomize. Keep every positive numeric choice across ALL rows;
    # the harmless extra small values avoid any current-race assumptions.
    for _,row in data.rows('CharaMakeType'):
        for field,value in row.items():
            if ('SubMenuParam['in field or'Icon'in field)and type(value)is int and 0<value<=1000000:icons.add(value)
    if any(type(v)is not int or not 0<v<=1000000 for v in icons):raise ValueError('Source icon ID exceeds reviewed range')
    return sorted(icons)


def rewrite_index(index,remap,dat_count):
    original=index.path.read_bytes();at=struct.unpack_from('<I',original,12)[0]
    prefix=bytearray(original[:at+1024]);aliases=[];entries=[]
    for key,value in sorted(index.entries.items()):
        if value&1:
            members=[(path,raw)for(k,path),raw in index.synonyms.items()if k==key and raw in remap]
            if not members:continue
            entries.append((key,value))
            for path,raw in sorted(members):
                encoded=path.encode()
                if len(encoded)>=240:raise ValueError('Source synonym path exceeds bounds')
                aliases.append(struct.pack('<4I',key,0,remap[raw],len(aliases))+encoded+b'\0'*(240-len(encoded)))
        elif value in remap:entries.append((key,remap[value]))
    table=b''.join(struct.pack('<II',*entry)for entry in entries);synonyms=b''.join(aliases)
    # Fresh self-contained tables; only archived hashes/offsets change. The
    # uncompressed virtual resource bytes and original path hashes do not.
    for offset in(8,84,156,228):prefix[at+offset:at+offset+68]=bytes(68)
    struct.pack_into('<II',prefix,at+8,len(prefix),len(table));prefix[at+16:at+36]=hashlib.sha1(table).digest()
    struct.pack_into('<III',prefix,at+80,dat_count,len(prefix)+len(table),len(synonyms));prefix[at+92:at+112]=hashlib.sha1(synonyms).digest()
    prefix[at+960:at+980]=hashlib.sha1(prefix[at:at+960]).digest()
    return bytes(prefix)+table+synonyms


def export_compact(game,output,root,*,callback=None,cancel=None):
    from resource_pack import check_cancel,progress,publish,MANIFEST,CATEGORIES
    import shutil,uuid
    game=Path(game);output=Path(output);pack=SqPack(game)
    temporary=output.with_name(output.name+'.'+uuid.uuid4().hex+'.partial')
    indexes={};records={};summary={};copied=0
    try:
        for category in('common','chara','shader','ui','vfx','exd'):
            check_cancel(cancel);progress(callback,'读取资源索引',len(indexes),6);indexes[category]=SourceIndex(pack,category,cancel=cancel)
        summary=select_resources(indexes,callback=callback,cancel=cancel)
        required=sum(sum(i.sizes[v]for v in i.selected)for i in indexes.values())+64*1024**2
        if shutil.disk_usage(output.parent).free<required:raise ValueError('导出位置可用空间不足')
        with zipfile.ZipFile(temporary,'x',compression=zipfile.ZIP_STORED,allowZip64=True)as archive:
            for category,index in indexes.items():
                remap={};dat_id=0;target=None;position=2048
                def start_dat():
                    name='game/sqpack/ffxiv/'+index.path.with_suffix('.dat'+str(dat_id)).name
                    stream=archive.open(name,'w',force_zip64=True);header=bytes(2048);stream.write(header)
                    h=hashlib.sha256();h.update(header);return name,stream,h
                def finish_dat():
                    if target is not None:
                        target.close();records[name]={'bytes':position,'sha256':h.hexdigest()}
                try:
                    name,target,h=start_dat()
                    for value in sorted(index.selected,key=lambda v:((v&14)>>1,(v&~15)*8)):
                        check_cancel(cancel);size=index.sizes[value]
                        if position+size>4*1024**3 and position>2048:
                            finish_dat();dat_id+=1
                            if dat_id>7:raise ValueError('Selected category requires more than eight data archives')
                            position=2048;name,target,h=start_dat()
                        if position%128:raise ValueError('Relocated resource is not 128-byte aligned')
                        remap[value]=(position//8)|(dat_id<<1);source=index.handle(value);source.seek((value&~15)*8);remaining=size
                        while remaining:
                            check_cancel(cancel);chunk=read_exact(source,min(4*1024**2,remaining));target.write(chunk);h.update(chunk)
                            remaining-=len(chunk);position+=len(chunk);copied+=len(chunk)
                        progress(callback,'导出精简资源',copied,required-64*1024**2)
                finally:finish_dat()
                raw=rewrite_index(index,remap,dat_id+1);name='game/sqpack/ffxiv/'+index.path.name;archive.writestr(name,raw)
                records[name]={'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}
            for name in('game/ffxivgame.ver','game/ffxiv_dx11.exe'):
                raw=(game/name).read_bytes();archive.writestr(name,raw);records[name]={'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}
            for index in indexes.values():
                for path,expected in index.source_stats.items():
                    p=Path(path)
                    if(p.stat().st_size,p.stat().st_mtime_ns)!=expected:raise ValueError('Source archives changed during compact export')
            summary['selected_span_bytes']=copied
            data={'format':'ff14-wardrobe-resources-v2','scope':'wardrobe-selected-all-races','region':'CN','categories':list(CATEGORIES),
                'client_version':(game/'game/ffxivgame.ver').read_text().strip(),'files':records,'selection':summary}
            from resource_pack import manifest_data
            manifest_data(json.dumps(data),json.loads((Path(root)/'docs/cn-customization-profile.json').read_bytes()))
            raw=json.dumps(data,ensure_ascii=False,sort_keys=True,indent=2)
            if len(raw.encode('utf8'))>1024**2:raise ValueError('Compact source manifest exceeds the importer bound')
            archive.writestr(MANIFEST,raw)
        publish(temporary,output,cancel=cancel)
        return output
    finally:
        for index in indexes.values():index.close()
        if temporary.exists():temporary.unlink()
