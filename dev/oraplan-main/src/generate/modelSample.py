from abc import abstractmethod

class LLMAgent:
    @abstractmethod
    def sendMessage(self, message, messageTitle = "", additionalMessage="", temperature=0.1, history=[], model=None, returnFormat=None):
        pass

    @abstractmethod
    def getHistory(self):
        pass
    
    @abstractmethod
    def getRawHistory(self):
        pass
    
    @abstractmethod
    def addToHistory(self, question, answer, messageTitle = ""):
        pass
    
    @abstractmethod  
    def getEntry(self, entryName):
        pass
        
    @abstractmethod    
    def getModel(self):
        pass
    
    @abstractmethod
    def setModel(self, newModel):
        pass
        
    @abstractmethod
    def getCompleteHistory(self):
        pass
    
    @abstractmethod
    def getRawAnswers(self):
        pass
    
    @abstractmethod
    def getTimeSpent(self):
        pass
    
    @abstractmethod
    def getDomainTitle(self):
        pass
    
    @abstractmethod
    def getProblemFile(self):
        pass
    
    @abstractmethod
    def getDomainFile(self):
        pass
    