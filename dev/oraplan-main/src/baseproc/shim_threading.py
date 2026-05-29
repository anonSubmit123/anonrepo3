import threading as _real_threading

class ContextThread(_real_threading.Thread):
    def start(self):
        import contextvars
        ctx = contextvars.copy_context()
        
        target, args, kwargs = self._target, self._args, self._kwargs
        
        def runner(*a, **k):
            ctx.run(target, *a, **k)
            
        self._target, self._args, self._kwargs = runner, args, kwargs
        
        super().start()

class ThreadingShim:
    Thread = ContextThread

    def __getattr__(self, name):
        return getattr(_real_threading, name)