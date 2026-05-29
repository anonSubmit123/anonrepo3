from abc import abstractmethod
from typing import NewType, List, Dict, Iterator, OrderedDict
from generate.promptGenerate import PromptGenerator
from asttokens.util import replace
from jsonpath_ng import parse as jsonpath_parse
import json
from spcoreutil.jsonCodec import get_first
from common import util
import pickle
import os
from _collections import OrderedDict

SubtaskContext = NewType("SubtaskContext", None)
Provider = NewType("Provider", None)
LlmCommunicator = NewType("LlmCommunicator", None)

CFG_OUTPUT_LOCATION = jsonpath_parse("$.outputLocation")
CFG_PICKLE_LOCATION = jsonpath_parse("$.pickleLocation")

class TestInfo:
    def __init__(self, enableHierarchy=True, technologyType="both", singleLlmPrompt=False, allowSemanticDecomposition=True):
        self.enableHierarchy = enableHierarchy
        self.technologyType = technologyType.lower()
        self.singleLlmPrompt = singleLlmPrompt
        self.allowSemanticDecomposition = allowSemanticDecomposition

    def getHierarchyEnabled(self):
        return self.enableHierarchy

    def getTechnologyType(self):
        return self.technologyType

    def getSingleLlmPrompt(self):
        return self.singleLlmPrompt

    def getSemanticDecompositionAllowed(self):
        return self.allowSemanticDecomposition

class TaskGenerator:
    def __init__(self, provider:Provider, problem:str, primitives:str, promptGenerator:PromptGenerator, taskGenMod: TestInfo, baseLocation:str="~/work/sciproj/oraplan", configLocation:str="~/work/sciproj/oraplan/dev/oraplan-main/oraconfig.json", intervention:List[str]=["subtask"]):
        self.provider = provider
        self.llmCommunicator = provider.provideLlmCommunicator()
        self.problem=problem
        self.primitives=primitives
        self.promptGenerate = promptGenerator
        self.taskGenMod = taskGenMod
        self.baseLocation=baseLocation
        self.configLocation=configLocation
        self.pendingSubtaskQueue = None
        
        with open(self.configLocation, 'r') as configFile:
            configInfo = json.load(configFile)
        outputLocation = get_first(CFG_OUTPUT_LOCATION, configInfo)
        if outputLocation.get("taskName") is not None:
            taskName = outputLocation.get("taskName")
            self.taskName = util.resolve_env_variables(taskName)
        if outputLocation.get("basepath") is not None:
            basePathLocation = outputLocation.get("basepath")
            self.basePathLocation = util.resolve_env_variables(basePathLocation)
        else:
            raise ValueError("No correct basepath location provided")
        self.userIntervention=intervention
    
    @abstractmethod
    def generateTask(self, problemStatement: str, primitiveActions: str, baseLocation: str):
        pass 
    
    def setLlmCommunicator(self, llmCommunicator:LlmCommunicator) -> None:
        self.llmCommunicator = llmCommunicator
        
    def getLlmCommunicator(self) -> LlmCommunicator:
        return self.llmCommunicator
    
    def setPromptGenerator(self, promptGen: PromptGenerator) -> None:
        self.promptGenerate = promptGen
        
    def getPromptGenerator(self) -> PromptGenerator:
        return self.promptGenerate
        
    def setProvider(self, provider:Provider) -> None:
        self.provider = provider
    
    def getProvider(self) -> Provider:
        return self.provider
    
    def setProblem(self, problem: str) -> None:
        self.problem = problem
        
    def getProblem(self) -> str:
        return self.problem
    
    def setPrimitive(self, primitives: str) -> None:
        self.primitives = primitives
        
    def getPrimitives(self) -> str:
        return self.primitives
    
    def setBaseLocation(self, location:str) -> None:
        self.baseLocation = location
    
    def getBaseLocation(self) -> str:
        return self.baseLocation
    
    def setTaskName(self, name:str) -> None:
        self.taskName = name
        
    def getTaskName(self) -> str:
        return self.taskName
    
    def saveToFile(self, file=""):
        with open(self.configLocation, 'r') as configFile:
            configInfo = json.load(configFile)
        outputLocation = get_first(CFG_PICKLE_LOCATION, configInfo)
        if outputLocation.get("location") is not None:
            pickleLocation = outputLocation.get("location")
            pickleLocation = util.resolve_env_variables(pickleLocation)
            
        if file == "":
            folderName = pickleLocation + "/" + self.taskName
            if not os.path.exists(folderName):
                os.makedirs(folderName)
            location = pickleLocation + "/" + self.taskName + "/" + self.taskName + "Pickle"
        else:
            location = file
        print(location)
        
        communicator = self.llmCommunicator
        communicator.removeClient()
                
        with open(location, "wb") as file:
            pickle.dump(self, file)
            
        communicator.reinitializeClient()
            
    @classmethod
    def loadFile(cls, file):
        with open(file, "rb") as location:
            newContext = pickle.load(location)
        newContext.getLlmCommunicator().reinitializeClient()
        return newContext
    
    def setFirstSubtask(self, subtask:SubtaskContext) -> None:
        self.firstSubtask = subtask
        
    def getFirstSubtask(self) -> SubtaskContext:
        return self.firstSubtask
    
    def setUserIntervention(self, intervention:List[str]) -> None:
        self.userIntervention = intervention
        
    def getUserIntervention(self) -> str:
        return self.userIntervention
    
    def setConfigLocation(self, configLoc:str) -> None:
        self.configLocation = configLoc
        
    def getConfigLocation(self) -> str:
        return self.configLocation
    
