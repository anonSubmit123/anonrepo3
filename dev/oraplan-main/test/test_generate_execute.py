import argparse
import json
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional

from jsonpath_ng import parse as jsonpath_parse
from spcoreutil.jsonCodec import get_first

from common import util
from generate.generateSubtasks import GeneratorProviderFactory
from generate.basegen import TestInfo

from basetestutil import (
    base_test_setup,
    base_test_teardown,
    BaseHelper,
    get_json_attrs,
    load_initial_context,
)


CFG_GENERATION_INPUT = jsonpath_parse("$.generationInput")
CFG_PROMPT_INPUT = jsonpath_parse("$.promptInformation")
CFG_OUTPUT_LOCATION = jsonpath_parse("$.outputLocation")
CFG_TEST_CONFIG = jsonpath_parse("$.testConfig")
CFG_HUMAN_INTERVENTION = jsonpath_parse("$.humanIntervention")
CFG_EXECUTION = jsonpath_parse("$.execution")


class GenerateExecuteRunner:
    def __init__(self, oraplan_config_path: str):
        self.oraplan_config_path = os.path.expanduser(oraplan_config_path)
        self.oraplan_cfg = self._load_json(self.oraplan_config_path)

    def _load_json(self, path: str) -> Dict[str, Any]:
        with open(os.path.expanduser(util.resolve_env_variables(path)), "r") as f:
            return json.load(f)

    def _required_location(self, jsonpath_expr, field_name: str) -> str:
        node = get_first(jsonpath_expr, self.oraplan_cfg)
        if not node or node.get("location") is None:
            raise ValueError(f"Missing {field_name}.location in OraPlan config")
        return util.resolve_env_variables(node["location"])

    def _load_test_config(self) -> Dict[str, Any]:
        test_config_path = self._required_location(CFG_TEST_CONFIG, "testConfig")
        return self._load_json(test_config_path)

    def _load_generation_inputs(self):
        generation_input_path = self._required_location(CFG_GENERATION_INPUT, "generationInput")
        generation_input = self._load_json(generation_input_path)

        generation_cfg = get_first(CFG_GENERATION_INPUT, self.oraplan_cfg)
        problem_name = generation_cfg.get("problemName") or generation_cfg.get("domain")

        if problem_name is None:
            raise ValueError("Missing generationInput.problemName in OraPlan config")

        if problem_name not in generation_input:
            raise ValueError(f"Unable to find problem '{problem_name}' in {generation_input_path}")

        problem_info = generation_input[problem_name]
        return problem_name, problem_info["problem"], problem_info["primitives"]

    def _load_prompt_location(self) -> str:
        return self._required_location(CFG_PROMPT_INPUT, "promptInformation")

    def _load_output_info(self):
        output_cfg = get_first(CFG_OUTPUT_LOCATION, self.oraplan_cfg)
        if not output_cfg:
            raise ValueError("Missing outputLocation in OraPlan config")

        task_name = output_cfg.get("taskName")
        basepath = output_cfg.get("basepath")

        if task_name is None:
            raise ValueError("Missing outputLocation.taskName in OraPlan config")

        if basepath is None:
            raise ValueError("Missing outputLocation.basepath in OraPlan config")

        task_name = util.resolve_env_variables(task_name)
        basepath = util.resolve_env_variables(basepath)

        generated_ltm = str(Path(basepath).joinpath(task_name, f"{task_name}1.json").resolve())
        return task_name, basepath, generated_ltm

    def _load_human_intervention(self):
        human_cfg = get_first(CFG_HUMAN_INTERVENTION, self.oraplan_cfg)
        if not human_cfg:
            return []
        return human_cfg.get("prompts", [])

    def _load_test_info(self, test_cfg: Dict[str, Any]) -> TestInfo:
        return TestInfo(
            test_cfg["enableHierarchy"],
            test_cfg["technologyType"],
            test_cfg["singleLlmPrompt"],
            test_cfg["semanticDecompositionAllowed"],
        )

    def generate(self, test_cfg: Dict[str, Any]) -> Dict[str, Any]:
        problem_name, problem, primitives = self._load_generation_inputs()
        prompt_location = self._load_prompt_location()
        _, base_location, generated_ltm = self._load_output_info()
        human_intervention = self._load_human_intervention()
        test_info = self._load_test_info(test_cfg)

        provider_name = test_cfg.get("provider", "gpt-5.1")

        provider_factory = GeneratorProviderFactory(self.oraplan_config_path)
        provider = provider_factory.createProvider(provider_name)

        task_generator = provider.provideTextGenerator(
            provider,
            problem,
            primitives,
            test_info,
            base_location,
            self.oraplan_config_path,
            prompt_location,
            human_intervention,
        )

        start = time.perf_counter()
        generation_result = task_generator.generateTask()
        print(generation_result)
        generation_time = time.perf_counter() - start

        return {
            "problemName": problem_name,
            "generatedLtm": generated_ltm,
            "generationTime": generation_time,
        }

    def execute(self, generated_ltm: str, test_cfg: Dict[str, Any]) -> Dict[str, Any]:
        execution_cfg = get_first(CFG_EXECUTION, self.oraplan_cfg) or {}

        init_state_pddl = execution_cfg.get("initStatePddl")
        init_taskid_index = execution_cfg.get("initTaskidIndex", 1)

        attrs = ["$.ns", "$.task_transitions[0].taskdef.taskid"]
        if init_state_pddl:
            attrs.append(f"$.task_transitions[{init_taskid_index}].taskdef.taskid")

        rc = get_json_attrs(generated_ltm, attrs)
        app_ns = rc[0]
        taskid = rc[1]
        init_taskid = rc[2] if init_state_pddl else None

        BaseHelper.sys_cxt.register_app(generated_ltm)

        state_cxt = None
        if init_state_pddl:
            state_cxt = load_initial_context(
                util.resolve_env_variables(init_state_pddl),
                app_ns,
                init_taskid,
                test_cfg.get("testName", "generate_execute_test"),
            )

        start = time.perf_counter()
        result = BaseHelper.sys_cxt.task_runner.launch_app(
            BaseHelper.sys_cxt,
            app_ns,
            taskid,
            initial_statecxt=state_cxt,
        )
        execution_time = time.perf_counter() - start

        return {
            "executionTime": execution_time,
            "result": str(result),
            "appNs": app_ns,
            "taskid": taskid,
            "initTaskid": init_taskid,
        }

    def _output_json_path(self, test_cfg: Dict[str, Any]) -> str:
        output_cfg = get_first(CFG_OUTPUT_LOCATION, self.oraplan_cfg)
        output_json = output_cfg.get("testResultJson")

        if output_json is None:
            test_name = test_cfg.get("testName", "generate_execute_test")
            task_name, base_location, _ = self._load_output_info()
            output_json = str(Path(base_location).joinpath(task_name, f"{test_name}_result.json"))

        return util.resolve_env_variables(output_json)

    def run(self) -> Dict[str, Any]:
        test_cfg = self._load_test_config()
        test_name = test_cfg.get("testName", "generate_execute_test")
        test_type = test_cfg.get("singleLlmPrompt")        
        print("Test name", test_name)
        base_test_setup(test_name)

        try:
            gen_result = self.generate(test_cfg)
            final_result = {}
            final_result["testName"] = test_name
            final_result["problemName"] = gen_result["problemName"]
            final_result["generationTime"] = gen_result["generationTime"]
            final_result["totalTime"] = gen_result["generationTime"]
            if test_type == False:
                exe_result = self.execute(gen_result["generatedLtm"], test_cfg)
                final_result["executionTime"] = exe_result["executionTime"]
                final_result["totalTime"] = gen_result["generationTime"] + exe_result["executionTime"]
                final_result["execution"] = exe_result

            output_path = Path(self._output_json_path(test_cfg))
            output_path.parent.mkdir(parents=True, exist_ok=True)

            with open(output_path, "w") as f:
                json.dump(final_result, f, indent=2)

            return final_result

        finally:
            base_test_teardown(test_name)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--oraplanConfig", type=str, required=False, default="~/work/sciproj/oraplan/dev/oraplan-main/oraconfig.json")
    args = parser.parse_args()

    runner = GenerateExecuteRunner(args.oraplanConfig)
    result = runner.run()
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
