"""onnx_lite: read an .onnx model and run it with numpy only (no onnx / onnxruntime / protobuf needed).

Covers the operator set small audio models use (Conv 1-D/2-D, BatchNormalization, element-wise math, shape ops,
Pad/Slice/Concat/Gather, reductions, MatMul/Gemm, pooling). When onnxruntime is installed it is used as a speed-up
(`backend="auto"`); results match the numpy path to float32 rounding (tests check both on the same graphs).
Unknown operators raise OnnxLiteError naming them, never a silent wrong answer.

    model = load("model.onnx")            # parse once
    out = model.run({"input": x})         # dict name -> array (float32 math, like onnxruntime on CPU)
"""
import os
import struct

import numpy as np

__all__ = ["load", "Model", "OnnxLiteError", "ops_used", "SUPPORTED"]


class OnnxLiteError(Exception):
    pass


# ---------------------------------------------------------------- protobuf wire format (just what ONNX needs)
def _varint(b, i):
    r = s = 0
    while True:
        c = b[i]
        i += 1
        r |= (c & 0x7F) << s
        if c < 0x80:
            return r, i
        s += 7


def _fields(b):
    """Yield (field_number, wire_type, value) of one message. value: int (varint/fixed) or memoryview (len)."""
    i, n = 0, len(b)
    while i < n:
        key, i = _varint(b, i)
        f, w = key >> 3, key & 7
        if w == 0:
            v, i = _varint(b, i)
        elif w == 2:
            ln, i = _varint(b, i)
            v = b[i:i + ln]
            i += ln
        elif w == 5:
            v = b[i:i + 4]
            i += 4
        elif w == 1:
            v = b[i:i + 8]
            i += 8
        else:
            raise OnnxLiteError(f"unsupported protobuf wire type {w}")
        yield f, w, v


def _signed(v):
    return v - (1 << 64) if v >= 1 << 63 else v


def _ints(w, v, out):
    if w == 2:                                              # packed
        j, n = 0, len(v)
        while j < n:
            x, j = _varint(v, j)
            out.append(_signed(x))
    else:
        out.append(_signed(v))


def _floats(w, v, out):
    if w == 2:
        out.extend(np.frombuffer(bytes(v), "<f4").tolist())
    else:
        out.append(struct.unpack("<f", bytes(v))[0])


_DT = {1: np.float32, 2: np.uint8, 3: np.int8, 4: np.uint16, 5: np.int16, 6: np.int32, 7: np.int64, 9: np.bool_,
       10: np.float16, 11: np.float64, 12: np.uint32, 13: np.uint64}


def _tensor(b, base_dir=None):
    dims, dt, name, raw = [], 1, "", None
    f32, i32, i64, f64, u64, ext = [], [], [], [], [], {}
    for f, w, v in _fields(b):
        if f == 1:
            _ints(w, v, dims)
        elif f == 2:
            dt = v
        elif f == 8:
            name = bytes(v).decode("utf-8", "replace")
        elif f == 9:
            raw = bytes(v)
        elif f == 4:
            _floats(w, v, f32)
        elif f in (5,):
            _ints(w, v, i32)
        elif f == 7:
            _ints(w, v, i64)
        elif f == 10:
            if w == 2:
                f64.extend(np.frombuffer(bytes(v), "<f8").tolist())
            else:
                f64.append(struct.unpack("<d", bytes(v))[0])
        elif f == 11:
            _ints(w, v, u64)
        elif f == 13:                                       # external data: StringStringEntryProto key/value
            k = val = ""
            for f2, _, v2 in _fields(v):
                if f2 == 1:
                    k = bytes(v2).decode()
                elif f2 == 2:
                    val = bytes(v2).decode()
            ext[k] = val
    if dt not in _DT:
        raise OnnxLiteError(f"tensor {name!r}: data type {dt} not supported")
    t = _DT[dt]
    if ext:
        if not base_dir:
            raise OnnxLiteError(f"tensor {name!r} uses external data; load the model from its file")
        p = os.path.normpath(os.path.join(base_dir, ext.get("location", "")))
        if os.path.dirname(p) != os.path.normpath(base_dir):
            raise OnnxLiteError(f"tensor {name!r}: external data outside the model's folder")
        with open(p, "rb") as fh:
            fh.seek(int(ext.get("offset", 0)))
            ln = ext.get("length")
            raw = fh.read(int(ln)) if ln else fh.read()
    if raw is not None:
        a = np.frombuffer(raw, dtype=np.dtype(t).newbyteorder("<")).astype(t)
    elif dt in (1,):
        a = np.array(f32, t)
    elif dt == 11:
        a = np.array(f64, t)
    elif dt == 7:
        a = np.array(i64, t)
    elif dt == 13 or dt == 12:
        a = np.array(u64, t)
    elif dt == 10:                                          # fp16 stored as uint16 bits in int32_data
        a = np.array(i32, np.uint16).view(np.float16)
    else:
        a = np.array(i32, t)
    n = int(np.prod(dims)) if dims else 1
    if a.size != n:
        if a.size == 0 and n == 0:
            pass
        else:
            raise OnnxLiteError(f"tensor {name!r}: {a.size} values for shape {dims}")
    return name, a.reshape(dims)