class SubtaskGenerator():
    @abstractmethod
    def __init__(self):
        pass
    
    @abstractmethod
    def generateSubtask(self, parentContext:SubtaskContext, taskContext:TaskGenerator, level:int):
        pass
    
class LlmCommunicator():
    @abstractmethod
    def sendMessage(self, message, history):
        pass
    
    def removeClient(self):
        pass
    
    def reinitializeClient(self):
        pass
    
SubtaskContext = NewType("SubtaskContext", None)
    
class ActionDef:
    def __init__(self, actionName:str, actionParams:List[str], actionDef:str, isPrimitive: bool, paramDescription:str):
        self.actionName=actionName
        self.actionParams=actionParams
        self.actionDef=actionDef
        self.isPrimitive = isPrimitive
        self.actionDefinition = []
        self.parentDescription = paramDescription
        self.additionalStateContextInformation = None
        self.expectedReturnValue = None
        
    def setActionName(self, actionName:str) -> None:
        self.actionName=actionName
        
    def getActionName(self) -> str:
        return self.actionName
    
    def setActionParams(self, actionParams:List[str]) -> None:
        self.actionParams = actionParams
        
    def getActionParams(self) -> List[str]:
        return self.actionParams
    
    def setActionDef(self, actionDef:str) -> None:
        self.actionDef=actionDef
        
    def getActionDef(self) -> str:
        return self.actionDef
    
    def setPrimitive(self, isPrimitive:bool) -> None:
        self.isPrimitive=isPrimitive
        
    def getPrimitive(self) -> bool:
        return self.isPrimitive
    
    def setActionDescription(self, descriptionAction: str) -> None:
        self.actionDescription = descriptionAction
        
    def getActionDescription(self) -> str:
        return self.actionDescription
    
    def addActionDefinition(self, childContext:SubtaskContext) -> None:
        self.actionDefinition.append(childContext)
        
    def removeActionDefinition(self, childContext:SubtaskContext) -> None:
        self.actionDefinition.remove(childContext)
        
    def getActionDefinitionIterator(self) -> Iterator[SubtaskContext]:
        return iter(self.actionDefinition)
    
    def setParamDescription(self, paramDescription:str) -> None:
        self.paramDescription = paramDescription
        
    def getParamDescription(self) -> str:
        return self.paramDescription
    
    def setAdditionalStateContextInformation(self, additionalStateContextInformation: str) -> None:
        self.additionalStateContextInformation = additionalStateContextInformation
        
    def getAdditionalStateContextInformation(self) -> str:
        return self.additionalStateContextInformation
    
    def setExpectedReturnValue(self, returnValue:str) -> None:
        self.expectedReturnValue = returnValue 
        
    def getExpectedReturnValue(self) -> str:
        return self.expectedReturnValue
    
