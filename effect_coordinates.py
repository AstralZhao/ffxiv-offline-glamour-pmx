"""CN affine48 assembly in column-vector convention, explicit channel inputs."""
import numpy as np


def point_auxiliary(matrix,follow):
    """Point3c0520: source float32 lengths, epsilon and reciprocal multiply."""
    m=np.asarray(matrix,dtype='<f4')
    if m.shape!=(4,4)or not np.isfinite(m).all():raise ValueError('Invalid point transform')
    squared=m[:3,:3]*m[:3,:3]
    lengths=np.sqrt((squared[1]+squared[0])+squared[2])
    out=np.eye(4,dtype='<f4');out[:3,3]=m[:3,3]
    if follow:
        lengths=lengths+np.float32(1.1920928955078125e-7)
        inverse=np.float32(1)/lengths
        # Keep the zero terms: original matrix composition preserves this
        # particular arithmetic (including signed zeros), not simple division.
        zero=np.float32(0);b=m[:3,:3]
        out[:3,0]=(b[:,0]*inverse[0]+b[:,1]*zero)+b[:,2]*zero
        out[:3,1]=(b[:,1]*inverse[1]+b[:,0]*zero)+b[:,2]*zero
        out[:3,2]=(b[:,1]*zero+b[:,0]*zero)+b[:,2]*inverse[2]
        out[:3,3]=((b[:,1]*zero+b[:,0]*zero)+b[:,2]*zero)+m[:3,3]
    return out,lengths


def compose(scale,rotate,position,order):
    s=np.asarray(scale,dtype='<f4');r=np.asarray(rotate,dtype='<f4');p=np.asarray(position,dtype='<f4')
    if s.shape!=(3,)or r.shape!=(3,3)or p.shape!=(3,)or order not in range(6):raise ValueError('Invalid coordinate assembly input')
    if not all(np.isfinite(v).all()for v in(s,r,p)):raise ValueError('Nonfinite coordinate assembly input')
    def apply(v):return (r[:,0]*v[0]+r[:,1]*v[1])+r[:,2]*v[2]
    out=np.eye(4,dtype='<f4');out[:3,:3]=r*s[None,:]if order in(0,1,4)else r*s[:,None]
    out[:3,3]=(p,apply(s*p),s*p,s*apply(p),apply(p),p)[order]
    return out


def multiply(first,second):
    """Original37d750 row(first*second) == column(second@first), float32."""
    a=np.asarray(first,dtype='<f4');b=np.asarray(second,dtype='<f4')
    if a.shape!=(4,4)or b.shape!=(4,4)or not np.array_equal(a[3],(0,0,0,1))or not np.array_equal(b[3],(0,0,0,1)):
        raise ValueError('Expected finite affine matrices')
    if not np.isfinite(a).all()or not np.isfinite(b).all():raise ValueError('Nonfinite affine matrix')
    result=np.eye(4,dtype='<f4')
    for column in range(4):
        values=(b[:3,0]*a[0,column]+b[:3,1]*a[1,column])+b[:3,2]*a[2,column]
        result[:3,column]=values+b[:3,3]if column==3 else values
    return result


def selective(scale,rotate,position,order,auxiliary,origin):
    """Mode0/1 prefix, supplied inherited channels, auxiliary and origin.

    Root document auxiliary is identity in this standalone preview context;
    origin is added AFTER auxiliary composition, as3e385a..3e3981.
    """
    result=compose(scale,rotate,position,order)
    if auxiliary is not None:result=multiply(result,auxiliary)
    origin=np.asarray(origin,dtype='<f4')
    if origin.shape!=(3,)or not np.isfinite(origin).all():raise ValueError('Invalid coordinate origin')
    result[:3,3]=result[:3,3]+origin
    return result
