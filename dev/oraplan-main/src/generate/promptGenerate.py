import json
from abc import abstractmethod
from jinja2 import Template
from typing import NewType, Dict
from collections import OrderedDict

SubtaskContext = NewType("SubtaskContext", None)

class PromptConfig:
    def __init__(self, enableHierarchy=True, technologyType="both", singleLlmPrompt=False, allowSemanticDecomposition=True):
        self.enableHierarchy = enableHierarchy
        self.technologyType = technologyType
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

class Prompt:
    def __init__(self, name: str):
        self.name = name
        self.content = ""
        
    def setName(self, name:str) -> None:
        self.name = name
        
    def getName(self) -> str:
        return self.name
        
    def getContent(self):
        return self.content
    
    def setContent(self, content:str) -> None:
        self.content=content
    
    def generateReplaceDict(self, jsonDict:Dict[str, str], context:SubtaskContext, promptConfig):
        replaceArr = {}
        return replaceArr    
    
    def replaceComponents(self, replaceText, context):
        while "{{" in replaceText and "}}" in replaceText:
            templ = Template(replaceText)
            replaceText = templ.render(**context)
        self.content = replaceText
    
class HighPrompt(Prompt):
    def generateReplaceDict(self, jsonDict, context, promptConfig):
        replaceArr = {}
        if promptConfig.getHierarchyEnabled():
            replaceArr["hierarchyExplanation1"] = jsonDict["hierarchy1WithHierarchy"]
            replaceArr["hierarchyExplanation2"] = jsonDict["hierarchy2WithHierarchy"]
            replaceArr["hierarchyExplanation3"] = jsonDict["hierarchy3WithHierarchy"]
            replaceArr["exampleCode"] = jsonDict["exampleCodeHierarchy"]
            replaceArr["activityGenerationInstructions"] = jsonDict["activityGenerationInstructionsWithHierarchy"]
            if promptConfig.getSemanticDecompositionAllowed():
                replaceArr["activityGenerationRules"] = jsonDict["activityGenerationRulesSemantic"]
            else:
                replaceArr["activityGenerationRules"] = jsonDict["activityGenerationRulesNoSemantic"]
        else:
            replaceArr["hierarchyExplanation1"] = jsonDict["hierarchy1NoHierarchy"]
            replaceArr["hierarchyExplanation2"] = jsonDict["hierarchy2NoHierarchy"]
            replaceArr["hierarchyExplanation3"] = jsonDict["hierarchy3NoHierarchy"]
            if promptConfig.getTechnologyType() == "procedure":
                replaceArr["exampleCode"] = jsonDict["exampleCodeProcedure"]
            else:
                replaceArr["exampleCode"] = jsonDict["exampleCodePlan"]
            if promptConfig.getSemanticDecompositionAllowed():
                replaceArr["activityGenerationRules"] = jsonDict["activityGenerationRulesSemantic"]
            else:
                replaceArr["activityGenerationRules"] = jsonDict["activityGenerationRulesNoSemantic"]
            replaceArr["activityGenerationInstructions"] = jsonDict["activityGenerationInstructionsNoHierarchy"]

        if promptConfig.getTechnologyType() == "both":
            replaceArr["technologyTypeInstructions"] = jsonDict["technologyTypeInstructionsBoth"]
        elif promptConfig.getTechnologyType() == "pddl":
            replaceArr["technologyTypeInstructions"] = jsonDict["technologyTypeInstructionsPDDL"]
        elif promptConfig.getTechnologyType() == "procedure":
            replaceArr["technologyTypeInstructions"] = jsonDict["technologyTypeInstructionsPython"]

        if context.getLevel() == 0: 
            replaceArr["parentExplanation"] = jsonDict["parentExplanationTop"]
            replaceArr["parentDetails"] = jsonDict["parentDetailsTop"]  
            replaceArr["parentContextDescription"] = ""
            replaceArr["exampleHighLevel"] = jsonDict["exampleTop"]
            replaceArr["levelReminder"] = jsonDict["levelReminderTop"]
            replaceArr["parentParametersAndContext"] = jsonDict["parentParametersAndContextTop"]
            replaceArr["parentStateContextDescription"] = jsonDict["parentStateContextDescriptionTop"]
            replaceArr["stateSetup"] = jsonDict["stateSetupTop"]
            replaceArr["outputTransitionInformation"] = jsonDict["outputTransitionInformationAbsent"]
        else:
            replaceArr["parentExplanation"] = jsonDict["parentExplanationNotTop"]
            replaceArr["parentDetails"] = jsonDict["parentDetailsNotTop"]
            parentParameters = context.getParentAction().getParamDescription()
            if parentParameters != None:
                replaceArr["parentParametersAndContext"] = jsonDict["parentParametersAndContextNotTop"]
                replaceArr["parentParameter"] = parentParameters
            elif parentParameters == None:
                replaceArr["parentParametersAndContext"] = jsonDict["parentParametersAndContextTop"]
            parentStateContext = context.getParentContext().getStateContext()
            if parentStateContext != None:
                replaceArr["parentStateContextDescription"] = jsonDict["parentStateContextDescriptionNotTop"]
                replaceArr["parentStateContext"] = parentStateContext
            elif parentStateContext == None:
                replaceArr["parentStateContextDescription"] = jsonDict["parentStateContextDescriptionTop"]
            if context.getParentContext().getType().lower() == "pddl":
                replaceArr["parentContextDescription"] = jsonDict["parentContextDescriptionPddl"]
                replaceArr["parentDetailsExact"] = jsonDict["parentDetailsExactPlan"]
                replaceArr["dom"] = context.getParentContext().getDomainFile()
                replaceArr["prob"] = context.getParentContext().getProblemFile()
            elif context.getParentContext().getType().lower() == "procedure":
                replaceArr["parentContextDescription"] = jsonDict["parentContextDescriptionPython"]
                replaceArr["parentDetailsExact"] = jsonDict["parentDetailsExactProcedure"]
                replaceArr["procedure"] = context.getParentContext().getProcedure()
            else:
                raise ValueError("Parent doesn't have valid context name")
            
            replaceArr["parentParameters"] = str(context.getParentAction().getParamDescription())
            replaceArr["parentStateContext"] = str(context.getParentContext().getStateContext())
            replaceArr["action"] = context.getParentAction().getActionDef() + " " + context.getParentAction().getActionDescription()
            replaceArr["exampleHighLevel"] = jsonDict["exampleNotTop"]
            replaceArr["levelReminder"] = jsonDict["levelReminderNotTop"]
            additionalStateContextInformation = context.getParentAction().getAdditionalStateContextInformation()
            if additionalStateContextInformation != None:
                replaceArr["additionalStateContext"] = jsonDict["additionalStateContextPresent"]
                replaceArr["ancestorContext"] = additionalStateContextInformation
            elif additionalStateContextInformation == None:
                replaceArr["additionalStateContext"] = jsonDict["additionalStateContextAbsent"]
            replaceArr["stateSetup"] = jsonDict["stateSetupNotTop"]
            
            expectedReturnValue = context.getParentAction().getExpectedReturnValue()
            if expectedReturnValue == None or expectedReturnValue == "":
                replaceArr["outputTransitionInformation"] = jsonDict["outputTransitionInformationAbsent"]
            else:
                replaceArr["outputTransitionInformation"] = jsonDict["outputTransitionInformationPresent"]
                replaceArr["outputTransitionComment"] = expectedReturnValue
                 
        problemStatement = context.getProblem()
        if problemStatement is None or problemStatement == "":
            raise ValueError("Blank problem statement in current context")
        replaceArr["problem"] = problemStatement
        primitives = context.getPrimitives()
        if primitives is None or primitives == "":
            raise ValueError("Blank primitives in current context")
        replaceArr["primitive"] = primitives  
        return replaceArr
     