def _attribute(b, base_dir):
    name, val, typ = "", None, 0
    fl, it, st, ts = [], [], [], []
    have = {}
    for f, w, v in _fields(b):
        if f == 1:
            name = bytes(v).decode()
        elif f == 20:
            typ = v
        elif f == 2:
            have["f"] = struct.unpack("<f", bytes(v))[0]
        elif f == 3:
            have["i"] = _signed(v)
        elif f == 4:
            have["s"] = bytes(v)
        elif f == 5:
            have["t"] = _tensor(v, base_dir)[1]
        elif f == 6:
            have["g"] = _graph(v, base_dir)
        elif f == 7:
            _floats(w, v, fl)
        elif f == 8:
            _ints(w, v, it)
        elif f == 9:
            st.append(bytes(v))
        elif f == 10:
            ts.append(_tensor(v, base_dir)[1])
    by_type = {1: "f", 2: "i", 3: "s", 4: "t", 5: "g", 6: fl, 7: it, 8: st, 9: ts}
    k = by_type.get(typ)
    if isinstance(k, str):
        val = have.get(k)
    elif k is not None:
        val = list(k)
    else:                                                   # no type field (old writers): take what is there
        val = next(iter(have.values()), None) if have else (fl or it or st or ts or [])
    return name, val


class _Node:
    __slots__ = ("op", "inputs", "outputs", "attrs", "name", "domain")


def _node(b, base_dir):
    n = _Node()
    n.inputs, n.outputs, n.attrs, n.name, n.op, n.domain = [], [], {}, "", "", ""
    for f, w, v in _fields(b):
        if f == 1:
            n.inputs.append(bytes(v).decode())
        elif f == 2:
            n.outputs.append(bytes(v).decode())
        elif f == 3:
            n.name = bytes(v).decode()
        elif f == 4:
            n.op = bytes(v).decode()
        elif f == 5:
            k, val = _attribute(v, base_dir)
            n.attrs[k] = val
        elif f == 7:
            n.domain = bytes(v).decode()
    return n


def _value_info(b):
    name, shape, et = "", [], None
    for f, w, v in _fields(b):
        if f == 1:
            name = bytes(v).decode()
        elif f == 2:
            for f2, _, v2 in _fields(v):
                if f2 == 1:                                 # tensor_type
                    for f3, _, v3 in _fields(v2):
                        if f3 == 1:
                            et = v3
                        elif f3 == 2:
                            for f4, _, v4 in _fields(v3):
                                if f4 == 1:
                                    d = None
                                    for f5, _, v5 in _fields(v4):
                                        if f5 == 1:
                                            d = _signed(v5)
                                        elif f5 == 2:
                                            d = bytes(v5).decode()
                                    shape.append(d)
    return name, shape, et


def _graph(b, base_dir):
    g = {"nodes": [], "init": {}, "inputs": [], "outputs": [], "name": ""}
    for f, w, v in _fields(b):
        if f == 1:
            g["nodes"].append(_node(v, base_dir))
        elif f == 2:
            g["name"] = bytes(v).decode()
        elif f == 5:
            k, a = _tensor(v, base_dir)
            g["init"][k] = a
        elif f == 11:
            g["inputs"].append(_value_info(v))
        elif f == 12:
            g["outputs"].append(_value_info(v))
    return g


# ---------------------------------------------------------------- operators
def _bc_np(x):
    return np.asarray(x)


def _axes_arg(node, ins, idx=1, attr="axes"):
    if len(ins) > idx and ins[idx] is not None:
        return [int(a) for a in np.asarray(ins[idx]).ravel()]
    a = node.attrs.get(attr)
    return None if a is None else [int(v) for v in a]


def _norm_axes(axes, nd):
    return [a + nd if a < 0 else a for a in axes]


def _pads_split(p, nd):
    p = [int(v) for v in p]
    return p[:nd], p[nd:]


