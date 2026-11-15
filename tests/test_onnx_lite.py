"""onnx_lite: protobuf reading and the numpy operators, checked against closed-form numpy and (when installed)
against onnxruntime on the same graphs. Models are built here with a tiny ONNX writer, so no onnx package needed."""
import os, struct, sys, tempfile
import numpy as np
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))
import onnx_lite as OL  # noqa: E402


# ---- minimal ONNX protobuf writer (test only)
def _vi(n):
    n &= (1 << 64) - 1
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        out.append(b | (0x80 if n else 0))
        if not n:
            return bytes(out)


def _f(num, wt, payload):
    key = _vi((num << 3) | wt)
    if wt == 0:
        return key + _vi(payload)
    if wt == 5:
        return key + struct.pack("<f", payload)
    return key + _vi(len(payload)) + payload


def _s(num, s):
    return _f(num, 2, s.encode() if isinstance(s, str) else s)


_TYPES = {np.float32: 1, np.int64: 7, np.int32: 6, np.float64: 11, np.bool_: 9}


def tensor(name, a, raw=True):
    a = np.asarray(a)
    dt = _TYPES[a.dtype.type]
    b = b"".join(_f(1, 0, d) for d in a.shape) + _f(2, 0, dt) + _s(8, name)
    if raw:
        b += _s(9, a.astype(a.dtype.newbyteorder("<")).tobytes())
    elif dt == 1:
        b += _s(4, a.astype("<f4").tobytes())               # packed float_data
    elif dt == 7:
        b += _s(7, b"".join(_vi(int(v)) for v in a.ravel()))
    return b


def attr(name, v):
    b = _s(1, name)
    if isinstance(v, float):
        return b + _f(2, 5, v) + _f(20, 0, 1)
    if isinstance(v, (int, np.integer)) and not isinstance(v, bool):
        return b + _f(3, 0, int(v)) + _f(20, 0, 2)
    if isinstance(v, (bytes, str)):
        return b + _s(4, v) + _f(20, 0, 3)
    if isinstance(v, np.ndarray):
        return b + _s(5, tensor("", v)) + _f(20, 0, 4)
    if isinstance(v, list) and v and isinstance(v[0], float):
        return b + _s(7, b"".join(struct.pack("<f", x) for x in v)) + _f(20, 0, 6)
    return b + _s(8, b"".join(_vi(int(x)) for x in v)) + _f(20, 0, 7)


def node(op, ins, outs, **attrs):
    b = b"".join(_s(1, i) for i in ins) + b"".join(_s(2, o) for o in outs) + _s(3, outs[0] + "_n") + _s(4, op)
    return b + b"".join(_s(5, attr(k, v)) for k, v in attrs.items())


def vinfo(name, shape, et=1):
    dims = b"".join(_s(1, _f(1, 0, d) if isinstance(d, int) else _s(2, d)) for d in shape)
    return _s(1, name) + _s(2, _s(1, _f(1, 0, et) + _s(2, dims)))


def model(nodes, inits, inputs, outputs, opset=13):
    g = b"".join(_s(1, n) for n in nodes) + _s(2, "g") + b"".join(_s(5, t) for t in inits)
    g += b"".join(_s(11, vinfo(*i)) for i in inputs) + b"".join(_s(12, vinfo(*o)) for o in outputs)
    return _f(1, 0, 8) + _s(2, "test") + _s(7, g) + _s(8, _s(1, "") + _f(2, 0, opset))


def save(b, d, name):
    p = os.path.join(d, name)
    with open(p, "wb") as fh:
        fh.write(b)
    return p


try:
    import onnxruntime  # noqa: F401
    HAVE_ORT = True
except Exception:
    HAVE_ORT = False


def both(path, feeds, tol=2e-4):
    m = OL.load(path)
    a = m.run(feeds, backend="numpy")
    if HAVE_ORT:
        b = m.run(feeds, backend="onnxruntime")
        for k in a:
            assert a[k].shape == b[k].shape, (k, a[k].shape, b[k].shape)
            if a[k].dtype.kind == "f":
                err = float(np.max(np.abs(a[k] - b[k]) / (1e-3 + np.abs(b[k]))))
                assert err < tol, (path, k, err)
            else:
                assert np.array_equal(a[k], b[k]), (path, k)
    return a


rng = np.random.default_rng(0)
d = tempfile.mkdtemp()

