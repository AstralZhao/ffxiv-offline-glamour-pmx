"""Bake native material colors into portable PNGs in a separate D3D11 device.

The original vertex/pixel programs still evaluate customization, dyes and
decals. A geometry shader changes only rasterization to a mesh's UV rectangle.
No preview render targets, camera, mesh arrays or resource caches are modified.
"""
from __future__ import annotations

import ctypes as c
import hashlib
import struct

import numpy as np
from PIL import Image


def bake_uv(vertices, indices, resolution):
    uv = np.asarray(vertices['uv'][:, :2], dtype=float)
    if not np.isfinite(uv).all():
        raise ValueError('PMX：材质 UV 含无效数值')
    used = uv[np.unique(indices)]
    low, high = used.min(axis=0), used.max(axis=0)
    span = high-low
    if np.any(span < 1e-8):
        raise ValueError('PMX：材质没有可烘焙的二维 UV')
    # Source U may exceed 1 (e.g. body versus face). Preserve that distinction
    # for the native shader; only the output atlas is normalized.
    border = 4/resolution
    return np.asarray(border+(uv-low)/span*(1-2*border), dtype='<f4')


def geometry_shader_source(signature):
    fields, position = [], None
    for index, entry in enumerate(signature):
        mask = entry['mask']
        if mask not in ('x', 'xy', 'xyz', 'xyzw') or entry['type'] != 'float':
            raise NotImplementedError('PMX：暂不支持此顶点着色器输出')
        semantic = entry['name']+str(entry['index'])
        fields.append(f'float{len(mask)} f{index} : {semantic};')
        if entry['system'] == 'POS':
            if mask != 'xyzw':
                raise ValueError('PMX：顶点位置输出格式不正确')
            position = f'f{index}'
    if position is None:
        raise ValueError('PMX：顶点程序没有位置输出')
    return ('''struct Vertex {'''+''.join(fields)+'''};
StructuredBuffer<float2> bakeUV : register(t0);
StructuredBuffer<uint> bakeIndices : register(t1);
[maxvertexcount(3)]
void main(triangle Vertex input[3], uint primitive : SV_PrimitiveID,
          inout TriangleStream<Vertex> output) {
    for(uint corner=0; corner<3; ++corner) {
        Vertex v=input[corner];
        float2 uv=bakeUV[bakeIndices[primitive*3+corner]];
        v.'''+position+'''=float4(uv*float2(2,-2)+float2(-1,1),0.5,1);
        output.Append(v);
    }
    output.RestartStrip();
}''').encode('ascii')


def pad_uv_islands(colors, coverage, steps=3):
    """Extend island edges, preserving authored transparency inside each island."""
    colors, coverage = colors.copy(), coverage.copy()
    height, width = coverage.shape
    for _ in range(steps):
        filled = coverage.copy()
        for dy, dx in ((-1,0),(1,0),(0,-1),(0,1),(-1,-1),(-1,1),(1,-1),(1,1)):
            y=slice(max(0,dy),min(height,height+dy));x=slice(max(0,dx),min(width,width+dx))
            sy=slice(max(0,-dy),min(height,height-dy));sx=slice(max(0,-dx),min(width,width-dx))
            take=~filled[y,x] & coverage[sy,sx]
            colors[y,x][take]=colors[sy,sx][take]
            filled[y,x] |= take
        coverage=filled
    return colors


def glass_rgba(transmission, reflection):
    """Approximate the native multiply + alpha passes with a PMX RGBA map.

    The composition is exact against white, including tinted transmission;
    arbitrary backgrounds cannot be reproduced with PMX source-over alone.
    """
    transmission=np.clip(transmission,0,1)
    result=reflection.copy()
    a=np.clip(reflection[:,:,3:4],0,1)
    minimum=transmission.min(axis=2,keepdims=True)
    opacity=1-minimum*(1-a)
    result[:,:,:3]=((transmission-minimum)*(1-a)+reflection[:,:,:3]*a)/np.maximum(opacity,1e-8)
    result[:,:,3]=opacity[:,:,0]
    return result


