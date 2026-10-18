# Veri: verified notes (Person 3, P3-03)

Verified 2026-10-18 against the official Veri pages, the installed SDK source, Veri's open-source managed training runtime, and offline experiments on this laptop.
**No Veri API call has been made yet: there is no Veri API key on this machine.**
Every snippet is labelled **ran** (executed locally, offline) or **not run (no API key)**.
Anything still unverified is marked `TODO(VERIFY)`.

## Evidence keys

| Key | Source |
|---|---|
| [lahacks] | https://www.veri.studio/lahacks (LA Hacks starter pack and its embedded SKILL.md), fetched 2026-10-18 |
| [docs:/path] | https://docs.veri.studio/path, fetched 2026-10-18 (index: https://docs.veri.studio/llms.txt) |
| (sdk: file) | `veri-sdk==0.2.44` wheel from PyPI (uploaded 2026-10-17), file inside the wheel |
| (runner: symbol) | `veri_runner/training_runtime.py` of https://github.com/Veri-Studios/veri-runner at commit `7b876fc`, the open-source managed runtime that [docs:/training/datasets] links to |
| (trl: file) | `trl==1.7.1` wheel, the TRL version the managed runtime pins ([docs:/training/base-images]) |
| (vllm: file) | `vllm==0.27.1` sdist, the platform's default serving version ([docs:/deployments]) |
| (mlx: file) | `mlx-lm==0.32.2` wheel (latest on PyPI) |
| (exp N) | Section N of the offline experiment, described in "How the experiments were run" at the end |

## 1. Access

- PyPI name `veri-sdk`, import name `veri_sdk`, latest 0.2.44, Python >= 3.10 (PyPI JSON API).
  The LA Hacks SKILL.md asks for `sdk_version >= 0.2.44` [lahacks].
- Install the CLI and SDK with `uv tool install --upgrade 'veri-sdk[mcp]'` [docs:/quickstart].
- Sign in with `veri login` (browser tab, click Authorize), or headless `veri login --token vk_...` [lahacks].
- API keys are created at https://www.veri.studio/settings/api-keys [docs:/quickstart].
  They start with `vk_` and are sent as `Authorization: Bearer vk_...` (sdk: client.py `Client.__init__`).
- **The SDK reads `VERI_API_KEY`** (and optionally `VERI_API_URL`, default `https://api.veri.studio`); env vars override the CLI config file (sdk: cli/utils/config.py `load`).
  So the name in SPEC 7.5 and `.env.example` is correct.
- **No org or project id is needed.**
  `Client` sends only the bearer header (sdk: client.py).
  A normal API key "can do everything in your workspace" [docs:/secrets], and the workspace is the billing entity [docs:/billing].
- Team workspaces: any member can run jobs and deployments and redeem credit codes [docs:/billing].
- Credits: DM **@daniung** in the LA Hacks Discord for a code and redeem it at https://www.veri.studio/settings/billing **before the first run**; a job on a $0 balance boots and is then killed with `insufficient_credits` [lahacks].
  The credit amount is not stated anywhere (UNKNOWN).
  `veri billing balance` shows what is left [lahacks].
- Per-account limits: 4 in-flight training jobs and **2 active deployment replicas**; hitting one is a 429 with `CONCURRENT_JOB_LIMIT` or `ACTIVE_DEPLOYMENT_LIMIT` [lahacks].
  The docs say to contact Veri to raise the replica limit [docs:/deployments].
  The REST API allows 120 requests per 60 s per key [lahacks].
- Model access keys are API keys bound to one deployment; they can only call that deployment's chat routes [docs:/secrets].
  `client.api_keys.create(name, deployment_id=..., expires_in_seconds=...)` returns the plaintext key once [docs:/secrets].
  This is the right thing to put in `MODEL_API_KEY`.

Snippet, not run (no API key):

```python
from veri_sdk import Client

client = Client()                 # VERI_API_KEY from the environment, else the `veri login` config
print(client.me())                # cheapest "is this key valid" call (sdk: client.py)
print(client.billing.balance())
```

## 2. SFT training

### 2.1 Built-in SFT exists: `method="sft_text"`, LoRA via `lora_rank`

- Managed methods are `grpo`, `sft_text` and `dpo` on TRL trainers; `grpo_agentic` is not runnable [docs:/training/managed].
- `sft_text` rows carry `messages`, `text`, or `prompt` + `completion`; the chat template is applied automatically; `lora_rank`, `lora_alpha` give LoRA and `load_in_4bit` gives QLoRA [docs:/training/sft].
- The AWS managed runtime pins transformers 4.56.2 and TRL 1.7.1 [docs:/training/base-images].

### 2.2 Our SPEC 9.8 export line is not accepted as is

