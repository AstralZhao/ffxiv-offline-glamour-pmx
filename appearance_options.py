"""CN source menus and palette choices for the application preset editor."""
import json
from pathlib import Path
from build_cn_catalog import VerifiedData
from audit_preset_hair_sources import hair_menu
from probe_appearance_presets import FIELDS
from cn_character import race_for_tribe,CLANS

LABELS={'tribe':'部族','hair':'发型','face':'脸型与龙角','height':'身高','bust':'胸围','ear_muscle_tail_size':'尾巴长度',
    'tail_or_ear_type':'尾巴形状','eyebrows':'眉型','eyes':'眼型','nose':'鼻型','jaw':'轮廓','mouth':'嘴型',
    'skin_color':'肤色','hair_color':'发色','highlight_color':'挑染颜色','left_eye_color':'左眼颜色',
    'right_eye_color':'右眼颜色','facial_feature_color':'面部特征颜色','lip_color_or_fur_pattern':'唇色',
    'face_paint_color':'面部彩绘颜色','face_paint':'面部彩绘'}


class AppearanceOptions:
    def __init__(self,pack,store,root):
        self.pack,self.store=pack,store;self.cache={}
        self.data=VerifiedData(pack,json.loads((Path(root)/'artifacts/cn-data/schema-mapping.json').read_bytes()))
        profile=json.loads((Path(root)/'docs/cn-customization-profile.json').read_bytes())
        self.cmp=pack.read(profile['cmp_path'])

    def fields(self,tribe,gender=1):
        key=(tribe,gender)
        if key in self.cache:return self.cache[key]
        menu,_=self.store.geometry_menu(tribe,gender)
        race=race_for_tribe(tribe)
        row=next(r for _,r in self.data.rows('CharaMakeType')if(r['Race'],r['Gender'],r['Tribe'])==(race,gender,tribe))
        customs=dict(self.data.rows('CharaMakeCustomize'))
        result=[{'field':'tribe','label':'部族','kind':'select','mask':255,
            'choices':[{'value':v,'label':CLANS[v]}for v in CLANS if race_for_tribe(v)==race]}]
        for option in menu.options:
            labels={**LABELS,'face':'脸型'if race!=6 else LABELS['face']}
            if race in(2,3):labels.update(ear_muscle_tail_size='耳朵长度',tail_or_ear_type='耳朵形状')
            if race==8:labels.update(ear_muscle_tail_size='耳朵长度',tail_or_ear_type='耳朵形状')
            if race in(1,5):labels.update(ear_muscle_tail_size='肌肉线条')
            choices=[]
            for index,value in enumerate(option.values):
                choice={'value':value,'label':str(value)if option.menu_type==5 else f'样式 {index+1}'}
                if option.menu_type in(0,1):
                    icon=row[f'CharaMakeStruct[{option.menu_index}].SubMenuParam[{index}]']
                    # Type0 links shared examples that do not depict the
                    # selected race/sex. Omit them instead of showing another
                    # character's face (also applies to shared ear examples).
                    # Type1 geometry choices directly name their own icons.
                    if option.menu_type==0:icon=0
                    if icon>0:choice['icon_id']=icon
                    if option.field=='face':
                        choice['feature_icons']=[row[f'FacialFeatureOption[{index}].Option{i}']for i in range(1,8)]
                choices.append(choice)
            record={'field':option.field,'label':labels[option.field],'kind':'slider'if option.menu_type==5 else'select',
                'mask':option.mask,'choices':choices}
            result.append(record)
        p={'race':race,'gender':gender,'body_type':1,'tribe':tribe}
        hair=hair_menu(self.data,p)
        result.append({'field':'hair','label':'发型','kind':'select','mask':255,
            'choices':[{'value':c['hair_id'],'label':f"发型 {c['hair_id']}",'icon_id':c['icon_id']}for c in hair['choices']]})
        palettes={}
        for i in range(28):
            pre=f'CharaMakeStruct[{i}].'
            if row[pre+'Menu']and row[pre+'SubMenuType']in(2,3):palettes[row[pre+'Customize']]=row[pre+'SubMenuNum']
        base=18432+((tribe-1)*2+gender)*5120
        colors={'skin_color':(base,4,palettes[8]),'hair_color':(base+1024,8,palettes[10]),
            'highlight_color':(1024,4,palettes[10]),'left_eye_color':(0,4,palettes[9]),'right_eye_color':(0,4,palettes[9]),
            'facial_feature_color':(3072,4,palettes[13]),'face_paint_color':(4096,4,palettes[25])}
        if race!=7:colors['lip_color_or_fur_pattern']=(2048,4,palettes[20])
        for field,(offset,stride,count)in colors.items():
            ids=list(range(count))
            if field in('lip_color_or_fur_pattern','face_paint_color'):ids+=list(range(128,128+count))
            choices=[]
            for value in ids:
                rgb=self.cmp[offset+value*stride:offset+value*stride+3]
                if len(rgb)!=3:raise ValueError('本机颜色表范围不正确')
                choices.append({'value':value,'label':str(value),'color':'#'+rgb.hex()})
            text='毛色'if race==7 and field=='hair_color'else LABELS[field]
            result.append({'field':field,'label':text,'kind':'color','mask':255,'choices':choices})
        if race==7:
            i=next(i for i in range(28)if row[f'CharaMakeStruct[{i}].Menu']and row[f'CharaMakeStruct[{i}].Customize']==20)
            pre=f'CharaMakeStruct[{i}].'
            if row[pre+'SubMenuType']!=1 or row[pre+'SubMenuNum']!=5:raise ValueError('硌狮族毛纹菜单已变化')
            result.append({'field':'lip_color_or_fur_pattern','label':'毛纹','kind':'select','mask':255,
                'choices':[{'value':row[pre+f'SubMenuGraphic[{j}]'],'label':f'毛纹 {j+1}','icon_id':row[pre+f'SubMenuParam[{j}]']}for j in range(5)]})
        paints=[]
        for i in range(28):
            pre=f'CharaMakeStruct[{i}].'
            if row[pre+'Customize']!=24 or not row[pre+'Menu']:continue
            for j in range(row[pre+'SubMenuNum']):
                linked=customs[row[pre+f'SubMenuParam[{j}]']]
                paints.append({'value':linked['FeatureID'],'label':'无'if linked['FeatureID']==0 else f'彩绘 {j}', 'icon_id':linked['Icon']})
        result.append({'field':'face_paint','label':LABELS['face_paint'],'kind':'select','mask':127,'choices':paints})
        self.cache[key]=result;return result

    def validate(self,original,updated):
        if not updated['gender']and updated['bust']!=original['bust']:raise ValueError('男性预设没有胸围选项')
        if updated['race']in(1,5)and updated['tail_or_ear_type']!=original['tail_or_ear_type']:raise ValueError('当前种族没有尾型选项')
        for option in self.fields(updated['tribe'],updated['gender']):
            field=option['field'];value=updated[field]
            # Keep a valid imported non-menu/unlocked selection when unchanged.
            if value==original[field]and original['tribe']==updated['tribe']:continue
            if value&option['mask']not in{c['value']for c in option['choices']}:
                raise ValueError(option['label']+'不在当前国服选项中')
        if(updated['highlight_flags']^original['highlight_flags'])&127:raise ValueError('挑染标记只支持修改启用选项')
        if(updated['facial_features']^original['facial_features'])&128:raise ValueError('面部特征标记范围不正确')