class MaterialBaker:
    def __init__(self, scene, resolution=1024):
        if resolution not in (512, 1024, 2048):
            raise ValueError('PMX：贴图尺寸须为 512、1024 或 2048')
        from d3d11_debug import P,compile_shader
        from gpu_selection import preferred_adapter
        from render_preset_materials import PresetMaterialRenderer

        class BakeRenderer(PresetMaterialRenderer):
            # UV triangles must be drawn regardless of their authored winding.
            def context(self, index, types, *args, **kwargs):
                if index == 43 and hasattr(self, 'rasters'):
                    args = (self.rasters[False],)
                return super().context(index, types, *args, **kwargs)

        self.scene, self.resolution = scene, resolution
        self.renderer = BakeRenderer(resolution, resolution, preferred_adapter())
        self.geometry_shaders = {}
        try:
            self.targets = []
            for _ in range(5):
                texture = self.renderer.texture(10, 32)
                view = self.renderer.create(9, [P, P], texture, None)
                self.targets.append((texture, view))
            code=compile_shader(b'main',b'ps_5_0',b'float4 main():SV_TARGET{return float4(1,1,1,1);}')
            self.coverage_shader=self.renderer.create(15,[P,c.c_size_t,P],code,len(code),None)
        except Exception:
            self.close()
            raise

    def close(self):
        self.renderer.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def _draw(self, mesh, plan, uv):
        from d3d11_debug import P, U, F, Viewport, compile_shader
        from material_shader_probe import disassemble
        from material_stage_feasibility import signature
        from static_material_inputs import studio_buffers

        renderer, scene = self.renderer, self.scene
        renderer.context(110, [])
        renderer.last_pass_counts = {}
        renderer.last_gbuffer_reads = {}
        renderer.frame_start_readbacks = renderer.readback_count
        package = scene.packages[mesh['shader_path']]
        code = package['stages']['vertex'][plan['vertex']['index']]['bytecode']
        key = hashlib.sha256(code).digest()
        if key not in self.geometry_shaders:
            source = geometry_shader_source(signature(disassemble(code)))
            compiled = compile_shader(b'main', b'gs_5_0', source)
            self.geometry_shaders[key] = renderer.create(13, [P, c.c_size_t, P], compiled, len(compiled), None)
        uv_buffer = renderer.structured(uv.tobytes(), 8, persistent=False)
        indices = np.asarray(mesh['indices'], dtype='<u4').ravel()
        index_buffer = renderer.structured(indices.tobytes(), 4, persistent=False)
        renderer.context(31, [U, U, P], 0, 2, (P*2)(uv_buffer, index_buffer))
        renderer.context(23, [P, P, U], self.geometry_shaders[key], None, 0)
        renderer.context(44, [U, P], 1, c.byref(Viewport(0, 0, self.resolution, self.resolution, 0, 1)))
        renderer.context(33, [U, P, P], 5, (P*5)(*[view for _, view in self.targets]), None)
        for _, target in self.targets:
            renderer.context(50, [P, P], target, (F*4)(0, 0, 0, 0))
        renderer.context(35, [P, P, U], None, None, 0xffffffff)
        camera, view = renderer.camera_inputs(scene, 0)
        shared = {**studio_buffers(camera, self.resolution, self.resolution), **scene.appearance}
        instance = np.frombuffer(shared['g_InstanceParameter'], dtype='<f4').copy().reshape(11, 4)
        instance[7:9] = scene.static_iris_parameters
        shared['g_InstanceParameter'] = instance.tobytes()
        selected = {**mesh, 'plan': plan}
        renderer.draw_mesh(scene, selected, shared, view)
        renderer.context(33, [U, P, P], 0, None, None)

    def _pixels(self, target):
        renderer = self.renderer
        count = len(renderer.resources)
        try:
            data = renderer.read_target(self.targets[target][0],
                [self.resolution, self.resolution, 1, 1, 10, 1, 0, 0, 32, 0, 0])
        finally:
            for resource in renderer.resources[count:].copy():
                renderer.temporary(resource)
        values = np.frombuffer(data, dtype='<f2').reshape(self.resolution, self.resolution, 4).astype(np.float32)
        if not np.isfinite(values).all():
            raise ValueError('PMX：材质烘焙产生无效颜色')
        return values

    def _coverage(self,mesh):
        from d3d11_debug import P,U,F
        r=self.renderer;target=self.targets[0][1]
        r.context(33,[U,P,P],1,c.byref(target),None)
        r.context(50,[P,P],target,(F*4)(0,0,0,0))
        r.context(9,[P,P,U],self.coverage_shader,None,0)
        r.context(12,[U,U,c.c_int],len(mesh['indices']),0,0)
        r.context(33,[U,P,P],0,None,None)
        return self._pixels(0)[:,:,3]>0

    def __call__(self, mesh):
        from static_material_inputs import alpha_test_enabled
        renderer, scene = self.renderer, self.scene
        uv = bake_uv(mesh['vertices'], mesh['indices'], self.resolution)
        notices = []
        try:
            plans = mesh['plans']
            overlay = mesh.get('overlay')
            if 'material' in plans:
                # Existing unclipped plans retain colors under partially
                # transparent hair. The GBuffer's RGB is already sqrt-encoded.
                plan = plans['material']
                if not plan.get('material_output_layout'):
                    raise NotImplementedError('PMX：尚未识别此材质的基础颜色输出：'+mesh['shader_path'])
                # Some hair packages keep the threshold test even in their
                # default material variant. Evaluate color with threshold zero
                # in a private constant-buffer copy, then recover real alpha
                # from the unmodified native core/fringe passes below.
                color_mesh = mesh
                if alpha_test_enabled(scene.packages[mesh['shader_path']], mesh['constants']):
                    offset = next(offset for identifier, offset, size in scene.packages[mesh['shader_path']]['parameters']
                                  if identifier == 0x29ac0223 and size == 4)
                    constants = bytearray(mesh['constants'])
                    struct.pack_into('<f', constants, offset, 0.)
                    color_mesh = {**mesh, 'constants': bytes(constants)}
                self._draw(color_mesh, plan, uv)
                colors = self._pixels(2)
                coverage = self._pixels(0)[:, :, 3] > 0
                alpha = coverage.astype(float)
                if alpha_test_enabled(scene.packages[mesh['shader_path']], mesh['constants']):
                    # Native clipping decides the opaque core. The original
                    # direct pass supplies subthreshold hair/fringe coverage.
                    clipped = renderer.render_plan(scene, mesh, 'material')
                    self._draw(mesh, clipped, uv)
                    core = self._pixels(0)[:, :, 3] > 0
                    self._draw(mesh, plans['direct'], uv)
                    fringe = self._pixels(0)[:, :, 3]
                    alpha = np.where(core, 1., np.clip(fringe, 0, 1))*coverage
                colors[:, :, 3] = alpha
            elif overlay:
                self._draw(mesh, plans[overlay], uv)
                colors = self._pixels(2)
                if overlay == 'overlay_occlusion':
                    # Native grayscale multiplication is equivalent to black
                    # source-over with alpha=(1-factor), within drawn pixels.
                    coverage = colors[:, :, 3] > 0
                    colors[:, :, 3] = (1-np.clip(colors[:, :, 0], 0, 1))*coverage
                    colors[:, :, :3] = 0
            else:
                selected=mesh
                transmission=None
                if mesh['shader_path'].endswith('/characterglass.shpk'):
                    # PMX has no colored transmission blend. Match the native
                    # two-pass composition against white with source-over;
                    # arbitrary backgrounds remain an explicit approximation.
                    selected={**mesh,'extra_constants':{**mesh.get('extra_constants',{}),
                        'g_GlassOffscreenParam':struct.pack('<4f',0,0,0,0)}}
                    self._draw(selected,plans.get('direct',mesh['plan']),uv)
                    transmission=np.clip(self._pixels(0)[:,:,:3],0,1)
                    selected={**mesh,'extra_constants':{**mesh.get('extra_constants',{}),
                        'g_GlassOffscreenParam':struct.pack('<4f',1,0,0,0)}}
                self._draw(selected, plans.get('direct', mesh['plan']), uv)
                colors = self._pixels(0)
                if transmission is not None:
                    colors=glass_rgba(transmission,colors)
                notices.append(mesh['shader_path']+'：使用固定预览条件烘焙颜色，反射/透射随视角变化的效果不保留。')
            coverage=self._coverage(mesh)
            colors[~coverage]=0
            colors=pad_uv_islands(colors,coverage)
            image = Image.fromarray(np.uint8(np.clip(colors, 0, 1)*255+.5), 'RGBA')
            if not np.any(np.asarray(image)[:, :, 3]):
                notices.append(mesh['material_path']+'：烘焙结果全透明，请在 PMX 编辑器中检查此材质。')
            return image, uv, notices
        finally:
            renderer.context(110, [])
            for resource in reversed(renderer.transient):
                renderer.probe.release(resource)
            renderer.transient.clear()
