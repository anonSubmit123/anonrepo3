# INSTALLATION

It is necessary to have a Linux system to run the system and the instructions
are specific to Ubuntu 24.04 LTS system. Other Linux systems may also work but
your setup may need to be fine-tuned to your specific version.

Set the directory where project is installed. Eg. Download the `dev` directory
as `~/work/sciproj/oraplan/dev` so that contents of `dev` directory appear under
`~/work/sciproj/oraplan/dev` and point `BASE_ORAPLAN` bash environment variable
to it. 

Ensure that micromamba is installed on the system and a micromamba environment
named `rl` is defined with the Python packages preinstalled. A
`util/micromamba-rlenv.txt` is provided to duplicate a sample run environment.

Micromamba installation typically puts micromamba specific environment setup in
`.bashrc`. However, a `util/.activate_umamba.sh` has been provided here that may
be copied to `~/.activate_umamba.sh` so that you can explicitly source it in
your terminal or scripts.

```bash
source ~/.activate_umamba.sh
micromamba create -n rl python=3.12 pip
micromamba run -n rl python -m pip install -r util/micromamba-rlenv.txt
micromamba activate rl
cp $INSTALL_PATH/util/.activate_umamba.sh ~/.activate_umamba.sh
```

Activate the micromamba environment using the following command and use it for
all terminals:

```bash
source ~/.activate_umamba.sh
micromamba activate rl
```

Additionally, external planners and API key setup is necessary to run any tests.
It is critical that all planners are installed and properly set up as per the
instructions of LAPKT and Fast Downward. Ensure that the `ORAPLAN_THIRD_PARTY`,
`ORAPLAN_TESTRESOURCES`, `LAPKT_PATH`, `PROMPT_LOCATION`, `TEST_LOCATION`, and
`SUBTASK_LOG_LOCATION` bash environment variables match your installation setup
and also commands are properly aliased. If these variables 
are not properly configured or these environment variables are not set up, it 
will result in error messages or unsuccessful planning. 

If running using an LLM running locally and not using an API key to connect with
OpenAI, Gemini, Claude, etc, it is necessary to use a machine with good GPU
support necessary to run the LLM. Ensure that appropriate support is given based
on the LLM used.

It is also necessary to properly configure the planner locations, prompt and input locations, 
and relevant output locations in `${BASE_ORAPLAN}/oraplan-main/oraconfig.json`.

# RUNNING EXPERIMENTS

To run the experiments, install the necessary components as described in the
installation section.

Most tests can be performed by the following command:

```bash
python ${BASE_ORAPLAN}/dev/oraplan-main/test/test_generate_execute.py --oraplanConfig ${BASE_ORAPLAN}/dev/oraplan-main/oraconfig.json
```
Also, ensure that a proper configuration `testConfig.json` file referenced in `${BASE_ORAPLAN}/dev/oraplan-main/oraconfig.json` is also present. By default, it is in `${BASE_ORAPLAN}/dev/oraplan-main/test` directory.
For example, the configuration in `testConfig.json` for ablating heterogeneous 
decomposition and only generating with PDDL can be done by setting `technologyType` to `pddl`.
