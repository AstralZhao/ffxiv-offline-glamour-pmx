"""Independent example DATs; never derived by changing the user's test.dat."""
import struct

from cn_character import CLANS
from probe_appearance_presets import FIELDS

CREATION_PROFILES=(
    {'id':'au-ra-female','label':'敖龙族女性','race':6,'gender':1,'tribes':(11,12)},
    {'id':'miqote-female','label':'猫魅族女性','race':4,'gender':1,'tribes':(7,8)},
    {'id':'miqote-male','label':'猫魅族男性','race':4,'gender':0,'tribes':(7,8)},
    {'id':'viera-female','label':'维埃拉族女性','race':8,'gender':1,'tribes':(15,16)},
    {'id':'viera-male','label':'维埃拉族男性','race':8,'gender':0,'tribes':(15,16)},
)


def miqote_example(tribe):
    if tribe not in(7,8):raise ValueError('Unknown Miqote clan')
    return example_dat(4,tribe,2,'猫魅族')


def miqote_male_example(tribe):
    if tribe not in(7,8):raise ValueError('Unknown Miqote clan')
    return example_dat(4,tribe,1,'猫魅族男性',gender=0)


def seed_miqote_male_examples(library):
    for tribe in(7,8):
        key=f'builtin:miqote-male-{tribe}:v1'
        if key in library.metadata['seeded']:continue
        path,_=library.add(miqote_male_example(tribe),'猫魅族男性-'+CLANS[tribe]+'.dat')
        library.metadata['seeded'][key]=path.name
    library.save_index()


def example_dat(race,tribe,hair,label,gender=1):
    p=dict.fromkeys(FIELDS,0)
    p.update(race=race,gender=gender,body_type=1,tribe=tribe,face=1,hair=hair,tail_or_ear_type=1,
        height=50,bust=50 if gender else 0,ear_muscle_tail_size=50,skin_color=50,hair_color=20,left_eye_color=7,right_eye_color=7)
    data=bytearray(212);struct.pack_into('<II',data,0,0x2013ff14,5);data[16:42]=bytes(p.values())
    comment=(label+CLANS[tribe]+' · 示例预设').encode('utf8');data[48:48+len(comment)]=comment
    checksum=0
    for i,value in enumerate(data[16:]):checksum^=value<<(i%24)
    struct.pack_into('<I',data,8,checksum&0xffffffff)
    return bytes(data)


def seed_miqote_examples(library):
    for tribe in(7,8):
        key=f'builtin:miqote-female-{tribe}:v1'
        if key in library.metadata['seeded']:continue
        path,_=library.add(miqote_example(tribe),'猫魅族-'+CLANS[tribe]+'.dat')
        library.metadata['seeded'][key]=path.name
    library.save_index()


def viera_example(tribe):
    if tribe not in(15,16):raise ValueError('Unknown Viera clan')
    return example_dat(8,tribe,1,'维埃拉族')


def seed_viera_examples(library):
    for tribe in(15,16):
        key=f'builtin:viera-female-{tribe}:v1'
        if key in library.metadata['seeded']:continue
        path,_=library.add(viera_example(tribe),'维埃拉族-'+CLANS[tribe]+'.dat')
        library.metadata['seeded'][key]=path.name
    library.save_index()


def viera_male_example(tribe):
    if tribe not in(15,16):raise ValueError('Unknown Viera clan')
    return example_dat(8,tribe,1,'维埃拉族男性',gender=0)


def seed_viera_male_examples(library):
    for tribe in(15,16):
        key=f'builtin:viera-male-{tribe}:v1'
        if key in library.metadata['seeded']:continue
        path,_=library.add(viera_male_example(tribe),'维埃拉族男性-'+CLANS[tribe]+'.dat')
        library.metadata['seeded'][key]=path.name
    library.save_index()
