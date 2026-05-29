from spcoreutil.jsonCodec import get_first
from jsonpath_ng import parse as jsonpath_parse
from common.util import dynamic_load_instance
import json
from typing import NewType

GPT_TOKEN = "gpt4o"
CFG_GENERATOR_PROVIDER = jsonpath_parse("$.generator_provider")

Provider = NewType("Provider", None)

class GeneratorProviderFactory:
    def __init__(self, configFileName: str):
        with open(configFileName, 'r') as configFile:
            self.config = json.load(configFile)
        
    def createProvider(self, providerToken: str) -> Provider:
        generator_provider_cfg = get_first(CFG_GENERATOR_PROVIDER, self.config)
        if generator_provider_cfg:
            generator_provider_cfg.append({'name': 'gpt-5.1', 'provider': {'module_name': 'generate.generateGptSubtasks', 'class_name': 'GPTProvider'}})
            for generator_provider in generator_provider_cfg:
                if generator_provider.get("name") == providerToken:
                    provider = generator_provider.get("provider")
                    module_name = provider["module_name"]
                    class_name = provider["class_name"]
                    args = {}
                    loadComponent = dynamic_load_instance(module_name, class_name, args)
                    return loadComponent
        
currProviderFactory = GeneratorProviderFactory("../oraconfig.json")
newClass = currProviderFactory.createProvider("gpt4o")