| # | Finding | Evidence |
|---|---|---|
| a | **Rejected** with `dataset_format_incompatible`: assistant tool-call messages have no `content` key (`to_openai_messages` omits `None`), and the runtime's chat detector requires `role` and `content` on every message. With an explicit `"content": null` the row is detected as chat and accepted. | (exp 1), (runner: `detect_dataset_format`) |
| b | Assistant `tool_calls` and `tool` messages pass through unchanged (`tool` is in the role map, other keys are kept). | (runner: `_ROLE_MAP`, `normalize_rows`) |
| c | A per-row `tools` column reaches the chat template: TRL passes `example["tools"]` to `apply_chat_template`, and `json.loads` it first if it is a string. | (trl: trainer/sft_trainer.py `tokenize_fn`) |
| d | **`tools` as a list is corrupted**: `datasets.Dataset.from_list` unifies the 6 tool schemas into one Arrow struct, so `get_accounts` comes back with 14 `null` properties belonging to other tools, which the template then renders. As a JSON string it round-trips exactly. | (exp 2) |
| e | **Loss is on the full conversation** for `messages` rows: the runtime never sets `assistant_only_loss` (TRL default `False`). Veri's own SFT demo says `train_on_responses_only` "is not exposed on the managed path, so loss is computed on the full conversation". For `prompt` + `completion` rows TRL defaults to completion-only loss. | (runner: `build_sft_config_kwargs`), (trl: trainer/sft_config.py), [docs:/demos/sft-unsloth] |
| f | **Token mismatch with inference**: vLLM parses tool-call argument strings into dicts before templating, and the template re-serializes them with `json.dumps` defaults (`{"a": 1}`), while `dumps_args` strings are compact (`{"a":1}`). Re-serializing with `json.dumps(json.loads(s), ensure_ascii=False)` makes the training text byte-identical to vLLM's render. | (vllm: entrypoints/chat_utils.py `_postprocess_messages`), (exp 3), (exp 6) |
| g | `seed` is only wired for GRPO, so managed SFT always uses TRL's default seed 42 and cannot honor "training seed equals the lineage seed" (SPEC 9.8). `lora_dropout` is honored (default 0.0). | (runner: `build_grpo_config_kwargs`, `_load_model_and_tokenizer`) |
| h | Do not set `use_unsloth` with `messages` rows: that path pre-renders the chat template without `tools`. | (runner: `prerender_chat_template_rows`) |

### 2.3 Exact assistant-only loss on the managed path: one row per assistant turn

Each row is exactly one inference call: `prompt` is the message list the agent sent, `completion` is the assistant message it got back.
TRL masks the prompt, so the loss covers exactly the assistant tokens including `<|im_end|>`.
On Qwen3-4B-Instruct-2507, (exp 6) showed: veri-runner accepts the rows (`prompt_completion`), every prompt is a token prefix of prompt + completion after the Arrow round trip, and the trained spans are identical to TRL's `assistant_only_loss` spans on the full trajectory (exp 4).
The cost is extra tokens, because each prefix is encoded again: 3,237 vs 1,194 tokens (2.7x) on the 3-call toy transcript.
The builder must drop rows longer than `max_seq_length`, or truncation can cut the completion.
This changes the SPEC 9.8 export line and transforms argument strings, so it needs a team decision (see "What the human must do").

Per-row transform, ran (offline, this is the code of exp 6):

```python
import json
from scar_schemas.openai_format import load_tools, to_openai_messages

def veri_rows(messages) -> list[dict]:
    """Managed-SFT rows for one passed run: one prompt/completion pair per assistant turn."""
    msgs = to_openai_messages(messages)
    for m in msgs:
        m.setdefault("content", None)                  # 2.2a
        for tc in m.get("tool_calls") or []:            # 2.2f: same text vLLM renders
            tc["function"]["arguments"] = json.dumps(json.loads(tc["function"]["arguments"]), ensure_ascii=False)
    tools = json.dumps(load_tools())                     # 2.2d
    return [{"prompt": msgs[:k], "completion": [m], "tools": tools}
            for k, m in enumerate(msgs) if m["role"] == "assistant"]
```

Submit, not run (no API key):

```python
ds = client.datasets.upload("D-s0-g1.jsonl", name="D-s0-g1")   # content-addressed: same bytes, same id
job = client.training_jobs.create(
    base_model="Qwen/Qwen3-4B-Instruct-2507", dataset_id=ds.id, output_name="D-s0-g1",
    method="sft_text", gpu_type="H100-80GB", gpu_count=1, provider="vast",
    hyperparameters={"lora_rank": 16, "lora_alpha": 32, "lora_dropout": 0.05,
                     "learning_rate": 1e-4, "num_epochs": 2, "max_seq_length": 4096},
)
job.wait(poll_interval=15)          # streams logs; Ctrl+C cancels the job (sdk: TrainingJob.wait)
```

