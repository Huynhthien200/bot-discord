# ============================================================
#  ML/DL/Graph stub – chặn mọi thư viện nặng mà sui-0.1.x gọi
# ============================================================
import sys, types

class _Dummy(types.ModuleType):
    def __getattr__(self, name):
        full = f"{self.__name__}.{name}"
        if full in sys.modules:
            return sys.modules[full]
        sub = _Dummy(full); sub.__path__ = []; sys.modules[full] = sub
        return sub
    def __call__(self, *a, **kw): return self
    def __iter__(self): return iter(())
    def __bool__(self): return False

# ---------- 1. Chặn gói ML/DL/Graph nặng ----------
for pkg in ("numpy", "pandas", "sklearn", "tensorflow", "networkx"):
    root = _Dummy(pkg); root.__path__ = []; sys.modules[pkg] = root

# ---------- 2. Stub tensorflow.keras ----------
keras_stub = _Dummy("tensorflow.keras"); keras_stub.__path__ = []
keras_layers = _Dummy("tensorflow.keras.layers"); keras_layers.__path__ = []
sys.modules["tensorflow.keras"] = keras_stub
sys.modules["tensorflow.keras.layers"] = keras_layers
class _FakeBase(type):
    def __new__(mcls, name, bases, ns): return super().__new__(mcls, name, (), ns)
keras_stub.Model = _FakeBase("Model", (), {})

# ---------- 3. Stub sui.ml ----------
ml_stub = types.ModuleType("sui.ml"); ml_stub.__path__ = []
for cls in ("FunkSVD", "BiasSVD", "SVDpp", "BPR", "ALS", "AFM", "FM"):
    setattr(ml_stub, cls, _FakeBase(cls, (), {}))
sys.modules["sui.ml"] = ml_stub

# ---------- 4. Stub sui.dl ----------
dl_stub = types.ModuleType("sui.dl"); dl_stub.__path__ = []
dl_stub.PNN = _FakeBase("PNN", (keras_stub.Model,), {})
sys.modules["sui.dl"] = dl_stub

# ---------- 5. Stub sui.graph ----------
graph_stub = types.ModuleType("sui.graph"); graph_stub.__path__ = []
graph_stub.DeepWalk = _FakeBase("DeepWalk", (), {})
sys.modules["sui.graph"] = graph_stub

print("[sitecustomize] ML/DL/Graph stubs ready ✔")
