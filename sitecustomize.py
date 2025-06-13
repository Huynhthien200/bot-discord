import sys, types

class Dummy(types.ModuleType):
    def __getattr__(self, name):
        full = f"{self.__name__}.{name}"
        if full in sys.modules:
            return sys.modules[full]
        sub = Dummy(full)
        sub.__path__ = []          # đánh dấu là package
        sys.modules[full] = sub
        return sub
    def __call__(self, *a, **kw): return self
    def __iter__(self): return iter(())
    def __len__(self): return 0
    def __bool__(self): return False

for pkg in ("numpy", "pandas", "sklearn"):
    root = Dummy(pkg)
    root.__path__ = []
    sys.modules[pkg] = root

print("[sitecustomize] Dummy ML stubs loaded ✔")
