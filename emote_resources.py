"""Read-only CN emote export roots; no animation playback or game access."""
import re
import struct
from ff14_readonly import Sheet
from cn_pap_load_table import PapLoadTable

RIGS=tuple(range(101,1802,100))
LAYOUTS={'Emote':('aff5d14e',44),'ActionTimeline':('d803699f',24),'EmoteMode':('087a32e7',8),
    'WeaponTimeline':('7ba5390a',8)}
PATHS=re.compile(rb'(?:chara|vfx|common|shader|sound|bgcommon)/[a-zA-Z0-9_./-]+\.[a-zA-Z0-9_]+')
PLT='chara/xls/animation/paploadtable.plt'
MLT='chara/xls/animation/MotionLineTable.mlt'
AET='chara/xls/animation/AnimationExchangeTable.aet'


def references(raw):
    return {match.group().decode('ascii').lower()for match in PATHS.finditer(raw)}


def collect(pack,*,cancel=None,callback=None):
    from resource_pack import check_cancel,progress
    sheets={name:Sheet(pack,name)for name in LAYOUTS}
    for name,sheet in sheets.items():
        expected=LAYOUTS[name]
        if (sheet.header.column_hash,sheet.header.fixed_size)!=expected:
            raise ValueError('情感动作资源表结构尚未核对：'+name)
    emotes=list(sheets['Emote'].rows());modes=list(sheets['EmoteMode'].rows())
    actions={row.id:row.value(6)for row in sheets['ActionTimeline'].rows()}
    # Physical CN columns, independently checked against the actual tables.
    # Include all seven context timelines, blank-name/end entries and unlocks.
    ids={row.value(column)for row in emotes for column in range(1,8)if row.value(column)}
    if ids-set(actions):raise ValueError('情感动作引用了不存在的时间线')
    keys={actions[key].lower()for key in ids if actions[key]}
    keys.update(key.lower()for key in actions.values()if key.startswith(('emote/','facial/')))
    weapon_keys={row.value(0).lower()for row in sheets['WeaponTimeline'].rows()if row.value(0)}
    if any(not re.fullmatch(r'[a-z0-9_/]+',key)or'..'in key.split('/')for key in keys|weapon_keys):
        raise ValueError('情感动作时间线路径不正确')
    icons={row.value(20)for row in emotes if row.value(20)>0}
    if any(not 0<identifier<=1000000 for identifier in icons):raise ValueError('情感动作图标编号越界')
    roots={PLT,MLT.lower(),AET.lower()};tmb_paths={'chara/action/'+key+'.tmb'for key in keys|weapon_keys}
    plt=PapLoadTable(pack.read(PLT));pap_paths=set()
    missing_rigs=[]
    for race in RIGS:
        if (race,1)not in plt.rows:missing_rigs.append(race)
    if missing_rigs:raise ValueError('情感动作缺少种族动作路由：'+str(missing_rigs))
    for index in range(plt.common_count):
        _,_,offset=struct.unpack_from('<3I',plt.raw,plt.common+index*12)
        start=plt.names+offset;end=plt.raw.find(b'\0',start)
        if not 0<=start<end<=start+192:raise ValueError('情感动作 PLT 名称越界')
        key=plt.raw[start:end].decode('ascii')
        if not re.fullmatch(r'[a-z0-9_/]+(?:\*)?',key):raise ValueError('情感动作 PLT 名称不正确')
        targets={k for k in keys if k.startswith(key[:-1])}if key.endswith('/*')else {key}if key in keys or key.startswith(('emote/','facial/'))else set()
        if not targets:continue
        for race in RIGS:
            model,variant,prefix=struct.unpack('<IHH',plt.packet(race,1,index))
            if model>>16:raise ValueError('情感动作路由不是玩家骨架')
            group=plt.string(plt.prefix+prefix,plt.names)
            pap_paths.update(f'chara/human/c{model:04}/animation/a{variant:04}/{group}/{target}.pap'for target in targets)
    # Nonresident clips may not have a PLT slot. All chara bytes are retained;
    # actual existing conventional paths also seed external dependency reads.
    for race in RIGS:
        pap_paths.update(f'chara/human/c{race:04}/animation/a0001/bt_common/{key}.pap'for key in keys)
    missing_tmb=[];found_pap=0;external=set()
    paths=sorted(tmb_paths|pap_paths)
    for ordinal,path in enumerate(paths,1):
        check_cancel(cancel)
        if ordinal%200==0:progress(callback,'收集全部情感动作',ordinal,len(paths))
        try:raw=pack.read(path)
        except FileNotFoundError:
            if path in tmb_paths:missing_tmb.append(path)
            continue
        roots.add(path);roots.update(references(raw));external.update(references(raw))
        if path.endswith('.pap'):found_pap+=1
    progress(callback,'收集全部情感动作',len(paths),len(paths))
    return {'paths':roots,'icons':icons,'summary':{
        'source':'CN Emote/EmoteMode/ActionTimeline + original PLT; no unlock or race filter',
        'emote_rows':len(emotes),'mode_rows':len(modes),'linked_timeline_ids':len(ids),
        'timeline_keys':len(keys),'existing_pap_paths':found_pap,'rigs':list(RIGS),
        'weapon_timeline_keys':len(weapon_keys),
        'icon_ids':len(icons),'root_paths':len(roots),'external_references':len(external),
        'missing_source_tmb':sorted(missing_tmb),
        'all_chara_animation_bytes_retained':True,'playback_implemented':False}}