def _auto_pad(mode, in_sz, k, s, d):
    """Same as onnxruntime for SAME_UPPER / SAME_LOWER / VALID -> (begin, end) per spatial dim."""
    b, e = [], []
    for n, kk, ss, dd in zip(in_sz, k, s, d):
        if mode == "VALID":
            b.append(0)
            e.append(0)
            continue
        out = -(-n // ss)
        tot = max(0, (out - 1) * ss + (kk - 1) * dd + 1 - n)
        lo = tot // 2 if mode == "SAME_UPPER" else tot - tot // 2
        b.append(lo)
        e.append(tot - lo)
    return b, e


def _conv_pads(node, x, w_shape):
    nd = x.ndim - 2
    k = list(node.attrs.get("kernel_shape") or w_shape[2:])
    s = list(node.attrs.get("strides") or [1] * nd)
    d = list(node.attrs.get("dilations") or [1] * nd)
    ap = node.attrs.get("auto_pad", b"NOTSET")
    ap = ap.decode() if isinstance(ap, bytes) else (ap or "NOTSET")
    if ap not in ("NOTSET", ""):
        b, e = _auto_pad(ap, x.shape[2:], k, s, d)
    else:
        b, e = _pads_split(node.attrs.get("pads") or [0] * (2 * nd), nd)
    return k, s, d, b, e


def _patches(x, k, s, d):
    """x (N, C, *sp) already padded -> view (N, C, *out, *k)."""
    nd = len(k)
    sp = x.shape[2:]
    out = [(sp[i] - (k[i] - 1) * d[i] - 1) // s[i] + 1 for i in range(nd)]
    if min(out) <= 0:
        raise OnnxLiteError("Conv/Pool: kernel larger than the padded input")
    st = x.strides
    shape = x.shape[:2] + tuple(out) + tuple(k)
    strides = st[:2] + tuple(st[2 + i] * s[i] for i in range(nd)) + tuple(st[2 + i] * d[i] for i in range(nd))
    return np.lib.stride_tricks.as_strided(x, shape, strides, writeable=False), out


def _conv(node, ins):
    x, w = ins[0], ins[1]
    bias = ins[2] if len(ins) > 2 and ins[2] is not None else None
    k, s, d, pb, pe = _conv_pads(node, x, w.shape)
    g = int(node.attrs.get("group", 1))
    nd = x.ndim - 2
    xp = np.pad(x, [(0, 0), (0, 0)] + list(zip(pb, pe))) if any(pb) or any(pe) else x
    N, C = xp.shape[:2]
    M = w.shape[0]
    cg, mg = C // g, M // g
    # long 1-D kernels (filter banks, CQT): FFT correlation is far cheaper than patches
    if nd == 1 and k[0] >= 128 and s[0] >= 1 and d[0] == 1:
        L = xp.shape[2]
        outn = (L - k[0]) // s[0] + 1
        nfft = 1 << int(np.ceil(np.log2(L + k[0])))
        X = np.fft.rfft(xp.astype(np.float64), nfft, axis=2)                     # (N, C, F)
        Wf = np.fft.rfft(w[:, :, ::-1].astype(np.float64), nfft, axis=2)          # (M, cg, F)
        y = np.empty((N, M, outn), np.float64)
        for gi in range(g):
            Xg = X[:, gi * cg:(gi + 1) * cg]                                       # (N, cg, F)
            Wg = Wf[gi * mg:(gi + 1) * mg]                                         # (mg, cg, F)
            Y = np.einsum("ncf,mcf->nmf", Xg, Wg)
            full = np.fft.irfft(Y, nfft, axis=2)
            y[:, gi * mg:(gi + 1) * mg] = full[:, :, k[0] - 1:k[0] - 1 + (outn - 1) * s[0] + 1:s[0]]
        y = y.astype(np.float32)
    else:
        P, out = _patches(np.ascontiguousarray(xp), k, s, d)                     # (N, C, *out, *k)
        ys = []
        kax = tuple(range(2 + nd, 2 + 2 * nd))
        for gi in range(g):
            Pg = P[:, gi * cg:(gi + 1) * cg]
            Wg = w[gi * mg:(gi + 1) * mg]                                          # (mg, cg, *k)
            if g > 1 and cg == 1 and mg == 1:                                      # depthwise
                yg = np.tensordot(Pg[:, 0], Wg[0, 0], axes=(tuple(range(1 + nd, 1 + 2 * nd)),
                                                            tuple(range(nd))))[:, None]
            else:
                yg = np.tensordot(Pg, Wg, axes=((1,) + kax, tuple(range(1, 2 + nd))))  # (N, *out, mg)
                yg = np.moveaxis(yg, -1, 1)
            ys.append(yg)
        y = np.concatenate(ys, axis=1) if g > 1 else ys[0]
        y = y.astype(np.float32, copy=False)
    if bias is not None:
        y = y + bias.reshape((1, -1) + (1,) * nd)
    return [y.astype(x.dtype, copy=False)]


def _pool(node, ins, kind):
    x = ins[0]
    nd = x.ndim - 2
    k = list(node.attrs["kernel_shape"])
    s = list(node.attrs.get("strides") or [1] * nd)
    d = list(node.attrs.get("dilations") or [1] * nd)
    ap = node.attrs.get("auto_pad", b"NOTSET")
    ap = ap.decode() if isinstance(ap, bytes) else ap
    if int(node.attrs.get("ceil_mode", 0)):
        raise OnnxLiteError(f"{node.op}: ceil_mode=1 not supported")
    if ap not in ("NOTSET", ""):
        pb, pe = _auto_pad(ap, x.shape[2:], k, s, d)
    else:
        pb, pe = _pads_split(node.attrs.get("pads") or [0] * (2 * nd), nd)
    fill = -np.inf if kind == "max" else 0.0
    xp = np.pad(x, [(0, 0), (0, 0)] + list(zip(pb, pe)), constant_values=fill) if any(pb) or any(pe) else x
    P, out = _patches(np.ascontiguousarray(xp), k, s, d)
    kax = tuple(range(2 + nd, 2 + 2 * nd))
    if kind == "max":
        return [P.max(axis=kax)]
    if int(node.attrs.get("count_include_pad", 0)) or not (any(pb) or any(pe)):
        return [P.mean(axis=kax).astype(x.dtype)]
    ones = np.pad(np.ones((1, 1) + x.shape[2:], x.dtype), [(0, 0), (0, 0)] + list(zip(pb, pe)))
    Q, _ = _patches(np.ascontiguousarray(ones), k, s, d)
    return [(P.sum(axis=kax) / Q.sum(axis=kax)).astype(x.dtype)]


def _batchnorm(node, ins):
    x, sc, b, mean, var = ins[:5]
    eps = float(node.attrs.get("epsilon", 1e-5))
    sh = (1, -1) + (1,) * (x.ndim - 2)
    inv = (sc / np.sqrt(var + np.float32(eps))).astype(x.dtype)
    return [(x - mean.reshape(sh)) * inv.reshape(sh) + b.reshape(sh)]


def _reshape(node, ins):
    x, shp = ins[0], [int(v) for v in np.asarray(ins[1]).ravel()]
    allowzero = int(node.attrs.get("allowzero", 0))
    if not allowzero:
        shp = [x.shape[i] if v == 0 else v for i, v in enumerate(shp)]
    return [x.reshape(shp)]


def _slice(node, ins):
    x = ins[0]
    if len(ins) >= 3:
        st = [int(v) for v in np.asarray(ins[1]).ravel()]
        en = [int(v) for v in np.asarray(ins[2]).ravel()]
        ax = [int(v) for v in np.asarray(ins[3]).ravel()] if len(ins) > 3 and ins[3] is not None else list(range(len(st)))
        sp = [int(v) for v in np.asarray(ins[4]).ravel()] if len(ins) > 4 and ins[4] is not None else [1] * len(st)
    else:                                                   # opset < 10
        st, en = list(node.attrs["starts"]), list(node.attrs["ends"])
        ax = list(node.attrs.get("axes") or range(len(st)))
        sp = [1] * len(st)
    sl = [slice(None)] * x.ndim
    for a, s0, e0, s1 in zip(_norm_axes(ax, x.ndim), st, en, sp):
        n = x.shape[a]
        big = 1 << 62
        if s1 > 0:
            s0 = max(0, min(n, s0 + n if s0 < 0 else s0))
            e0 = n if e0 >= big else max(0, min(n, e0 + n if e0 < 0 else e0))
            sl[a] = slice(s0, e0, s1)
        else:
            s0 = max(-1, min(n - 1, s0 + n if s0 < 0 else s0))
            e0 = -1 if e0 <= -big else max(-1, min(n - 1, e0 + n if e0 < 0 else e0))
            sl[a] = slice(s0, None if e0 < 0 else e0, s1)
    return [x[tuple(sl)]]


def _pad(node, ins):
    x = ins[0]
    mode = node.attrs.get("mode", b"constant")
    mode = mode.decode() if isinstance(mode, bytes) else mode
    if len(ins) > 1 and ins[1] is not None:
        pads = [int(v) for v in np.asarray(ins[1]).ravel()]
        cv = np.asarray(ins[2]).ravel()[0] if len(ins) > 2 and ins[2] is not None and np.size(ins[2]) else 0
        axes = [int(v) for v in np.asarray(ins[3]).ravel()] if len(ins) > 3 and ins[3] is not None else None
    else:
        pads, cv, axes = list(node.attrs["pads"]), node.attrs.get("value", 0.0), None
    nd = x.ndim
    axes = _norm_axes(axes, nd) if axes is not None else list(range(nd))
    na = len(axes)
    pw = [(0, 0)] * nd
    for j, a in enumerate(axes):
        pw[a] = (pads[j], pads[j + na])
    neg = any(p < 0 for pr in pw for p in pr)
    if neg:                                                 # negative pads crop
        sl = tuple(slice(-b if b < 0 else 0, x.shape[i] + e if e < 0 else None) for i, (b, e) in enumerate(pw))
        x = x[sl]
        pw = [(max(b, 0), max(e, 0)) for b, e in pw]
    if mode == "constant":
        return [np.pad(x, pw, mode="constant", constant_values=cv)]
    if mode == "reflect":
        return [np.pad(x, pw, mode="reflect")]
    if mode == "edge":
        return [np.pad(x, pw, mode="edge")]
    if mode == "wrap":
        return [np.pad(x, pw, mode="wrap")]
    raise OnnxLiteError(f"Pad mode {mode!r} not supported")


def _reduce(fn):
    def r(node, ins):
        x = ins[0]
        axes = _axes_arg(node, ins)
        keep = bool(int(node.attrs.get("keepdims", 1)))
        if not axes:
            if int(node.attrs.get("noop_with_empty_axes", 0)) and axes is not None:
                return [x]
            ax = None
        else:
            ax = tuple(_norm_axes(axes, x.ndim))
        return [np.asarray(fn(x, axis=ax, keepdims=keep), dtype=x.dtype)]
    return r


def _split(node, ins):
    x = ins[0]
    ax = int(node.attrs.get("axis", 0))
    ax = ax + x.ndim if ax < 0 else ax
    if len(ins) > 1 and ins[1] is not None:
        parts = [int(v) for v in np.asarray(ins[1]).ravel()]
    elif node.attrs.get("split"):
        parts = list(node.attrs["split"])
    else:
        n = int(node.attrs.get("num_outputs", len(node.outputs)))
        size = -(-x.shape[ax] // n)
        parts = [size] * (n - 1) + [x.shape[ax] - size * (n - 1)]
    idx = np.cumsum(parts)[:-1]
    return np.split(x, idx, axis=ax)


def _gather(node, ins):
    x, i = ins[0], np.asarray(ins[1]).astype(np.int64)
    ax = int(node.attrs.get("axis", 0))
    ax = ax + x.ndim if ax < 0 else ax
    i = np.where(i < 0, i + x.shape[ax], i)
    return [np.take(x, i, axis=ax)]


def _constant(node, ins):
    a = node.attrs
    if "value" in a and a["value"] is not None:
        return [a["value"]]
    if "value_float" in a:
        return [np.array(a["value_float"], np.float32)]
    if "value_floats" in a:
        return [np.array(a["value_floats"], np.float32)]
    if "value_int" in a:
        return [np.array(a["value_int"], np.int64)]
    if "value_ints" in a:
        return [np.array(a["value_ints"], np.int64)]
    raise OnnxLiteError("Constant: no value")


def _cast(node, ins):
    to = int(node.attrs["to"])
    if to not in _DT:
        raise OnnxLiteError(f"Cast to type {to} not supported")
    return [ins[0].astype(_DT[to])]


def _div(node, ins):
    a, b = ins
    if np.issubdtype(a.dtype, np.integer):
        return [np.trunc(a / b).astype(a.dtype)]            # ONNX integer division truncates
    return [a / b]


def _mod(node, ins):
    a, b = ins
    if int(node.attrs.get("fmod", 0)):
        return [np.fmod(a, b)]
    return [np.mod(a, b)]


def _clip(node, ins):
    x = ins[0]
    lo = ins[1] if len(ins) > 1 and ins[1] is not None and np.size(ins[1]) else node.attrs.get("min")
    hi = ins[2] if len(ins) > 2 and ins[2] is not None and np.size(ins[2]) else node.attrs.get("max")
    if lo is not None:
        x = np.maximum(x, np.asarray(lo, x.dtype))
    if hi is not None:
        x = np.minimum(x, np.asarray(hi, x.dtype))
    return [x]


def _softmax(node, ins, log=False):
    x = ins[0]
    ax = int(node.attrs.get("axis", -1))
    m = x - x.max(axis=ax, keepdims=True)
    e = np.exp(m)
    s = e.sum(axis=ax, keepdims=True)
    return [(m - np.log(s)) if log else e / s]


def _gemm(node, ins):
    a, b = ins[0], ins[1]
    c = ins[2] if len(ins) > 2 else None
    if int(node.attrs.get("transA", 0)):
        a = a.T
    if int(node.attrs.get("transB", 0)):
        b = b.T
    y = np.float32(node.attrs.get("alpha", 1.0)) * (a @ b)
    if c is not None:
        y = y + np.float32(node.attrs.get("beta", 1.0)) * c
    return [y.astype(ins[0].dtype)]


def _unsqueeze(node, ins):
    x = ins[0]
    axes = _axes_arg(node, ins)
    nd = x.ndim + len(axes)
    for a in sorted(_norm_axes(axes, nd)):
        x = np.expand_dims(x, a)
    return [x]


def _squeeze(node, ins):
    x = ins[0]
    axes = _axes_arg(node, ins)
    if axes is None:
        return [np.squeeze(x)]
    return [np.squeeze(x, axis=tuple(_norm_axes(axes, x.ndim)))]


def _shape(node, ins):
    s = np.array(ins[0].shape, np.int64)
    a, b = node.attrs.get("start"), node.attrs.get("end")
    return [s[a:b]] if a is not None or b is not None else [s]


def _range(node, ins):
    s, l, d = (np.asarray(v).ravel()[0] for v in ins[:3])
    return [np.arange(s, l, d, dtype=np.asarray(ins[0]).dtype)]


def _expand(node, ins):
    shp = [int(v) for v in np.asarray(ins[1]).ravel()]
    out = np.broadcast_shapes(ins[0].shape, tuple(shp))
    return [np.broadcast_to(ins[0], out).copy()]


def _transpose(node, ins):
    p = node.attrs.get("perm")
    return [np.transpose(ins[0], p if p else None)]


def _concat(node, ins):
    ax = int(node.attrs["axis"])
    return [np.concatenate([v for v in ins if v is not None and (v.size or v.ndim > 1)], axis=ax)]


def _flatten(node, ins):
    x = ins[0]
    ax = int(node.attrs.get("axis", 1))
    ax = ax + x.ndim if ax < 0 else ax
    return [x.reshape(int(np.prod(x.shape[:ax])), -1)]


def _cumsum(node, ins):
    x = ins[0]
    ax = int(np.asarray(ins[1]).ravel()[0])
    if int(node.attrs.get("reverse", 0)):
        y = np.flip(np.cumsum(np.flip(x, ax), axis=ax), ax)
    else:
        y = np.cumsum(x, axis=ax)
    if int(node.attrs.get("exclusive", 0)):
        y = y - x
    return [y.astype(x.dtype)]


def _leaky(node, ins):
    a = np.float32(node.attrs.get("alpha", 0.01))
    x = ins[0]
    return [np.where(x >= 0, x, a * x).astype(x.dtype)]


def _elu(node, ins):
    a = np.float32(node.attrs.get("alpha", 1.0))
    x = ins[0]
    return [np.where(x >= 0, x, a * (np.exp(x) - 1)).astype(x.dtype)]


def _hardsig(node, ins):
    a, b = np.float32(node.attrs.get("alpha", 0.2)), np.float32(node.attrs.get("beta", 0.5))
    return [np.clip(a * ins[0] + b, 0, 1).astype(ins[0].dtype)]


try:                                                            # optional speed-up, same values
    from scipy.special import erf as _sp_erf
except Exception:
    _sp_erf = None


def _erf(x):
    """erf for GELU. scipy's when installed; otherwise Abramowitz-Stegun 7.1.26 in float32 (|error| < 1.5e-7,
    below float32 resolution for these activations)."""
    if _sp_erf is not None:
        return _sp_erf(x).astype(x.dtype, copy=False)
    z = np.abs(x).astype(np.float32)
    t = np.float32(1.0) / (np.float32(1.0) + np.float32(0.3275911) * z)
    y = ((((np.float32(1.061405429) * t - np.float32(1.453152027)) * t + np.float32(1.421413741)) * t
          - np.float32(0.284496736)) * t + np.float32(0.254829592)) * t
    r = np.float32(1.0) - y * np.exp(-z * z)
    return np.copysign(r, x).astype(x.dtype, copy=False)


def _instnorm(node, ins):
    x, sc, b = ins
    eps = np.float32(node.attrs.get("epsilon", 1e-5))
    ax = tuple(range(2, x.ndim))
    m = x.mean(axis=ax, keepdims=True)
    v = ((x - m) ** 2).mean(axis=ax, keepdims=True)
    sh = (1, -1) + (1,) * (x.ndim - 2)
    return [((x - m) / np.sqrt(v + eps) * sc.reshape(sh) + b.reshape(sh)).astype(x.dtype)]


def _convtranspose(node, ins):
    """N-D transposed convolution (groups=1, no output_shape): out[o, i*s + k*d - pad] += x[c, i] * w[c, o, k]."""
    x, w = ins[0], ins[1]
    bias = ins[2] if len(ins) > 2 and ins[2] is not None else None
    if int(node.attrs.get("group", 1)) != 1 or node.attrs.get("output_shape"):
        raise OnnxLiteError("ConvTranspose: only group=1 without output_shape is supported")
    nd = x.ndim - 2
    k = list(w.shape[2:])
    s = list(node.attrs.get("strides") or [1] * nd)
    d = list(node.attrs.get("dilations") or [1] * nd)
    op = list(node.attrs.get("output_padding") or [0] * nd)
    pb, pe = _pads_split(node.attrs.get("pads") or [0] * (2 * nd), nd)
    N, C = x.shape[:2]
    M = w.shape[1]
    insz = x.shape[2:]
    full = [(insz[i] - 1) * s[i] + (k[i] - 1) * d[i] + 1 + op[i] for i in range(nd)]
    y = np.zeros((N, M) + tuple(full), np.float32)
    for kk in np.ndindex(*k):
        contrib = np.tensordot(x, w[(slice(None), slice(None)) + kk], axes=([1], [0]))   # (N, *in, M)
        contrib = np.moveaxis(contrib, -1, 1)
        sl = (slice(None), slice(None)) + tuple(slice(kk[i] * d[i], kk[i] * d[i] + (insz[i] - 1) * s[i] + 1, s[i])
                                                for i in range(nd))
        y[sl] += contrib
    crop = (slice(None), slice(None)) + tuple(slice(pb[i], full[i] - pe[i]) for i in range(nd))
    y = y[crop]
    if bias is not None:
        y = y + bias.reshape((1, -1) + (1,) * nd)
    return [y.astype(x.dtype)]


def _einsum(node, ins):
    eq = node.attrs["equation"]
    eq = (eq.decode() if isinstance(eq, bytes) else eq).replace(" ", "")
    return [np.einsum(eq, *ins, optimize=True).astype(ins[0].dtype)]


def _tile(node, ins):
    return [np.tile(ins[0], [int(v) for v in np.asarray(ins[1]).ravel()])]


def _cos(node, ins):
    return [np.cos(ins[0])]


def _u(fn):
    return lambda node, ins: [np.asarray(fn(ins[0]), dtype=ins[0].dtype)]


def _b(fn):
    return lambda node, ins: [fn(ins[0], ins[1])]


def _var(fn):
    def r(node, ins):
        y = ins[0]
        for v in ins[1:]:
            y = fn(y, v)
        return [y]
    return r


def _sig(x):
    return (1.0 / (1.0 + np.exp(-x.astype(np.float64)))).astype(x.dtype)


OPS = {
    "Conv": _conv, "BatchNormalization": _batchnorm, "InstanceNormalization": _instnorm,
    "ConvTranspose": _convtranspose, "Einsum": _einsum, "MaxPool": lambda n, i: _pool(n, i, "max"),
    "AveragePool": lambda n, i: _pool(n, i, "avg"),
    "GlobalAveragePool": lambda n, i: [i[0].mean(axis=tuple(range(2, i[0].ndim)), keepdims=True)],
    "GlobalMaxPool": lambda n, i: [i[0].max(axis=tuple(range(2, i[0].ndim)), keepdims=True)],
    "Relu": _u(lambda x: np.maximum(x, 0)), "Sigmoid": lambda n, i: [_sig(i[0])],
    "Tanh": _u(np.tanh), "Exp": _u(np.exp), "Log": _u(np.log), "Sqrt": _u(np.sqrt), "Abs": _u(np.abs),
    "Neg": _u(np.negative), "Reciprocal": _u(lambda x: 1.0 / x), "Floor": _u(np.floor), "Ceil": _u(np.ceil),
    "Round": _u(np.round), "Sign": _u(np.sign), "Sin": _u(np.sin), "Cos": _cos, "Not": _u(np.logical_not),
    "Erf": lambda n, i: [_erf(i[0])], "Softplus": _u(lambda x: np.logaddexp(0, x)),
    "Identity": lambda n, i: [i[0]], "Dropout": lambda n, i: [i[0]] + ([np.ones(i[0].shape, bool)] if len(n.outputs) > 1 else []),
    "LeakyRelu": _leaky, "Elu": _elu, "HardSigmoid": _hardsig, "Clip": _clip, "Cast": _cast,
    "Softmax": _softmax, "LogSoftmax": lambda n, i: _softmax(n, i, True),
    "Add": _b(np.add), "Sub": _b(np.subtract), "Mul": _b(np.multiply), "Div": _div, "Pow": _b(lambda a, b: np.power(a, b).astype(a.dtype)),
    "Mod": _mod, "Equal": _b(np.equal), "Less": _b(np.less), "Greater": _b(np.greater),
    "LessOrEqual": _b(np.less_equal), "GreaterOrEqual": _b(np.greater_equal), "And": _b(np.logical_and),
    "Or": _b(np.logical_or), "Xor": _b(np.logical_xor),
    "Where": lambda n, i: [np.where(i[0], i[1], i[2])],
    "Max": _var(np.maximum), "Min": _var(np.minimum), "Sum": _var(np.add),
    "Mean": lambda n, i: [sum(i[1:], i[0]) / np.asarray(len(i), i[0].dtype)],
    "MatMul": _b(np.matmul), "Gemm": _gemm,
    "Shape": _shape, "Size": lambda n, i: [np.array(i[0].size, np.int64)], "Reshape": _reshape,
    "Transpose": _transpose, "Squeeze": _squeeze, "Unsqueeze": _unsqueeze, "Concat": _concat, "Split": _split,
    "Slice": _slice, "Gather": _gather, "Flatten": _flatten, "Expand": _expand, "Tile": _tile, "Pad": _pad,
    "Constant": _constant, "Range": _range, "CumSum": _cumsum,
    "ConstantOfShape": lambda n, i: [np.full([int(v) for v in np.asarray(i[0]).ravel()],
                                             (n.attrs.get("value") if n.attrs.get("value") is not None
                                              else np.zeros(1, np.float32)).ravel()[0])],
    "ReduceMean": _reduce(np.mean), "ReduceSum": _reduce(np.sum), "ReduceMax": _reduce(np.max),
    "ReduceMin": _reduce(np.min), "ReduceProd": _reduce(np.prod),
    "ReduceL2": _reduce(lambda x, axis, keepdims: np.sqrt(np.sum(x * x, axis=axis, keepdims=keepdims))),
    "ReduceSumSquare": _reduce(lambda x, axis, keepdims: np.sum(x * x, axis=axis, keepdims=keepdims)),
    "ArgMax": lambda n, i: [np.expand_dims(np.argmax(i[0], axis=int(n.attrs.get("axis", 0))),
                                           int(n.attrs.get("axis", 0))) if int(n.attrs.get("keepdims", 1))
                            else np.argmax(i[0], axis=int(n.attrs.get("axis", 0)))],
    "NonZero": lambda n, i: [np.array(np.nonzero(i[0]), np.int64)],
}
SUPPORTED = sorted(OPS)


# ---------------------------------------------------------------- model
class Model:
    def __init__(self, data, path=None):
        base_dir = os.path.dirname(os.path.abspath(path)) if path else None
        graph, self.opset, self.ir_version, self.producer = None, {}, 0, ""
        for f, w, v in _fields(memoryview(data)):
            if f == 1:
                self.ir_version = v
            elif f == 2:
                self.producer = bytes(v).decode("utf-8", "replace")
            elif f == 7:
                graph = _graph(v, base_dir)
            elif f == 8:
                dom, ver = "", 0
                for f2, _, v2 in _fields(v):
                    if f2 == 1:
                        dom = bytes(v2).decode()
                    elif f2 == 2:
                        ver = v2
                self.opset[dom] = ver
        if graph is None:
            raise OnnxLiteError("not an ONNX model (no graph)")
        self.path = path
        self.graph = graph
        self.init = graph["init"]
        self.inputs = [(n, s) for n, s, _ in graph["inputs"] if n not in self.init]
        self.outputs = [n for n, _, _ in graph["outputs"]]
        self.nodes = graph["nodes"]
        missing = sorted({n.op for n in self.nodes if n.op not in OPS or n.domain not in ("", "ai.onnx")})
        self.unsupported = missing
        last = {}
        for i, n in enumerate(self.nodes):
            for x in n.inputs:
                last[x] = i
        self._last = last
        self._ort = None

    def run(self, feeds, outputs=None, backend="auto"):
        """feeds: {input name: array}. backend auto (onnxruntime if installed, else numpy) | numpy | onnxruntime."""
        want = list(outputs or self.outputs)
        if backend in ("auto", "onnxruntime"):
            sess = self._session(backend == "onnxruntime")
            if sess is not None:
                res = sess.run(want, {k: np.asarray(v) for k, v in feeds.items()})
                return dict(zip(want, res))
        if self.unsupported:
            raise OnnxLiteError("operators not supported by the numpy runner: " + ", ".join(self.unsupported))
        env = dict(self.init)
        for k, v in feeds.items():
            env[k] = np.asarray(v)
        keep = set(want)
        for i, n in enumerate(self.nodes):
            ins = [env.get(x) if x else None for x in n.inputs]
            for x, v in zip(n.inputs, ins):
                if x and v is None:
                    raise OnnxLiteError(f"node {n.name or n.op}: input {x!r} has no value")
            try:
                outs = OPS[n.op](n, ins)
            except OnnxLiteError:
                raise
            except Exception as e:
                raise OnnxLiteError(f"node {n.name or n.op} ({n.op}): {type(e).__name__}: {e}") from e
            for name, val in zip(n.outputs, outs):
                if name:
                    env[name] = np.asarray(val)
            for x in n.inputs:                               # free what nothing later reads
                if x and self._last.get(x) == i and x not in keep and x not in self.init:
                    env.pop(x, None)
        return {k: env[k] for k in want}

    def _session(self, required):
        if self._ort is False:
            if required:
                raise OnnxLiteError("onnxruntime is not installed")
            return None
        if self._ort is None:
            try:
                import onnxruntime as ort                  # optional speed-up
                so = ort.SessionOptions()
                so.log_severity_level = 3
                src = self.path if self.path else None
                self._ort = ort.InferenceSession(src, so, providers=["CPUExecutionProvider"]) if src else False
            except Exception:
                self._ort = False
                if required:
                    raise OnnxLiteError("onnxruntime is not installed or can't load this model")
                return None
        return self._ort or None


_CACHE = {}


def load(path):
    """Parse an .onnx file once per (path, mtime)."""
    p = os.path.abspath(path)
    key = (p, os.path.getmtime(p))
    m = _CACHE.get(key)
    if m is None:
        with open(p, "rb") as fh:
            m = Model(fh.read(), p)
        _CACHE.clear()
        _CACHE[key] = m
    return m


def ops_used(path):
    m = load(path)
    from collections import Counter
    return dict(Counter(n.op for n in m.nodes)), m.unsupported