class CombinePrompt(Prompt):
    def generateReplaceDict(self, jsonDict:Dict[str, str], context:SubtaskContext, promptConfig):
        replaceArr = {}
        if context.getType().lower() == "procedure":
            replaceArr["primitiveKeepDescription"] = jsonDict["primitiveKeepDescriptionPython"]
            replaceArr["combExample"] = jsonDict["combExamplePython"]
        elif context.getType().lower() == "pddl":
            replaceArr["primitiveKeepDescription"] = jsonDict["primitiveKeepDescriptionPddl"]
            replaceArr["combExample"] = jsonDict["combExamplePddl"]
        else:
            raise ValueError("No valid context type")
        if context.getLevel() == 0:
            replaceArr["parentContextInfo"] = jsonDict["parentContextInfoTop"]
        elif context.getLevel() > 0:
            replaceArr["parentContextInfo"] = jsonDict["parentContextInfoNotTop"]
        return replaceArr
     
class ErrorPrompt(Prompt):
    def generateReplaceDict(self, jsonDict:Dict[str, str], context:SubtaskContext, promptConfig):
        replaceArr = {}
        if context.getType().lower() == "procedure":
            replaceArr["resultOutput"] = jsonDict["resultOutputPython"]
            replaceArr["rewriteComponents"] = jsonDict["rewriteComponentsPython"]
        elif context.getType().lower() == "pddl":
            replaceArr["resultOutput"] = jsonDict["resultOutputPython"]
            replaceArr["rewriteComponents"] = jsonDict["rewriteComponentsPython"]
        else:
            raise ValueError("No valid context type")
        return replaceArr
     
