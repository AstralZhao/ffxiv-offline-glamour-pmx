"""On-demand original item icons, decoded on CPU without the character GPU."""
from collections import OrderedDict
from io import BytesIO
import threading,hashlib
from pathlib import Path

from PIL import Image

from ff14_readonly import SqPack
from texture_probe import Texture


COLOR_SWATCH_TEXTURE='ui/uld/ListColorChooser.tex'
# Temporary UI-only bundling exception, explicitly requested by the user.
BUNDLED_UI_ASSETS={COLOR_SWATCH_TEXTURE:{'file':'demo/assets/ui/ListColorChooser.tex',
    'sha256':'f3cc6cb2cc89f5d3d43384d2a49a06046c28164f9b9c09a8ddc0db65387d9fdd'}}
UI_RESOURCE_ORDER={'game':('source','bundled'),'resource_pack':('bundled','source'),'network':('bundled','source')}


def read_bundled_ui_asset(virtual_path,program=None):
    record=BUNDLED_UI_ASSETS.get(virtual_path)
    if record is None:return None
    root=Path(program)if program is not None else Path(__file__).resolve().parents[1]
    path=root/record['file']
    try:raw=path.read_bytes()
    except FileNotFoundError:return None
    if hashlib.sha256(raw).hexdigest()!=record['sha256']:raise ValueError('Bundled UI resource checksum differs: '+record['file'])
    return raw


def bundled_ui_asset_files(program):
    root=Path(program);result=[]
    for virtual_path,record in BUNDLED_UI_ASSETS.items():
        if read_bundled_ui_asset(virtual_path,root)is None:raise FileNotFoundError('Missing bundled UI resource: '+record['file'])
        result.append(root/record['file'])
    return result



class ItemIcons:
    def __init__(self,game):
        self.pack=SqPack(game);self.cache=OrderedDict();self.lock=threading.Lock();self.swatch_layers=None

    def png(self,identifier):
        if type(identifier)is not int or not 0<identifier<=1000000:raise ValueError('Invalid item icon ID')
        with self.lock:
            if identifier in self.cache:self.cache.move_to_end(identifier);return self.cache[identifier]
            path=f'ui/icon/{identifier//1000*1000:06}/{identifier:06}.tex'
            try:raw=self.pack.read(path[:-4]+'_hr1.tex')
            except FileNotFoundError:raw=self.pack.read(path)
            texture=Texture.read(raw)
            if max(texture.width,texture.height)>512:raise ValueError('Item icon exceeds preview bounds')
            raw=texture.mip_data[0];size=(texture.width,texture.height)
            bcn={0x3420:(1,'BC1'),0x3430:(2,'BC2'),0x3431:(3,'BC3'),0x6432:(7,'BC7')}
            if texture.format in bcn:image=Image.frombytes('RGBA',size,raw,'bcn',bcn[texture.format])
            elif texture.format in(0x1450,0x1451):image=Image.frombytes('RGBA',size,raw,'raw','BGRA'if texture.format==0x1450 else'BGRX')
            else:raise NotImplementedError('Unsupported item-icon format')
            output=BytesIO();image.save(output,format='PNG');value=output.getvalue();self.cache[identifier]=value
            while len(self.cache)>256:self.cache.popitem(last=False)
            return value


    def read_ui_resource(self,virtual_path):
        root=self.pack.game_root
        mode='network'if(root/'network-source.json').is_file()else'resource_pack'if(root/'resource-pack.json').is_file()else'game'
        order=UI_RESOURCE_ORDER[mode]if virtual_path in BUNDLED_UI_ASSETS else('source',)
        for provider in order:
            if provider=='bundled':
                raw=read_bundled_ui_asset(virtual_path)
                if raw is not None:return raw
            elif provider=='source':
                try:return self.pack.read(virtual_path)
                except FileNotFoundError:pass
            else:raise ValueError('Unsupported UI resource provider')
        raise FileNotFoundError(virtual_path)

    def color_swatch_layers_png(self):
        """Only native ULD parts18:3 and18:9; omit cross/check/selection glyphs."""
        with self.lock:
            if self.swatch_layers is not None:return self.swatch_layers
            texture=Texture.read(self.read_ui_resource(COLOR_SWATCH_TEXTURE))
            if(texture.width,texture.height,texture.format)!=(144,136,0x1450):raise ValueError('Native color chooser atlas layout changed')
            atlas=Image.frombytes('RGBA',(144,136),texture.mip_data[0],'raw','BGRA')
            layers=Image.new('RGBA',(80,40));layers.paste(atlas.crop((0,48,40,88)),(0,0));layers.paste(atlas.crop((48,88,88,128)),(40,0))
            output=BytesIO();layers.save(output,format='PNG');self.swatch_layers=output.getvalue();return self.swatch_layers