Signatures are from (sdk: client.py `_Datasets.upload`, `_TrainingJobs.create`); dedup is from [docs:/training/datasets].
`TODO(VERIFY)`: the control plane accepts `lora_dropout` for `sft_text` (the runtime reads it; the API docs do not list it).

### 2.4 Custom script (`create_custom_script`)

Signature (sdk: client.py `_TrainingJobs.create_custom_script`):
`create_custom_script(entrypoint, code_artifact_id, *, gpu_type, gpu_count=1, base_image="veri/base", output_name="default", deps=None, env=None, dataset_id=None, provider=None, requires_heartbeat=True, num_nodes=1)`.

- `code_artifact_id` comes from `client.code_artifacts.upload(dir)`, which tars and gzips the directory and posts it to `/v1/code_artifacts` (sdk: client.py `_CodeArtifacts.upload`).
- `deps` is `{"kind": "requirements", "content": "<requirements.txt text>"}` (sdk: cli/commands/script.py).
- `base_image` is one of `veri/base`, `veri/base-vllm`, `veri/base-sglang`, `veri/base-claude-agent` [docs:/training/custom-script].
  `veri/base` has torch 2.8.0 (cu128), transformers 4.56.2, datasets 4.0.0, peft 0.17.1 and no TRL [docs:/training/base-images].
- `gpu_type` values: `L4-24GB` and `A10G-24GB` (1 or 4 per box on AWS), `A100-80GB` and `H100-80GB` (8 per box on AWS; 1, 2, 4 or 8 on `vast`; H100 x1 on `digitalocean`), `H200-141GB` (x1 on `digitalocean`) [lahacks], [docs:/training/providers].
  Custom scripts are NVIDIA-only [docs:/training/custom-script].
- `env` is a dict sent as `script.env` (sdk); the docs treat it as container environment variables ("Any `WANDB_*` value you set yourself in `env` takes precedence") [docs:/training/custom-script].
  Veri also injects `VERI_DATA_DIR` (attached dataset), `VERI_OUTPUT_DIR` (captured to the account), `VERI_METRICS_FILE` and `VERI_NUM_GPUS` [docs:/training/custom-script], plus `VERI_CHECKPOINT_DIR` for `checkpoint-<step>` dirs [docs:/training/checkpoints].
- `output_name` is sent as-is in the job body (sdk); exit code 0 marks the job completed [docs:/training/custom-script].
- Outputs: everything under `VERI_OUTPUT_DIR` is captured and custom-script checkpoints are downloadable like managed ones [docs:/training/custom-script], [docs:/training/checkpoints]; `job.download(output_dir)` fetches the files of the job's model manifest (sdk: `TrainingJob.download`).
  `TODO(VERIFY)`: `job.download` returns the `VERI_OUTPUT_DIR` files for a custom-script job.
- **Blocking limit for serving on Veri**: a job that "ran a custom script ... cannot be saved as a model" [docs:/deployments/custom-models], and only a saved model with `artifact_type="adapter"` can be attached to a multi-adapter deployment [docs:/deployments/api].
  So a custom-script adapter can only be served on our own vLLM (section 6), or merged, pushed to a public HF repo and deployed as one Veri deployment per version.

Minimal TRL script: loss only on assistant tokens, the model's own chat template, tools per example.
Ran locally on CPU with `trl-internal-testing/tiny-Qwen3ForCausalLM` plus the real `Qwen/Qwen3-4B-Instruct-2507` tokenizer (trl 1.7.1, transformers 4.56.2, peft 0.17.1, 4 export lines built with `scar_schemas`): 1,137 tokens per example, 115 of them trained, adapter saved with the original chat template.
Not run on Veri (no API key).

