"""Independent example DATs; never derived by changing the user's test.dat."""
import struct

from cn_character import CLANS
from probe_appearance_presets import FIELDS


def miqote_example(tribe):
    if tribe not in(7,8):raise ValueError('Unknown Miqote clan')
    return example_dat(4,tribe,2,'猫魅族')


def example_dat(race,tribe,hair,label):
    p=dict.fromkeys(FIELDS,0)
    p.update(race=race,gender=1,body_type=1,tribe=tribe,face=1,hair=hair,tail_or_ear_type=1,
        height=50,bust=50,ear_muscle_tail_size=50,skin_color=50,hair_color=20,left_eye_color=7,right_eye_color=7)
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