class GeneratePythonPrompt(Prompt):
    def generateReplaceDict(self, jsonDict:Dict[str, str], context:SubtaskContext, promptConfig):
        replaceArr = {}
        if context.getLevel() == 0:
            replaceArr["procedureWarning"] = jsonDict["procedureWarningTop"]
        elif context.getLevel() > 0:
            replaceArr["procedureWarning"] = jsonDict["procedureWarningNotTop"]
        return replaceArr
     
class TranslatePrompt(Prompt):
    def generateReplaceDict(self, jsonDict:Dict[str, str], context:SubtaskContext, promptConfig):
        replaceArr = {}
        if context.getType().lower() == "procedure":
            replaceArr["generateTranslate"] = jsonDict["generateTranslatePython"].get("text")
        elif context.getType().lower() == "pddl":
            replaceArr["generateTranslate"] = jsonDict["generateTranslatePddl"].get("text")
        else:
            raise ValueError("No valid context type")
        
        if context.getLevel() > 0:
            expectedReturnValue = context.getParentAction().getExpectedReturnValue()
            if expectedReturnValue == None:
                replaceArr["outputTranslate"] = jsonDict["outputTranslateAbsent"]
            else:
                replaceArr["outputTranslate"] = jsonDict["outputTranslatePresent"]
                parentContextType = context.getParentContext().getType()
                if parentContextType.lower() == "pddl":
                    replaceArr["parentReturn"] = jsonDict["parentPlan"]
                elif parentContextType.lower() == "proceure":
                    replaceArr["parentReturn"] = jsonDict["parentProcedure"]
        
        return replaceArr
     
class DescribePrompt(Prompt):
    def generateReplaceDict(self, jsonDict:Dict[str, str], context:SubtaskContext, promptConfig):
        replaceArr = {}
        if context.getType().lower() == "procedure":
            replaceArr["descriptionType"] = jsonDict["descriptionTypePython"]
            replaceArr["describeDetails"] = jsonDict["describeDetailsPython"]
            replaceArr["contextModify"] = jsonDict["contextModifyPython"]
            replaceArr["sampleActionDefinition"] = jsonDict["sampleActionDefinitionPython"]
            replaceArr["actDesc"] = jsonDict["actDescPython"]
        elif context.getType().lower() == "pddl":
            replaceArr["descriptionType"] = jsonDict["descriptionTypePddl"]
            replaceArr["describeDetails"] = jsonDict["describeDetailsPddl"]
            replaceArr["contextModify"] = jsonDict["contextModifyPddl"]
            replaceArr["sampleActionDefinition"] = jsonDict["sampleActionDefinitionPddl"]
            replaceArr["actDesc"] = jsonDict["actDescPddl"]
        else:
            raise ValueError("No valid context type")
        if context.getLevel() == 0:
            replaceArr["additionalContextExample"] = jsonDict["additionalContextExampleTop"]
            replaceArr["additionalContextDetails"] = jsonDict["additionalContextDetailsTop"]
        elif context.getLevel() > 0:
            replaceArr["additionalContextExample"] = jsonDict["additionalContextExampleNotTop"]
            replaceArr["additionalContextDetails"] = jsonDict["additionalContextDetailsNotTop"]
        return replaceArr