```python
"""train_sft.py: LoRA SFT on SPEC 9.8 export rows, loss on assistant tokens only (TRL 1.7.1)."""
import json
import os
from pathlib import Path

import torch
from datasets import Dataset
from peft import LoraConfig
from transformers import AutoModelForCausalLM, AutoTokenizer
from trl import SFTConfig, SFTTrainer

HERE = Path(__file__).parent
BASE = os.environ["BASE_MODEL_HF_ID"]
TOKENIZER = os.environ.get("TOKENIZER_HF_ID", BASE)  # local smoke test only
TRAIN_FILE = Path(os.environ.get("TRAIN_FILE", HERE / "train.jsonl"))
OUT = Path(os.environ.get("VERI_OUTPUT_DIR", HERE / "out"))
SEED = int(os.environ["TRAIN_SEED"])


def as_served(messages: list[dict]) -> list[dict]:
    """Render tool-call arguments exactly as vLLM does at inference (2.2f), keeping them strings for Arrow."""
    out = json.loads(json.dumps(messages))
    for m in out:
        for tc in m.get("tool_calls") or []:
            tc["function"]["arguments"] = json.dumps(json.loads(tc["function"]["arguments"]), ensure_ascii=False)
    return out


rows = []
for line in TRAIN_FILE.read_text().splitlines():
    ex = json.loads(line)
    rows.append({"messages": as_served(ex["messages"]), "tools": json.dumps(ex["tools"])})  # 2.2d

tokenizer = AutoTokenizer.from_pretrained(TOKENIZER)
model = AutoModelForCausalLM.from_pretrained(BASE, dtype=torch.bfloat16 if torch.cuda.is_available() else None)

trainer = SFTTrainer(
    model=model,
    processing_class=tokenizer,
    train_dataset=Dataset.from_list(rows),
    peft_config=LoraConfig(r=16, lora_alpha=32, lora_dropout=0.05, task_type="CAUSAL_LM",
                           target_modules=["q_proj", "k_proj", "v_proj", "o_proj",
                                           "gate_proj", "up_proj", "down_proj"]),
    args=SFTConfig(
        output_dir=str(OUT / "trainer"),
        assistant_only_loss=True,  # TRL swaps in its Qwen3 training template with generation markers
        max_length=4096,
        learning_rate=1e-4,
        num_train_epochs=2,
        per_device_train_batch_size=1,
        gradient_accumulation_steps=8,
        gradient_checkpointing=True,
        bf16=torch.cuda.is_available(),
        seed=SEED,
        logging_steps=1,
        save_strategy="no",
        report_to="none",
    ),
)
first = trainer.train_dataset[0]
print(f"example 0: {len(first['input_ids'])} tokens, {sum(first['assistant_masks'])} trained")
trainer.train()
trainer.save_model(str(OUT / "adapter"))
tokenizer.save_pretrained(str(OUT / "adapter"))
```

`assistant_only_loss=True` works on Qwen3, Qwen3-2507 and Qwen3.5 because TRL 1.7.1 ships training templates with `{% generation %}` markers for them and swaps one in automatically (trl: chat_template_utils.py `get_training_chat_template`, trainer/sft_trainer.py).

Submit, not run (no API key):

```python
art = client.code_artifacts.upload("train/job")      # dir with train_sft.py and train.jsonl
job = client.training_jobs.create_custom_script(
    entrypoint="python train_sft.py", code_artifact_id=art,
    gpu_type="L4-24GB", gpu_count=1, base_image="veri/base", output_name="D-s0-g1",
    deps={"kind": "requirements", "content": "trl==1.7.1\n"},   # pulls datasets>=4.7.0 over the image's 4.0.0
    env={"BASE_MODEL_HF_ID": "Qwen/Qwen3-4B-Instruct-2507", "TRAIN_SEED": "0"},
)
job.wait(poll_interval=15)
job.download(output_dir="adapters")                   # -> adapters/<job_id>/adapter/...
```

`TODO(VERIFY)`: `trl==1.7.1` installs cleanly on `veri/base`; the code artifact size limit (else attach the data with `dataset_id=` and read it from `VERI_DATA_DIR`, whose file name is also unverified).

## 3. Base models

- Veri has no fixed model list for training: any ungated Hugging Face model the runtime can load works, and gated models fail after the GPU boots [lahacks].
  The weekend defaults name `Qwen/Qwen3-0.6B`, `Qwen3-1.7B` and `Qwen3-4B` on 1x L4 [lahacks], and Veri's SFT demo trains `Qwen/Qwen3-4B-Instruct-2507` on 1x L4 [docs:/demos/sft-unsloth].
- The training ceiling is transformers 4.56.2 (managed runtime and `veri/base`): it knows `qwen3` but not `qwen3_5` (ran: `CONFIG_MAPPING_NAMES` in transformers 4.56.2).
- The serving ceiling is vLLM 0.27.1, or a newer official image via `vllm_image` [docs:/deployments].

