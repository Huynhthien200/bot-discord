"""
sitecustomize.py
  Stub toàn năng: numpy / pandas / sklearn (+ mọi sub-module)
  Được Python import tự động trước khi chạy bất kỳ user-code nào.
"""
import sys, types

class _Dummy(types.ModuleType):
    def __getattr__(self, name):
        fullname = f"{self.__name__}.{name}"
        if fullname in sys.modules:
            return sys.modules[fullname]
        sub = _Dummy(fullname)
        sub.__path__ = []            # đánh dấu là “package”
        sys.modules[fullname] = sub
        return sub
    # Cho phép len(), iter(), call, bool
    def __iter__(self): return iter(())
    def __len__(self): return 0
    def __call__(self, *a, **kw): return self
    def __bool__(self): return False

for _pkg in ("numpy", "pandas", "sklearn"):
    root = _Dummy(_pkg)
    root.__path__ = []               # cho import sub-package
    sys.modules[_pkg] = root
