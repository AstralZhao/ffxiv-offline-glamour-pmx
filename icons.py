"""On-demand original item icons, decoded on CPU without the character GPU."""
from collections import OrderedDict
from io import BytesIO
import threading

from PIL import Image

from ff14_readonly import SqPack
from texture_probe import Texture


COLOR_SWATCH_TEXTURE='ui/uld/ListColorChooser.tex'


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


    def color_swatch_layers_png(self):
        """Only native ULD parts18:3 and18:9; omit cross/check/selection glyphs."""
        with self.lock:
            if self.swatch_layers is not None:return self.swatch_layers
            texture=Texture.read(self.pack.read(COLOR_SWATCH_TEXTURE))
            if(texture.width,texture.height,texture.format)!=(144,136,0x1450):raise ValueError('Native color chooser atlas layout changed')
            atlas=Image.frombytes('RGBA',(144,136),texture.mip_data[0],'raw','BGRA')
            layers=Image.new('RGBA',(80,40));layers.paste(atlas.crop((0,48,40,88)),(0,0));layers.paste(atlas.crop((48,88,88,128)),(40,0))
            output=BytesIO();layers.save(output,format='PNG');self.swatch_layers=output.getvalue();return self.swatch_layers
