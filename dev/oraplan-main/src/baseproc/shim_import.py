import builtins
import types
from baseproc.shim_threading import ThreadingShim

_real_builtins_import = builtins.__import__

def _custom_import(name, globals=None, locals=None, fromlist=(), level=0):
    if name == "threading":
        return ThreadingShim()
    return _real_builtins_import(name, globals, locals, fromlist, level)


def add_thread_shim(mod: types.ModuleType) -> None:
    sandbox_builtins = dict(builtins.__dict__)
    sandbox_builtins["__import__"] = _custom_import
    mod.__dict__['__builtins__'] = sandbox_builtins
    mod.__dict__['threading'] = ThreadingShim()