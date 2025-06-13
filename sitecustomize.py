# sitecustomize.py (đặt trước mọi import)
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
    def __len__(self): return 0
    def __bool__(self): return False

# stub numpy/pandas/sklearn/tensorflow
for m in ("numpy", "pandas", "sklearn", "tensorflow"):
    sys.modules[m] = _Dummy(m)

# 👉 tạo lớp giả Model để kế thừa được
class _FakeBase(type):
    def __new__(mcls, name, bases, ns): return super().__new__(mcls, name, (), ns)
keras_stub = _Dummy("tensorflow.keras"); keras_stub.__path__ = []
keras_layers = _Dummy("tensorflow.keras.layers"); keras_layers.__path__ = []
keras_stub.layers = keras_layers
keras_stub.Model = _FakeBase("Model", (), {})
sys.modules["tensorflow.keras"] = keras_stub
sys.modules["tensorflow.keras.layers"] = keras_layers

# chặn sui.ml & sui.dl
for pkg in ("sui.ml", "sui.dl"):
    sys.modules[pkg] = types.ModuleType(pkg)

print("[sitecustomize] keras.Model stub ready, ML/DL blocked")
