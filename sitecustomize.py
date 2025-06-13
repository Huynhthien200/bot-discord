# ============================================================
#  Lightweight ML/DL stub  –  dành cho Render / Replit free
#  • Chặn numpy, pandas, sklearn, tensorflow & nhánh keras
#  • Chặn toàn bộ sui.ml.* (FunkSVD, ALS, …)   +  sui.dl.PNN
#  • Tạo class giả để các thư viện vẫn kế thừa được
# ============================================================

import sys, types

# ---------- 1. Helper Dummy module ----------
class _Dummy(types.ModuleType):
    """Trả về sub-module Dummy khi truy cập thuộc tính."""
    def __getattr__(self, name):
        full = f"{self.__name__}.{name}"
        if full in sys.modules:
            return sys.modules[full]
        sub = _Dummy(full); sub.__path__ = []          # coi như package
        sys.modules[full] = sub
        return sub
    # cho phép gọi / lặp / bool mà không lỗi
    def __call__(self, *a, **kw): return self
    def __iter__(self):   return iter(())
    def __len__(self):    return 0
    def __bool__(self):   return False

# ---------- 2. Stub các gói ML nặng ----------
for pkg in ("numpy", "pandas", "sklearn", "tensorflow"):
    root = _Dummy(pkg); root.__path__ = []
    sys.modules[pkg] = root

# ---------- 3. Stub nhánh TensorFlow phổ biến ----------
keras_stub        = _Dummy("tensorflow.keras");        keras_stub.__path__ = []
keras_layers_stub = _Dummy("tensorflow.keras.layers"); keras_layers_stub.__path__ = []
sys.modules["tensorflow.keras"]        = keras_stub
sys.modules["tensorflow.keras.layers"] = keras_layers_stub
sys.modules.setdefault("tensorflow.python", _Dummy("tensorflow.python"))

# tạo lớp Model giả để có thể kế thừa
class _FakeBase(type):
    """Metaclass trống: cho phép class X(_FakeBase) kế thừa mà không lỗi."""
    def __new__(mcls, name, bases, ns): return super().__new__(mcls, name, (), ns)
keras_stub.Model = _FakeBase("Model", (), {})

# ---------- 4. Stub sui.ml  (FunkSVD, …) ----------
ml_stub = types.ModuleType("sui.ml"); ml_stub.__path__ = []
for _cls in ("FunkSVD", "BiasSVD", "SVDpp", "BPR", "ALS", "AFM", "FM"):
    setattr(ml_stub, _cls, _FakeBase(_cls, (), {}))
sys.modules["sui.ml"] = ml_stub

# ---------- 5. Stub sui.dl  (PNN) ----------
dl_stub = types.ModuleType("sui.dl"); dl_stub.__path__ = []
# PNN kế thừa keras.Model giả để tránh mro lỗi
dl_stub.PNN = _FakeBase("PNN", (keras_stub.Model,), {})
sys.modules["sui.dl"] = dl_stub

print("[sitecustomize] ML/DL stubs ready → numpy/pandas/sklearn/tensorflow & sui.ml|dl blocked ✔")
