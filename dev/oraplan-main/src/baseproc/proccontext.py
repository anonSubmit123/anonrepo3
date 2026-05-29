if "_initialized_baseplan" not in globals():
    from common.excp import InvalidStateException
    from baseproc.basestore import AppContextStore
    
    _initialized_baseplan = True

    import contextvars
    from typing import Any, Dict, List
    
    class TaskAction:
        def __init__(self, action: str, arg_list: List[str] = None):
            self.action_name: str = action
            self.arg_list: List[str] = arg_list
    
        def __repr__(self) -> str:
            strargs = " " +  " ".join(self.arg_list) if self.arg_list is not None else ""
            return f"({self.action_name}{strargs})"
    
        def __str__(self) -> str:
            strargs = " " +  " ".join(self.arg_list) if self.arg_list is not None else ""
            return f"({self.action_name}{strargs})"   
    
    class ProcManage:
        CXTVAR_TASKCXT_KEY = "op_task_cxt"
        CXTVAR_TASKCXT = contextvars.ContextVar(CXTVAR_TASKCXT_KEY)
        subtask_invoker = None
        
        @staticmethod
        def get_appcxt_store() -> AppContextStore:
            task_cxt = ProcManage.CXTVAR_TASKCXT.get()
            return task_cxt.state_cxt
        
        @staticmethod
        def get_logger() -> str:
            task_cxt = ProcManage.CXTVAR_TASKCXT.get()
            return task_cxt.logger
        
        @staticmethod
        def get_app_spec() -> str:
            task_cxt = ProcManage.CXTVAR_TASKCXT.get()
            return task_cxt.app_spec
        
        @staticmethod
        def set_new_app(app_id: str, app_ns: str, task_cxt: Any) -> None:
            app_cxt = task_cxt.state_cxt
            app_cxt.set_item(ProcManage.APPCXT_APPID, app_id)
            app_cxt.set_item(ProcManage.APPCXT_NAMESPACE, app_ns)
            ProcManage.CXTVAR_TASKCXT.set(task_cxt)
        
        @staticmethod
        def set_task_cxt(task_cxt: Any) -> Any:
            try:
                prev_task_cxt = ProcManage.CXTVAR_TASKCXT.get()
            except LookupError:
                prev_task_cxt = None
            ProcManage.CXTVAR_TASKCXT.set(task_cxt)
            return prev_task_cxt
            
        @staticmethod
        def invoke_subtask(task_purpose: str, args: Dict[str, Any] = None) -> Any:
            task_cxt = ProcManage.CXTVAR_TASKCXT.get()
            if task_cxt is None:
                raise InvalidStateException("No task context assigned - task not initialized?")
            task_runner = task_cxt.sys_cxt.task_runner
            if task_runner is None:
                raise InvalidStateException("No task runner setup for the system context")
                
            arg_list = list(args.keys()) if args is not None else None
            result = task_runner.invoke_subtask(task_cxt, TaskAction(task_purpose, arg_list), args,
                wait_completion=True)
            return result[0]
        
        @staticmethod
        def action_begin(self, action_spec: TaskAction) -> None:
            task_cxt = ProcManage.CXTVAR_TASKCXT.get()
            if task_cxt is None:
                raise InvalidStateException("No task context assigned - task not initialized?")
            task_cxt.sys_cxt.task_monitor.action_begin(action_spec, task_cxt)
            
        @staticmethod
        def action_pause(self, action_spec: TaskAction) -> None:
            task_cxt = ProcManage.CXTVAR_TASKCXT.get()
            if task_cxt is None:
                raise InvalidStateException("No task context assigned - task not initialized?")
            task_cxt.sys_cxt.task_monitor.action_pause(action_spec, task_cxt)
        
        @staticmethod
        def action_complete(self, action_spec: TaskAction) -> None:
            task_cxt = ProcManage.CXTVAR_TASKCXT.get()
            if task_cxt is None:
                raise InvalidStateException("No task context assigned - task not initialized?")
            task_cxt.sys_cxt.task_monitor.action_complete(action_spec, task_cxt)
            
op_get_appcxt_store = ProcManage.get_appcxt_store
op_get_appspec = ProcManage.get_app_spec
op_invoke_subtask = ProcManage.invoke_subtask
op_action_begin = ProcManage.action_begin
op_action_pause = ProcManage.action_pause
op_action_complete = ProcManage.action_complete
op_get_logger = ProcManage.get_logger