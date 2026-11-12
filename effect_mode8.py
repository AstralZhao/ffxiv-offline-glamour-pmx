"""Reviewed cached-SRT inheritance for zero-unstickiness Point children.

The native parent caches a particular decomposition, not its own Euler fields.
These scalar float32 operations preserve the original order, including the
inverse's small-determinant branch. No scene fitting or arbitrary SVD is used.
"""
from functools import lru_cache
import math
import numpy as np
from effect_coordinates import compose,multiply
from effect_sample import rotation,UnsupportedEffect


def affine(matrix):
    m=np.asarray(matrix,dtype='<f4')
    if m.shape!=(4,4)or not np.isfinite(m).all()or not np.array_equal(m[3],(0,0,0,1)):
        raise ValueError('Mode8 requires a finite affine parent')
    return m


def inverse(matrix):
    m=affine(matrix);f=np.float32;v=m[:3].T.copy().ravel();a,b,c,d,e,g,h,i,j,tx,ty,tz=v
    cof=np.asarray((f(f(j*e)-f(i*g)),f(f(i*c)-f(j*b)),f(f(g*b)-f(e*c)),
                    f(f(h*g)-f(j*d)),f(f(j*a)-f(h*c)),f(f(d*c)-f(g*a)),
                    f(f(i*d)-f(h*e)),f(f(h*b)-f(i*a)),f(f(e*a)-f(d*b))),dtype='<f4')
    det=f(f(f(cof[3]*b)+f(cof[0]*a))+f(cof[6]*c))
    out=np.eye(4,dtype='<f4')
    if f(-1e-5)<=det<=f(1e-5):out[:3,3]=-v[9:12];return out
    A=f(f(tz*h)-f(tx*j));B=f(f(ty*h)-f(tx*i));C=f(f(tz*i)-f(ty*j))
    nx=f(f(f(A*e)-f(C*d))-f(B*g))
    ny=f(f(f(C*a)-f(A*b))+f(B*c))
    D=f(f(tz*d)-f(tx*g));E=f(f(ty*d)-f(tx*e));F=f(f(tz*e)-f(ty*g))
    nz=f(f(f(D*b)-f(F*a))-f(E*c))
    reciprocal=f(f(1)/det)
    out[:3,:3]=(cof*reciprocal).reshape(3,3).T
    out[:3,3]=np.asarray((nx,ny,nz),dtype='<f4')*reciprocal
    return out


def decompose(matrix):
    f=np.float32;v=affine(matrix)[:3].T.copy().ravel()
    scale=np.asarray([f(np.sqrt(f(f(f(a*a)+f(b*b))+f(c*c))))for a,b,c in v[:9].reshape(3,3)],dtype='<f4')
    if np.any(scale==0):raise UnsupportedEffect('Mode8退化父坐标分解尚未接入')
    det=f(f(f(f(v[8]*v[4])-f(v[7]*v[5]))*v[0])+f(f(f(v[6]*v[5])-f(v[8]*v[3]))*v[1]))
    det=f(det+f(f(f(v[7]*v[3])-f(v[6]*v[4]))*v[2]))
    if det<0:scale[0]=-scale[0]
    z=f(math.atan2(float(v[1]),float(v[0])))
    y=f(math.asin(float(np.clip(f(-v[2]/scale[0]),-1,1))))
    cy=f(math.cos(float(y)))
    if abs(cy)<f(1e-4):x=f(0);z=f(math.atan2(float(-v[3]),float(v[4])))
    else:
        x=f(math.asin(float(np.clip(f(f(v[5]/scale[0])/cy),-1,1))))
        if v[8]<0:x=f(f(np.pi)-x)
    return scale,np.asarray((x,y,z),dtype='<f4'),v[9:12].copy()


@lru_cache(maxsize=512)
def _frame(raw):
    m=np.frombuffer(raw,dtype='<f4').reshape(4,4)
    scale,angles,position=decompose(m)
    return compose(scale,rotation(angles,0),position,0).tobytes()


def parent_frame(matrix):
    return np.frombuffer(_frame(affine(matrix).tobytes()),dtype='<f4').reshape(4,4)


@lru_cache(maxsize=512)
def _point(raw):
    m=np.frombuffer(raw,dtype='<f4').reshape(4,4);inv=inverse(m);p=m[:3,3]
    position=((inv[:3,0]*p[0]+inv[:3,1]*p[1])+inv[:3,2]*p[2])+inv[:3,3]
    return position.tobytes()


def point_origin(matrix):
    """Original3dd700 transforms the parent's center through its own inverse."""
    return np.frombuffer(_point(affine(matrix).tobytes()),dtype='<f4')


def point_matrix(parent,local,origin):
    value=affine(local).copy();value[:3,3]=value[:3,3]+np.asarray(origin,dtype='<f4')
    return multiply(value,parent_frame(parent))


def local_matrix(scale,angles,position,extra,origin,order,rotation_order):
    angles=np.float32(np.asarray(angles,dtype='<f4')+np.asarray(extra,dtype='<f4'))
    value=compose(scale,rotation(angles,rotation_order),position,order)
    value[:3,3]=value[:3,3]+np.asarray(origin,dtype='<f4')
    return value
