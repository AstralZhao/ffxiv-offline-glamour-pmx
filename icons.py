"""On-demand original item icons, decoded on CPU without the character GPU."""
from collections import OrderedDict
from io import BytesIO
import threading
from pathlib import Path

from PIL import Image

from ff14_readonly import SqPack
from texture_probe import Texture


class ItemIcons:
    def __init__(self,game,*,use_bundled=True):
        self.pack=SqPack(game);self.cache=OrderedDict();self.lock=threading.Lock()
        self.bundled=Path(__file__).resolve().parents[1]/'artifacts/cn-data/dye-icons'if use_bundled else None

    def png(self,identifier):
        if type(identifier)is not int or not 0<identifier<=1000000:raise ValueError('Invalid item icon ID')
        with self.lock:
            if identifier in self.cache:self.cache.move_to_end(identifier);return self.cache[identifier]
            if self.bundled is not None:
                prepared=self.bundled/(str(identifier)+'.png')
                if prepared.is_file():
                    value=prepared.read_bytes();self.cache[identifier]=value
                    while len(self.cache)>256:self.cache.popitem(last=False)
                    return value
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