class SubtaskContext:    
    counter = 0
    
    def __init__(self, correspondingGenerator:SubtaskGenerator, parentContext:SubtaskContext, 
                 level:int, subtaskType:str, parentAction: ActionDef, taskGenerator:TaskGenerator, replaceContext:int=-1):
        if replaceContext == -1:
            self.subtaskId = SubtaskContext.generate_id()
        else:
            self.subtaskId = replaceContext
        self.corrGenerator = correspondingGenerator
        self.parentContext = parentContext
        self.level=level
        self.subtaskType = subtaskType.lower()
        self.history = []
        self.problem = self.parentContext.getProblem() if parentContext is not None else None
        self.primitives = self.parentContext.getPrimitives() if parentContext is not None else None
        self.actionDef = OrderedDict()
        self.stateContext = None
        self.inputTransitionProcedure = None
        self.outputTransitionProcedure = None
        if parentAction is not None:
            if replaceContext != -1:
                for context in parentAction.getActionDefinitionIterator():
                    if replaceContext == context.get_id():
                        parentAction.removeActionDefinition(context)
            self.parentAction = parentAction
            self.parentAction.addActionDefinition(self)
        else:
            self.parentAction = None
        self.taskGenerator = taskGenerator
        
        if self.parentAction == None:
            self.subtaskName = "top"
        else:
            self.subtaskName = self.parentAction.getActionName()
        
    @classmethod
    def generate_id(cls):
        cls.counter = cls.counter + 1
        return cls.counter
    
    def get_id(self) -> int:
        return self.subtaskId
    
    def set_id(self, id:int) -> None:
        self.subtaskId = id
        
    def setLevel(self, level:int) -> None:
        self.level = level
        
    def getLevel(self) -> int:
        return self.level
    
    def setType(self, subtaskType:str) -> None:
        self.subtaskType = subtaskType
    
    def getType(self) -> str:
        return self.subtaskType
    
    def getParentContext(self) -> SubtaskContext:
        return self.parentContext
    
    def setParentContext(self, parent: SubtaskContext) -> None:
        self.parentContext = parent
    
    def setInputTransitionProcedure(self, procedure:str):
        self.inputTransitionProcedure = procedure
        
    def getInputTransitionProcedure(self):
        return self.inputTransitionProcedure
    
    def setOutputTransitionProcedure(self, procedure:str):
        self.outputTransitionProcedure = procedure
        
    def getOutputTransitionProcedure(self):
        return self.outputTransitionProcedure
    
    def setSubtaskExplanation(self, explanation:str):
        self.subtaskExplanation = explanation
        
    def getSubtaskExplanation(self):
        return self.subtaskExplanation
    
    def setActionDefs(self, actionDef: OrderedDict[str, ActionDef]) -> None:
        self.actionDef = actionDef
        
    def addActionDefs(self, actionDef: Dict[str, ActionDef]) -> None:
        for actionKey, actionVal in actionDef.items():
            self.actionDef[actionKey] = actionVal
        
    def getActionDefs(self) -> OrderedDict[str, ActionDef]:
        return self.actionDef
    
    def getNonprimitiveActions(self) -> List[ActionDef]:
        actionArr = []
        for action in self.actionDef.values():
            if not action.getPrimitive():
                actionArr.append(action)
        return actionArr
    
    def getAction(self, name:str) -> ActionDef:
        return self.actionDef[name]
    
    def setHistory(self, history: List[Dict[str, str]]):
        self.history = history
        
    def getHistory(self) -> List[Dict[str, str]]:
        return self.history
    
    def addHistoryElement(self, query: str, response: str):
        queryResponse = {"role":"system", "content":query}
        aiResponse = {"role":"agent", "content":response}
        self.history.append(queryResponse)
        self.history.append(aiResponse)
        
    def setProblem(self, problem: str) -> None:
        self.problem = problem
        
    def getProblem(self) -> str:
        return self.problem
    
    def setPrimitives(self, primitives: str) -> None:
        self.primitives = primitives
    
    def getPrimitives(self) -> str:
        return self.primitives
    
    def setParentAction(self, parentAction: ActionDef):
        self.parentAction = parentAction
        
    def getParentAction(self) -> ActionDef:
        return self.parentAction
    
    def setTaskGenerator(self, taskGenerator: TaskGenerator) -> None:
        self.taskGenerator = taskGenerator
    
    def getTaskGenerator(self) -> TaskGenerator:
        return self.taskGenerator
    
    def setCorrGenerator(self, corrGenerator:SubtaskGenerator) -> None:
        self.corrGenerator = corrGenerator
        
    def getCorrGenerator(self) -> SubtaskGenerator:
        return self.corrGenerator
    
    def setStateContext(self, stateContext: str) -> None:
        self.stateContext = stateContext
        
    def getStateContext(self) -> str:
        return self.stateContext
    
    def setSubtaskName(self, subtaskName:str) -> None:
        self.subtaskName = subtaskName 
        
    def getSubtaskName(self) -> str:
        return self.subtaskName
    
    def clearSubtask(self):
        self.subtaskType = ""
        self.history = []
        self.actionDef = OrderedDict()
        self.stateContext = None
        self.inputTransitionProcedure = None
        self.outputTransitionProcedure = None 
        self.subtaskExplanation = None 
    