# 1. basic-pitch-like front end: long 1-D conv (FFT path), magnitude, log, batchnorm, harmonic stack, 2-D convs
L, K = 4000, 513
w1 = rng.standard_normal((8, 1, K)).astype(np.float32) * 0.05
w2 = rng.standard_normal((4, 2, 5, 3)).astype(np.float32)
b2 = rng.standard_normal(4).astype(np.float32)
bn = [rng.random(4).astype(np.float32) + 0.5, rng.standard_normal(4).astype(np.float32),
      rng.standard_normal(4).astype(np.float32), rng.random(4).astype(np.float32) + 0.5]
w3 = rng.standard_normal((4, 1, 3, 3)).astype(np.float32)          # depthwise (group 4)
nodes = [
    node("Transpose", ["x"], ["xt"], perm=[0, 2, 1]),                 # (1, L, 1) -> (1, 1, L) like tf2onnx
    node("Conv", ["xt", "w1"], ["c1"], strides=[32], pads=[K // 2, K // 2]),
    node("Mul", ["c1", "c1"], ["sq"]),
    node("Add", ["sq", "eps"], ["sq2"]),
    node("Sqrt", ["sq2"], ["mag"]),
    node("Log", ["mag"], ["lg"]),
    node("Unsqueeze", ["lg", "ax0"], ["u"]),                          # (1, 1, 8, T)
    node("Slice", ["u", "st", "en", "axs"], ["sh"]),                  # harmonic shift: bins 2.. then pad back
    node("Pad", ["sh", "pads"], ["shp"], mode="constant"),
    node("Concat", ["u", "shp"], ["hs"], axis=1),                     # (1, 2, 8, T)
    node("Conv", ["hs", "w2", "b2"], ["c2"], pads=[2, 1, 2, 1], strides=[1, 1]),
    node("BatchNormalization", ["c2", "g", "b", "m", "v"], ["bn"], epsilon=1e-3),
    node("Relu", ["bn"], ["r"]),
    node("Conv", ["r", "w3"], ["c3"], group=4, auto_pad="SAME_UPPER"),
    node("MaxPool", ["c3"], ["mp"], kernel_shape=[2, 1], strides=[2, 1]),
    node("ReduceMean", ["mp"], ["rm"], axes=[1], keepdims=1),
    node("Sigmoid", ["rm"], ["y"]),
    node("Shape", ["y"], ["ys"]),
]
inits = [tensor("w1", w1), tensor("eps", np.array([1e-6], np.float32)), tensor("ax0", np.array([1], np.int64)),
         tensor("st", np.array([2], np.int64)), tensor("en", np.array([1 << 62], np.int64)),
         tensor("axs", np.array([2], np.int64)), tensor("pads", np.array([0, 0, 0, 0, 0, 0, 2, 0], np.int64)),
         tensor("w2", w2), tensor("b2", b2, raw=False), tensor("g", bn[0]), tensor("b", bn[1]), tensor("m", bn[2]),
         tensor("v", bn[3]), tensor("w3", w3)]
p1 = save(model(nodes, inits, [("x", ["batch", L, 1])], [("y", ["batch", 1, 4, "T"]), ("ys", [4], 7)]), d, "front.onnx")
x = rng.standard_normal((2, L, 1)).astype(np.float32)
out = both(p1, {"x": x})
# closed-form check of the long conv against np.correlate
m1 = OL.load(p1)
r = m1.run({"x": x}, outputs=["c1"], backend="numpy")["c1"]
xp = np.pad(x[0, :, 0].astype(np.float64), K // 2)
ref = np.correlate(xp, w1[3, 0].astype(np.float64), "valid")[::32]
assert r.shape == (2, 8, len(ref)) and np.allclose(r[0, 3], ref, atol=1e-4), "long conv"
assert list(out["ys"]) == list(out["y"].shape)
print("front end:", out["y"].shape, "ort compared" if HAVE_ORT else "ort not installed")

# 2. shape plumbing tf2onnx emits: Shape/Gather/Concat/Reshape, ConstantOfShape, Cast, Split, Where, Gemm, opset-11 style
nodes = [
    node("Shape", ["x"], ["s"]),
    node("Gather", ["s", "i0"], ["n"], axis=0),
    node("Unsqueeze", ["n"], ["n1"], axes=[0]),
    node("Concat", ["n1", "m1"], ["shp"], axis=0),
    node("Reshape", ["x", "shp"], ["flat"]),
    node("Gemm", ["flat", "W", "B"], ["gm"], transB=1, alpha=0.5),
    node("Split", ["gm"], ["a", "b2"], axis=1, split=[3, 2]),
    node("Greater", ["b2", "zero"], ["mask"]),
    node("ConstantOfShape", ["s2"], ["ones"], value=np.array([1.0], np.float32)),
    node("Where", ["mask", "b2", "ones"], ["wh"]),
    node("Cast", ["mask"], ["mi"], to=7),
    node("ReduceSum", ["mi"], ["cnt"], axes=[1], keepdims=0),
    node("Constant", [], ["k"], value_floats=[2.0, 3.0]),
    node("Mul", ["wh", "k"], ["out"]),
    node("Softmax", ["a"], ["sm"], axis=-1),
]
W = rng.standard_normal((5, 12)).astype(np.float32)
inits = [tensor("i0", np.array(0, np.int64)), tensor("m1", np.array([-1], np.int64), raw=False), tensor("W", W),
         tensor("B", np.zeros(5, np.float32)), tensor("zero", np.array(0.0, np.float32)),
         tensor("s2", np.array([3, 2], np.int64))]
p2 = save(model(nodes, inits, [("x", [3, 4, 3])], [("out", [3, 2]), ("cnt", [3], 7), ("sm", [3, 3])], opset=11), d, "plumb.onnx")
xx = rng.standard_normal((3, 4, 3)).astype(np.float32)
o2 = both(p2, {"x": xx})
gm = 0.5 * xx.reshape(3, -1) @ W.T
assert np.allclose(o2["out"], np.where(gm[:, 3:] > 0, gm[:, 3:], 1.0) * [2, 3], atol=1e-5)
assert list(o2["cnt"]) == list((gm[:, 3:] > 0).sum(1))
print("plumbing ok")

# 3. unsupported op is named, never a silent wrong answer
p3 = save(model([node("GridSample", ["x", "x"], ["y"])], [], [("x", [2, 2])], [("y", [2])]), d, "bad.onnx")
m3 = OL.load(p3)
assert m3.unsupported == ["GridSample"]
try:
    m3.run({"x": np.ones((2, 2), np.float32)}, backend="numpy")
    raise AssertionError("should fail")
except OL.OnnxLiteError as e:
    assert "GridSample" in str(e)
try:
    OL.Model(b"\x08\x01")
    raise AssertionError("no graph accepted")
except OL.OnnxLiteError:
    pass

# 3b. ops added for transformer models: Einsum, InstanceNormalization, ConvTranspose (vs onnxruntime), fast Erf
x4 = rng.standard_normal((1, 4, 12)).astype(np.float32)
w4 = rng.standard_normal((4, 3, 8)).astype(np.float32)
nodes = [node("ConvTranspose", ["x", "w", "b"], ["ct"], strides=[8], pads=[0, 0]),
         node("InstanceNormalization", ["ct", "sc", "bi"], ["inorm"], epsilon=1e-5),
         node("Erf", ["inorm"], ["er"]),
         node("Unsqueeze", ["er", "ax0"], ["u"]),
         node("Einsum", ["u", "u"], ["y"], equation="iped, ipbd-> ipeb")]
inits = [tensor("w", w4), tensor("b", rng.standard_normal(3).astype(np.float32)),
         tensor("sc", rng.random(3).astype(np.float32) + 0.5), tensor("bi", rng.standard_normal(3).astype(np.float32)),
         tensor("ax0", np.array([0], np.int64))]
p4 = save(model(nodes, inits, [("x", [1, 4, 12])], [("y", [1, 1, 3, 3])], opset=17), d, "tf.onnx")
o4 = both(p4, {"x": x4})
ct = np.zeros((1, 3, 96), np.float32)
for i in range(12):
    ct[0, :, i * 8:(i + 1) * 8] += np.einsum("c,cok->ok", x4[0, :, i], w4)
print("transformer ops ok")

# 4. the bundled basic-pitch model, when present: parses and every operator is supported
bp = os.path.join(os.path.dirname(HERE), "src", "data", "transcribe", "nmp.onnx")
if os.path.isfile(bp):
    ops, miss = OL.ops_used(bp)
    assert not miss, miss
    print("nmp.onnx ops:", len(ops))
print("ok")
