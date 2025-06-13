import sys, types

class _Dummy(types.ModuleType):
    def __getattr__(self, n):
        full = f"{self.__name__}.{n}"
        if full in sys.modules:
            return sys.modules[full]
        sub = _Dummy(full); sub.__path__ = []; sys.modules[full] = sub
        return sub
    def __call__(self, *a, **kw): return self
    def __bool__(self): return False

# ----- stub numpy / pandas / sklearn / tensorflow -----
for m in ("numpy", "pandas", "sklearn", "tensorflow"):
    sys.modules[m] = _Dummy(m)

# keras.Model giả (đã có)
keras = _Dummy("tensorflow.keras"); keras.__path__ = []
keras.layers = _Dummy("tensorflow.keras.layers"); keras.layers.__path__ = []
class _FakeBase(type): pass
keras.Model = _FakeBase("Model", (), {})
sys.modules["tensorflow.keras"] = keras
sys.modules["tensorflow.keras.layers"] = keras.layers

# ----- tạo module 'sui.ml' với 7 class dummy -----
ml = types.ModuleType("sui.ml"); ml.__path__ = []
for name in ("FunkSVD", "BiasSVD", "SVDpp", "BPR", "ALS", "AFM", "FM"):
    setattr(ml, name, _FakeBase(name, (), {}))
sys.modules["sui.ml"] = ml

# ----- chặn luôn 'sui.dl' (Deep-Learning) -----
sys.modules["sui.dl"] = types.ModuleType("sui.dl")

print("[sitecustomize] keras.Model & FunkSVD stubs ready ✔")