class ProcedureSubtaskContext(SubtaskContext):
    def __init__(self, correspondingGenerator:SubtaskGenerator, parentContext:SubtaskContext, 
                 level:int, subtaskType:str, parentAction: ActionDef, taskGenerator:TaskGenerator, replaceContext:int=-1):
        self.pythonProcedure = None
        super().__init__(correspondingGenerator, parentContext, level, subtaskType, parentAction, taskGenerator, replaceContext)
        
    def setProcedure(self, procedure: str) -> None:
        self.pythonProcedure = procedure
        
    def getProcedure(self) -> str:
        return self.pythonProcedure
    
    @classmethod 
    def from_context(self, context: SubtaskContext):
        newObject = ProcedureSubtaskContext(context.corrGenerator, context.parentContext, context.level, "procedure", context.parentAction, context.getTaskGenerator(), context.get_id())
        newObject.setHistory(context.history)
        newObject.setProblem(context.problem)
        newObject.setPrimitives(context.primitives)
        newObject.setActionDefs(context.actionDef)
        newObject.set_id(context.get_id())
        return newObject
    
    def clearSubtask(self):
        super().clearSubtask()
        self.pythonProcedure = None
    
class PlanSubtaskContext(SubtaskContext):
    def __init__(self, correspondingGenerator:SubtaskGenerator, parentContext:SubtaskContext, 
                 level:int, subtaskType:str, parentAction: ActionDef, taskGenerator:TaskGenerator, replaceContext:int=-1):
        self.domainFile = None
        self.sampleProblemFile = None 
        self.problemFile = None 
        super().__init__(correspondingGenerator, parentContext, level, 
                       subtaskType, parentAction, taskGenerator, replaceContext)
    
    def setDomainFile(self, domainFile: str) -> None:
        self.domainFile = domainFile
        
    def getDomainFile(self) -> str:
        return self.domainFile
    
    def clearSubtask(self):
        super().clearSubtask()
        self.domainFile = None 
        self.sampleProblemFile = None
        self.problemFile = None 
        
    def setSampleProblemFile(self, sampleProblemFile: str) -> None:
        self.sampleProblemFile = sampleProblemFile
        
    def getSampleProblemFile(self) -> str:
        return self.sampleProblemFile
    
    def setProblemFile(self, problemFile: str) -> None:
        self.problemFile = problemFile
        
    def getProblemFile(self) -> str:
        if self.level == 0 and self.sampleProblemFile != None:
            self.problemFile = self.sampleProblemFile
        return self.problemFile
    
    @classmethod 
    def from_context(self, context: SubtaskContext):
        newObject = PlanSubtaskContext(context.getCorrGenerator(), context.getParentContext(), context.getLevel(), "pddl", context.getParentAction(), context.getTaskGenerator(), context.get_id())
        newObject.setHistory(context.history)
        newObject.setProblem(context.problem)
        newObject.setPrimitives(context.primitives)
        newObject.setActionDefs(context.actionDef)
        return newObject
        
class Provider:
    @abstractmethod
    def provideTextGenerator(self, provider:Provider, problem:str, primitives:str, testInfo:TestInfo, baseLocation:str, configLocation:str, promptLocation:str,humanInterventions:List[str]) -> TaskGenerator:
        pass

    @abstractmethod
    def provideSubtaskGenerator(self, parentContext:SubtaskContext, parentAction:ActionDef, taskGenerator:TaskGenerator, level:int, problem:str, primitives:str) -> SubtaskGenerator:
        pass
    
    @abstractmethod
    def provideLlmCommunicator(self) -> LlmCommunicator:
        pass