class PromptGenerator:
    def __init__(self, json_path:str, promptConfig: PromptConfig):
        self.filePath = json_path
        self.jsonContent = json.load(open(json_path))
        self.promptConfig = promptConfig
                
    def generatePrompts(self, context: SubtaskContext, numPrompts=-1):
        prompt_keys = OrderedDict()
        prompt_keys["high"] = HighPrompt
        if self.promptConfig.getHierarchyEnabled() == True:
            prompt_keys["combine"] = CombinePrompt
        if context.getType().lower() == "pddl":
            prompt_keys["error"] = ErrorPrompt
            prompt_keys["generate"] = Prompt
        elif context.getType().lower() == "procedure":
            prompt_keys["generate"] = Prompt
            prompt_keys["error"] = ErrorPrompt
        if self.promptConfig.getHierarchyEnabled() == True and \
            context.getLevel() > 0:
            prompt_keys["translation"] = TranslatePrompt
        prompt_keys["description"] = DescribePrompt
            
        prompts = []
        for promptNum, (promptKey, promptVal) in enumerate(prompt_keys.items()):
            if numPrompts != -1 and promptNum >= numPrompts:
                return prompts
            currPrompt = promptVal(promptKey)
            currName = promptKey
            currJsonMainPrompt = self.jsonContent.get(currName)
            mainPrompt = currJsonMainPrompt.get("main")
            
            if currName == "generate":
                if context.getType().lower() == "procedure":
                    newPrompt = GeneratePythonPrompt("generate")
                    textContent = currJsonMainPrompt.get("generateComponentsPython")["text"]
                    replaceDict = newPrompt.generateReplaceDict(currJsonMainPrompt, context, self.promptConfig)
                    newPrompt.replaceComponents(textContent, replaceDict)
                    prompts.append(newPrompt)
                elif context.getType().lower() == "pddl":
                    for i in range(1, 6, 1):
                        promptName = "PDDLPrompt" + str(i)
                        newPrompt = Prompt(promptName)
                        promptText = currJsonMainPrompt.get(promptName)["text"]
                        newPrompt.setContent(promptText)
                        prompts.append(newPrompt)
                continue    
            replaceDict = currPrompt.generateReplaceDict(currJsonMainPrompt, context, self.promptConfig)
            if isinstance(mainPrompt, dict):
                promptText = mainPrompt["text"]
            else:
                promptText = mainPrompt
            currPrompt.replaceComponents(promptText, replaceDict)
            
            prompts.append(currPrompt)
        return prompts
    
    def generateStateContextModifyPrompt(self, context:SubtaskContext, className:str, classText:str):
        origStateReplaceDict = self.jsonContent.get("modifyStateContext")
        origStateReplacePrompt = Prompt("modifyStateContext")
        replacementDict = origStateReplacePrompt.generateReplaceDict(origStateReplaceDict, context)
        mainPrompt = origStateReplaceDict.get("main")
        if isinstance(mainPrompt, dict):
            promptText = mainPrompt["text"]
        else:
            promptText = mainPrompt
        replacementDict["childName"] = className
        replacementDict["modifyText"] = classText
        origStateReplacePrompt.replaceComponents(promptText, replacementDict)
        return origStateReplacePrompt   

    def generateOverridePrompt(self, problem, primitives):
        llmOverrideContent = self.jsonContent.get("singleLevelLlmOverride")["main"]["text"]
        print(llmOverrideContent)
        replaceArr = {}
        replaceArr["problem"] = problem
        replaceArr["primitive"] = primitives
        llmOverridePrompt = Prompt("llmOverridePrompt")
        llmOverridePrompt.replaceComponents(llmOverrideContent, replaceArr)
        return llmOverridePrompt
    
    def generateNewPrompt(self, name, content):
        newPrompt = Prompt(name)
        newPrompt.setContent(content)   
        return newPrompt 
