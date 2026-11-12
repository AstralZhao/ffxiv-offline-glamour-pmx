"""Reviewed ordinary VR displacement math with explicit birth basis inputs.

The caller must supply the actual constructor matrix and local-direction
mode. This helper is not a substitute for establishing those source inputs.
"""
import math
import numpy as np


def linear(matrix,vector):
    f=np.float32;m=np.asarray(matrix,dtype='<f4');v=np.asarray(vector,dtype='<f4')
    return f(f(m[:,1]*v[1]+m[:,0]*v[0])+m[:,2]*v[2])


def cylinder_packet(grid,normal,rotate,parent,speed,radial=False):
    """Actual leaf outputs in world space, before mode-specific inverse.

    Direction exists independently of speed. Radial direction preserves
    the original world-position minus world-center cancellation.
    """
    f=np.float32;parent=np.asarray(parent,dtype='<f4')
    world=f(linear(parent[:3,:3],linear(rotate,grid))+parent[:3,3])
    direction=f(world-parent[:3,3])if radial else linear(parent[:3,:3],linear(rotate,normal))
    squared=f(f(f(direction[1]*direction[1])+f(direction[0]*direction[0]))+f(direction[2]*direction[2]))
    if squared<=0 or not np.isfinite(squared):raise ValueError('Invalid cylinder birth direction')
    third=f(direction*f(f(1)/f(np.sqrt(squared))))
    return parent[:3,3].copy()if radial else world,f(third*f(speed)),third


def in_child_space(packet,parent,mode):
    if mode not in(2,3,8):return tuple(np.asarray(v,dtype='<f4')for v in packet)
    from effect_mode8 import inverse
    inv=inverse(parent)
    pos,velocity,direction=(linear(inv[:3,:3],v)for v in packet)
    return np.float32(pos+inv[:3,3]),velocity,direction


def construct(velocity,direction,local_direction,parent=None):
    """Mode0/1 supplies its actual birth parent; mode2/3/8 uses global UP."""
    basis=birth_basis(direction,parent);v=np.asarray(velocity,dtype='<f4')
    return (linear(basis.T,v)if local_direction==1 else v.copy()),basis


def birth_basis(direction,parent=None):
    """Constructor basis from generator output+18 and mode-specific UP.

    This is saved at instance+174, independently of the parent matrix.
    The generator-specific direction producer still belongs to the caller.
    """
    f=np.float32;target=np.asarray(direction,dtype='<f4')
    if target.shape!=(3,)or not np.isfinite(target).all():raise ValueError('Invalid VR birth direction')
    def squared(v):return f(f(f(v[1]*v[1])+f(v[0]*v[0]))+f(v[2]*v[2]))
    def norm(v):return f(np.sqrt(squared(v)))
    length=norm(target)
    # The original look matrix leaves the initialized identity on failure.
    if squared(target)<f(1e-4):return np.eye(3,dtype='<f4')
    if parent is not None:
        parent=np.asarray(parent,dtype='<f4')
        if parent.shape!=(4,4)or not np.isfinite(parent).all():raise ValueError('Invalid VR birth parent')
    forward=f(target*f(f(1)/length));up=np.array((0,1,0),dtype='<f4')if parent is None else parent[:3,1].copy()
    if norm(up)==0:raise ValueError('Degenerate VR birth parent UP')
    normalized_up=f(up*f(f(1)/norm(up)))
    dot=f(f(f(forward[0]*normalized_up[0])+f(forward[1]*normalized_up[1]))+f(forward[2]*normalized_up[2]))
    if dot>f(.99)or dot<f(-.99):up=np.array((0,0,1),dtype='<f4')if parent is None else parent[:3,2].copy()
    if norm(up)==0:raise ValueError('Degenerate VR birth parent alternate UP')
    # 37dc10 normalizes UP only for its parallel check. The cross product
    # uses RAW UP, then normalizes RIGHT; preserve that float32 sequence.
    normalized_up=f(up*f(f(1)/norm(up)))
    dot=f(f(f(normalized_up[1]*forward[1])+f(normalized_up[0]*forward[0]))+f(normalized_up[2]*forward[2]))
    if abs(dot)>f(.99):return np.eye(3,dtype='<f4')
    cross=np.array((f(f(up[1]*forward[2])-f(up[2]*forward[1])),
        f(f(up[2]*forward[0])-f(up[0]*forward[2])),
        f(f(up[0]*forward[1])-f(up[1]*forward[0]))),dtype='<f4')
    right=f(cross*f(f(1)/norm(cross)))
    actual_up=np.array((f(f(right[2]*forward[1])-f(right[1]*forward[2])),
        f(f(right[0]*forward[2])-f(right[2]*forward[0])),
        f(f(right[1]*forward[0])-f(right[0]*forward[1]))),dtype='<f4')
    return np.column_stack((right,actual_up,forward)).astype('<f4')


def increment(velocity,delta,angles,basis,local_direction):
    f=np.float32;velocity=np.asarray(velocity,dtype='<f4');angles=np.asarray(angles,dtype='<f4')
    basis=np.asarray(basis,dtype='<f4')
    if velocity.shape!=(3,)or angles.shape!=(3,)or basis.shape!=(3,3):raise ValueError('Invalid VR input shape')
    if not all(np.isfinite(v).all()for v in(velocity,angles,basis))or not math.isfinite(delta)or delta<0:
        raise ValueError('Invalid VR input')
    vector=f(velocity*f(delta));squared=f(f(f(vector[1]*vector[1])+f(vector[0]*vector[0]))+f(vector[2]*vector[2]))
    length=f(np.sqrt(squared))
    if length==0 or not np.any(angles):return vector
    # Preserve the original sequence, including zero products. The source
    # constructs a direction from LENGTH, rather than rotating vector.
    cx,cy,cz=(f(math.cos(float(v)))for v in angles)
    sx,sy,sz=(f(math.sin(float(v)))for v in angles);zero=f(0)
    a=f(f(f(cx*zero)+zero)-f(sx*length))
    b=f(f(cx*length)+f(f(sx*zero)+zero))
    c=f(f(length*zero)+zero)
    u=f(f(f(a*zero)-f(c*sy))+f(cy*b))
    v=f(f(f(c*zero)+a)+f(b*zero))
    w=f(f(f(cy*c)+f(a*zero))+f(b*sy))
    direction=np.array((f(f(f(cz*w)-f(v*sz))+f(u*zero)),
        f(f(f(w*sz)+f(cz*v))+f(u*zero)),f(f(f(w*zero)+f(v*zero))+u)),dtype='<f4')
    out=f(f(basis[:,0]*direction[0]+basis[:,1]*direction[1])+basis[:,2]*direction[2])
    if local_direction==1:
        # Source X sums Y+X, then Z. This is transpose, not a second
        # forward matrix multiplication; do not cancel it algebraically.
        out=np.array((f(f(f(basis[1,0]*out[1])+f(basis[0,0]*out[0]))+f(basis[2,0]*out[2])),
            f(f(f(basis[0,1]*out[0])+f(basis[1,1]*out[1]))+f(basis[2,1]*out[2])),
            f(f(f(basis[0,2]*out[0])+f(basis[1,2]*out[1]))+f(basis[2,2]*out[2]))),dtype='<f4')
    return out