| | `Qwen/Qwen3.5-4B` | `Qwen/Qwen3-4B-Instruct-2507` | `Qwen/Qwen3-4B` |
|---|---|---|---|
| Hugging Face facts (HF API) | `qwen3_5`, `Qwen3_5ForConditionalGeneration` (vision-language), 4.66B params, hybrid linear/full attention, Apache-2.0, ungated | `qwen3`, `Qwen3ForCausalLM`, 4.02B, Apache-2.0, ungated | same architecture as 2507 |
| Trainable on Veri managed runtime | **No**: transformers 4.56.2 lacks `qwen3_5` (ran); the model card asks for the latest transformers | **Yes** (Veri's SFT demo) | Yes |
| Thinking | On by default; the generation prompt ends with `<think>\n` (exp 5) | None (instruct-only) | On by default; Veri adds `--reasoning-parser qwen3` for `Qwen/Qwen3-*` and returns the thinking in `message.reasoning` [docs:/deployments/openai-compat], which our transcripts do not store (R7) |
| Chat template supports tools | Yes (exp 5) | Yes (exp 3) | Yes |
| Tool-call format | XML: `<tool_call>\n<function=NAME>\n<parameter=KEY>\nVALUE\n</parameter>...` (exp 5) | Hermes JSON: `<tool_call>\n{"name": ..., "arguments": {...}}\n</tool_call>` (exp 4) | Hermes JSON |
| vLLM tool parser | `qwen3_coder` (model card; registered in vLLM 0.27.1, vllm: tool_parsers/__init__.py) | `hermes` ([docs:/deployments/openai-compat]; [lahacks]) | `hermes` |
| Our string tool-call arguments | **Template crashes**: `TypeError: Can only get item pairs from a mapping` (exp 5) | Renders (compact, see 2.2f) | Renders |
| vLLM LoRA | Marked LoRA-capable (vllm: docs/models/supported_models.md) | Yes, `Qwen3ForCausalLM` (same file) | Yes |

Qwen3.5-4B exists, but it is not trainable on the managed runtime, thinks by default, and needs an export transform before its template even renders our transcripts.
Qwen3-4B-Instruct-2507 is the closest small tool-calling Qwen that Veri trains and serves today.
Its template is prefix-preserving: every per-turn inference prompt is a prefix of the full render (exp 3).
Baseline accuracy on our tasks is not measured yet (needs a live endpoint; P1-07, P3-04).

## 4. Deployment

- Create with `client.deployments.create(model, name, *, gpu, source="training_job" | "huggingface" | "custom_model", min_replicas=..., max_replicas=..., vllm_extra_args=[...], vllm_image=..., ...)` (sdk: client.py `_Deployments.create`).
  vLLM is the only engine [docs:/deployments].
- Base URL for the OpenAI SDK: `https://api.veri.studio/v1/deployments/{deployment_id}`; auth header `Authorization: Bearer vk_...` (full key or model access key) [docs:/deployments/openai-compat], [docs:/secrets].
  The URL is fixed for the deployment's lifetime, across parks and wakes [docs:/deployments/hosting].
- **Tool calling works but must be switched on**: `tools` and `tool_choice` are forwarded to vLLM untouched, and the deployment needs `vllm_extra_args=["--enable-auto-tool-choice", "--tool-call-parser", "hermes"]`, otherwise vLLM rejects tool requests [docs:/deployments/openai-compat].
  Both flags are on the allow-list [docs:/deployments/api].
  `finish_reason` is `tool_calls` when the model calls a tool [docs:/deployments/openai-compat].
- The `model` field: a single-model deployment rewrites any string to its served name; a multi-adapter fleet routes by `model` and rejects unknown names [docs:/deployments/openai-compat].
- A LoRA job, or a model saved from one, cannot be deployed on its own (400) [docs:/deployments/api].
  Two ways to serve one:
  1. **Multi-adapter fleet (beta, API-only).**
     Create the deployment with `"serving_mode": "multi_adapter"` (defaults: `max_loras` 32 on GPU, `max_lora_rank` 64, `max_cpu_loras` 128).
     The SDK's `create()` has no `serving_mode` argument, so this one call is a raw POST [docs:/deployments/api].
     Save each LoRA training job as an adapter model with `client.models.create_from_training_job(job_id, name=..., artifact="adapter")`, then `client.deployments.attach_adapter(dep_id, serve_name=..., model_id=...)` (sdk: client.py).
     An attachment starts `attaching` and becomes `active` once every replica has loaded it; until then requests for it get 503 with `Retry-After: 5` [docs:/deployments/api].
     Up to 128 attachments per deployment; `serve_name` is 1 to 64 chars of `[A-Za-z0-9._-]`, so `D-s0-g2` is valid [docs:/api-reference/deployments/attach-an-adapter].
     `flip_adapter`, `rollback_adapter` and `detach_adapter` also exist (sdk).
     The response's `model` field shows an internal versioned name, not the alias [docs:/deployments/api].
  2. **Merged weights.**
     Submit the job with `hf_push={"repo": "<user>/<name>", "artifact": "merged", "private": False}` (needs `veri hf set` once), then deploy the repo with `source="huggingface"` [lahacks], (sdk: `_TrainingJobs.create`).
     That is one deployment per version, so the 2-replica limit allows only two versions at a time.
- **One deployment serving several adapters by `model` name: yes, the fleet.**
  Adding a version is one more `attach_adapter` with a new `serve_name`, with no redeploy; our registry never reuses a version name, so `flip_adapter` is not needed.
- Cold start: small Hugging Face models take about 1 to 3 minutes from create to `serving` [docs:/deployments/hosting].
  A `min_replicas: 0` deployment parks after an idle window (default 3600 s, minimum 300 s); the next request wakes it and gets 503 `deployment_waking` with `Retry-After` for a few minutes, longer than the OpenAI client retries [docs:/deployments], [docs:/deployments/openai-compat].
  So keep `min_replicas=1` while the loop runs and switch to 0 (PATCH via `client.deployments.update`) when idle.
- Billing is per replica per minute from box launch; `min_replicas >= 1` never stops billing on its own; `stop` is permanent [docs:/deployments].
- `GET .../models` is not served [lahacks].

Registry mapping for `app.model_versions` with a fleet: `endpoint_base_url = https://api.veri.studio/v1/deployments/{fleet_id}`, `endpoint_model_id = serve_name` for adapters, and the fleet's base alias for `base`.
`TODO(VERIFY)`: the base alias string (probably the deployment `name`; the docs only say a `serve_name` "must not collide with the deployment's base alias").
`MODEL_API_KEY` is a model access key for the fleet.
The agent needs no change: it already sends `base_url`, `model` and one key (agent/scar_agent/registry.py, loop.py).

Fleet setup and smoke test, not run (no API key):

```python
import os
import httpx
from openai import OpenAI
from scar_schemas.openai_format import load_tools
from veri_sdk import Client

client = Client()
r = httpx.post("https://api.veri.studio/v1/deployments", timeout=60,
               headers={"Authorization": f"Bearer {os.environ['VERI_API_KEY']}"},
               json={"model": "Qwen/Qwen3-4B-Instruct-2507", "source": "huggingface", "name": "scar-base",
                     "gpu": {"gpu_type": "L4-24GB", "gpu_count": 1}, "serving_mode": "multi_adapter",
                     "min_replicas": 1, "max_replicas": 1,
                     "vllm_extra_args": ["--enable-auto-tool-choice", "--tool-call-parser", "hermes"]})
r.raise_for_status()
dep = client.deployments.get(r.json()["id"]).wait(poll_interval=10)       # until serving
m = client.models.create_from_training_job(job.id, name="D-s0-g1", artifact="adapter")
client.deployments.attach_adapter(dep.id, serve_name="D-s0-g1", model_id=m.id)
# poll client.deployments.adapters(dep.id) until this attachment's status == "active"
key = client.api_keys.create("scar-agent", deployment_id=dep.id)["key"]    # -> MODEL_API_KEY
oai = OpenAI(base_url=f"https://api.veri.studio/v1/deployments/{dep.id}", api_key=key)
resp = oai.chat.completions.create(
    model="D-s0-g1", tools=load_tools(), tool_choice="auto", temperature=0, max_tokens=1024,
    messages=[{"role": "system", "content": "..."}, {"role": "user", "content": "Company: ...\n\n..."}])
assert resp.choices[0].message.tool_calls                                  # SPEC 9.8 pre-registration check
```

`TODO(VERIFY)`: that `serving_mode: multi_adapter` accepts `vllm_extra_args` together, and that fleets are enabled for our workspace (beta).

## 5. Timing and cost

GPU prices for this weekend, per GPU-hour [lahacks]:

| Provider | GPU | GPUs per box | $/GPU-hour | Train | Serve | Availability (Veri's estimate) |
|---|---|---|---|---|---|---|
| `aws` (default) | L4-24GB | 1, 4 | 1.05 | yes | yes | 1x plenty; 4x scarce |
| `aws` | A10G-24GB | 1, 4 | 1.31 | yes | yes | same pool as L4 |
| `aws` | A100-80GB / H100-80GB | 8 only | 4.46 / 8.94 | yes | yes | rare |
| `vast` | A100-80GB / H100-80GB | 1, 2, 4, 8 | about 1.39 / 3.36 | yes | no | usually free; boots in 5 to 15 min |
| `digitalocean` | H100-80GB | 1 | 5.73 | yes | yes | often none free |
| `digitalocean` | H200-141GB | 1 | 5.81 | yes | yes | usually free |

- Training is billed per second from the moment a GPU is acquired; a job that never gets one is not billed [docs:/training/managed].
  Each job also pays a flat checkpoint-storage charge of about $0.24 [docs:/billing].
- Veri's worked example: an L4 job that boots in about 6 minutes and trains for 60 is billed $1.39 [docs:/billing].
- AWS and DigitalOcean boot in about 5 minutes, Vast in 5 to 15 [lahacks].
- Serving: L4 x1 at $1.05/hour per replica while up [lahacks], [docs:/deployments].

Estimate for one generation of one lineage, **not measured**:
assume 1,500 passed trajectories of about 3,000 tokens (to be measured on real runs; the toy transcript is 1,194), 2 epochs, and an assumed LoRA throughput on a 4B model of roughly 1.5k to 2k tokens/s on an L4 and 10k to 15k on an H100.

| Path | Tokens | 1x L4 | 1x H100 (vast) |
|---|---|---|---|
| Custom script, full trajectory, assistant mask | about 9M | about 1.3 to 1.7 h, about $2 | about 10 to 15 min, under $1 |
| Managed, one row per assistant turn (2.7x) | about 24M | about 3.5 to 4.5 h, about $4 to 5 | about 30 to 40 min, about $2 |

Add boot time and the model download to each.
P3-05 must replace these numbers with a measured toy job (log `job.duration_seconds` and `job.cost_usd`, both on the SDK's `TrainingJob`).

## 6. vLLM fallback (F16)

All from (vllm: docs/features/lora.md, docs/features/tool_calling.md, tool_parsers/__init__.py, entrypoints/chat_utils.py); commands not run (no GPU box).

```bash
VLLM_ALLOW_RUNTIME_LORA_UPDATING=True vllm serve Qwen/Qwen3-4B-Instruct-2507 \
  --enable-lora --max-lora-rank 16 --max-loras 8 \
  --lora-modules D-s0-g1=/adapters/D-s0-g1 \
  --enable-auto-tool-choice --tool-call-parser hermes \
  --api-key "$MODEL_API_KEY"

# add a version at runtime (no restart), then call it with model="D-s0-g2"
curl -X POST http://HOST:8000/v1/load_lora_adapter -H "Authorization: Bearer $MODEL_API_KEY" \
  -H "Content-Type: application/json" -d '{"lora_name": "D-s0-g2", "lora_path": "/adapters/D-s0-g2"}'
```

- Adapters are requested by passing their name as `model`, and `/v1/models` lists them.
- Runtime loading exists: `VLLM_ALLOW_RUNTIME_LORA_UPDATING=True` enables `POST /v1/load_lora_adapter` and `/v1/unload_lora_adapter` with `{"lora_name", "lora_path"}`; vLLM warns it is for trusted, isolated environments only.
- Tool-call parser: `hermes` for Qwen3-4B-Instruct-2507 (Hermes-style `<tool_call>` JSON, exp 4); `qwen3_coder` for Qwen3.5 (model card), which vLLM 0.27.1 registers.
- Adapters from either Veri path work here: managed jobs download as a Hugging Face checkpoint with the adapter config [docs:/training/managed], and the custom script saves a PEFT adapter.
- Where it could run: a Vultr GPU (SPEC Section 5, Best Use of Vultr).
  Not signed up; a new vendor needs the team's agreement (R12).
- **Cross-team finding for Person 2 (source read, applies to Veri deployments and our own vLLM):** vLLM 0.27.1 calls `json.loads` on every assistant tool-call argument string in the request history with no error handling (`_postprocess_messages`).
  If the model ever emits invalid JSON arguments, the agent stores `{"__raw__": s}`, re-sends `s`, and the next model call fails, so the run ends as `model_error` instead of letting the model recover.
  Such runs never pass, so they never reach training data, but Jev will see them as model errors.

## 7. Local fallback (mlx-lm), facts only

All from (mlx: server.py, tokenizer_utils.py, tuner/utils.py), version 0.32.2; nothing was run and `/Users/doyoonkim/Qwen3.8-27B-Uncensored-MLX` was not loaded.

- `mlx_lm.server` serves an OpenAI-style `POST /v1/chat/completions` and `GET /v1/models`.
- Tool calling: it accepts `tools`, returns `tool_calls` with `finish_reason="tool_calls"`, and picks a parser from the chat template (`json_tools` for `<tool_call>` JSON templates such as Qwen3-2507, `qwen3_coder` for Qwen3.5).
- LoRA: `--adapter-path` loads one adapter at startup; a request can also send a non-standard `adapters` field.
  Only one (model, adapter) pair is in memory at a time and changing it reloads the model.
  A request's `model` value other than the default is treated as a model path to load, so the agent's `endpoint_model_id` would have to be `default_model` with one server per version.
- Adapters must be in MLX's own format (`adapter_config.json` + `adapters.safetensors`); a PEFT adapter from Veri is not loadable as is (`TODO(VERIFY)` a conversion path).
- `--chat-template-args '{"enable_thinking": false}'` exists for thinking models.

## Recommended plan

**D-04 (base model): `Qwen/Qwen3-4B-Instruct-2507`.**
Veri trains it on the managed runtime and serves it on 1x L4, it does not think (so transcripts hold everything it generated), its Hermes tool calls parse with vLLM's `hermes` parser, its template is prefix-preserving, and TRL has a verified assistant-mask template for it.
Qwen3.5-4B is out for now (section 3).
Confirm at P3-04 that the base failure rate lands in the 20 to 85% band (SPEC 15).

**D-03 (serving): one Veri multi-adapter fleet, gated on a P3-05 smoke test; vLLM on a GPU box as the fallback.**
One fleet deployment serves `base` and every adapter version by `model` name, which fits the 2-replica account limit and keeps everything on Veri (Veri prize, no new vendor).
Training for that path is managed `sft_text` with LoRA and one prompt/completion row per assistant turn (2.3), run on a 1x H100 (`vast`) for speed, because only managed LoRA jobs can be saved as attachable adapters.
If the fleet fails the smoke test (beta, `TODO(VERIFY)` items in section 4), switch training to the custom script (2.4: exact masking, full trajectories, lineage seed, cheaper on an L4) and serve with vLLM on a GPU box (section 6).

P3-05 smoke test, in order:
1. `client.me()` and `client.billing.balance()` succeed.
2. Create the fleet for the base model with the tool-parser flags; time create to `serving`; register `base`.
3. Run the agent on 3 seed tasks against `base`; confirm parsed tool calls.
4. Build the per-turn JSONL from the passed runs, train a tiny managed `sft_text` LoRA job (`max_steps` small), and record duration and cost.
5. Save it with `artifact="adapter"`, attach it as `D-smoke-g1`, wait for `active`, send one tool request, and confirm a tool call parses (SPEC 9.8).
6. Record the timings here and decide D-03 in `docs/DECISIONS.md`.

Serving capacity is the next risk: SPEC 9.13 is up to about 5,000 agent runs per lineage per generation, all through one L4 replica.
Ask Veri to raise the replica limit; `--data-parallel-size` is an allowed vLLM flag for a 4x L4 box, which still counts as one replica, but 4x boxes are scarce [docs:/deployments/api], [lahacks].

## What the human must do

1. Get credits: DM **@daniung** in the LA Hacks Discord for a credit code.
2. Sign in at https://www.veri.studio (or run `veri login`), create a team workspace for the three of us if you want shared credits, and redeem the code at https://www.veri.studio/settings/billing in that workspace **before** any job runs.
3. Create an API key at https://www.veri.studio/settings/api-keys and put it in the repo-root `.env` as `VERI_API_KEY=vk_...` (never commit it).
4. Install the CLI with `uv tool install --upgrade 'veri-sdk[mcp]'` and check `veri whoami` and `veri billing balance`.
5. Ask @daniung: (a) is `serving_mode: "multi_adapter"` enabled for our workspace, and does it accept `vllm_extra_args` with `--enable-auto-tool-choice --tool-call-parser hermes`; (b) can our active-replica limit go above 2; (c) how many credits we get; (d) are the `vast` and `digitalocean` training providers enabled for us.
6. Team decision (SPEC change, owner P3): replace the SPEC 9.8 export line with the per-turn rows of 2.3 for Veri jobs, including the argument re-serialization (R7: training tokens then match inference exactly), and accept managed SFT's fixed seed 42 (2.2g); or choose the custom-script path and approve a GPU box (Vultr) for vLLM (R12).
7. After D-04 is agreed, set `BASE_MODEL_HF_ID=Qwen/Qwen3-4B-Instruct-2507` in `.env`; after the fleet exists, set `MODEL_API_KEY` to its model access key.

## How the experiments were run

All offline on this laptop with `uv run --no-project` (Python 3.11), using the worktree's `scar_schemas` package; scripts were kept in the session scratchpad, and the code that matters is reproduced above.
The test transcript was built with `ChatMessage`/`ToolCall` and converted with `to_openai_messages`: `system_v1`, a user question, `get_accounts`, `get_transactions` with 3 arguments, and `submit_answer` (with assistant text), each followed by its tool result; `tools` = `load_tools()`.

| Exp | What ran | Result |
|---|---|---|
| 1 | veri-runner's `detect_dataset_format`, `normalize_rows`, `validate_rows_for_method("sft_text")` on our export row | `unknown`, rejected; with `"content": null` on every message: `chatml`, accepted |
| 2 | `datasets.Dataset.from_list` on the row | `tools` list comes back null-filled; a JSON-string `tools` round-trips exactly |
| 3 | `Qwen/Qwen3-4B-Instruct-2507` tokenizer, transformers 4.56.2: our string arguments vs vLLM-style dict arguments; per-turn prompts with `add_generation_prompt=True` | renders differ (compact vs spaced JSON); every per-turn prompt is a prefix of the full render |
| 4 | TRL 1.7.1's `qwen3_instruct_2507_training.jinja` with `return_assistant_tokens_mask=True` | renders the same text as the original template; 108 of 1,194 tokens masked in, exactly the 3 assistant turns including `<|im_end|>` |
| 5 | `Qwen/Qwen3.5-4B` tokenizer (transformers 4.56.2) | string arguments raise `TypeError`; dict arguments render the XML tool-call format; default generation prompt opens `<think>` |
| 6 | Per-turn prompt/completion rows (2.3) through veri-runner and Arrow, tokenized with the Qwen3-2507 template | accepted (`prompt_completion`); prompt is a token prefix in every row; completion spans equal exp 4's assistant spans; respaced arguments render byte-identical to the dict render; absent `content` renders the same as `null` |
| - | `train_sft.py` (2.4) on CPU with `trl-internal-testing/tiny-Qwen3ForCausalLM` | trained 2 steps, 115 of 1,137 tokens per example masked in, PEFT adapter with the 7 projection modules saved |
