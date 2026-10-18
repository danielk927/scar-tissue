# scar_schemas/constants.py — SHARED CONTRACT
SCHEMA_VERSION = "1.3.0"
TOOLS_VERSION = "tools_v1"
BASE_PROMPT_VERSION = "system_v1"
EVAL_SET_ID = "eval_v1"

# splits and pools
SPLIT_SEED = 7                 # company → split assignment
EVAL_COMPANY_FRACTION = 0.30   # fraction of companies in eval split (min 2 eval companies)
TRAIN_POOL_SIZE = 2000         # pre-generated train traffic tasks
TRAIN_POOL_SEED = 42
EVAL_SET_SIZE = 300            # 100 per family
EVAL_SET_SEED = 1234

# agent
MAX_STEPS = 12
MAX_TOKENS_PER_TURN = 1024
TOOL_RESULT_MAX_CHARS = 6000
EVAL_TEMPERATURE = 0.0

# loop
SEEDS = (0, 1, 2)
N_GENERATIONS = 4              # generations 1..4 after generation 0
TRAFFIC_PER_GEN = 200
FAILURE_CAP_PER_GEN = 25       # max included failures used per generation (was 50; 1.1.0)
INCLUDE_LEARNABLE_P = 0.6
VARIANTS_PER_FAILURE = 5       # was 10 (1.1.0, SPEC 9.13 fallback)
K_SAMPLES = 4                  # was 8 (1.1.0, SPEC 9.13 fallback)
SAMPLE_TEMPERATURE = 0.8
MAX_EXAMPLES_PER_TASK = 2
REPLAY_RATIO = 1.0             # replay examples per self example
REGRESSION_SET_SIZE = 100
REGRESSION_TOLERANCE = 0.05     # candidate may trail the parent on the regression set by this (1.3.0)

# arm B (memory) and arm C (GEPA)
MEMORY_MAX_LESSONS = 20
GEPA_MAX_METRIC_CALLS = 150
GEPA_TRAIN_EXTRA = 50          # random train tasks added to GEPA trainset
GEPA_VAL_SIZE = 50
