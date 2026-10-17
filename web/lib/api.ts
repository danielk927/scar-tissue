// TypeScript views of the SPEC 8.2 contract models the web reads. Python's scar_schemas is the source of truth.

export type Arm = "A" | "B" | "C" | "D" | "E";

export interface ToolCall {
  id: string;
  name: string;
  arguments: Record<string, unknown>;
}

export interface ChatMessage {
  role: "system" | "user" | "assistant" | "tool";
  content: string | null;
  tool_calls: ToolCall[] | null;
  tool_call_id: string | null;
  name: string | null;
}

export interface RunResult {
  run_id: string;
  task_id: string;
  company_id: string;
  split: string;
  family: string;
  model_version: string;
  endpoint_model_id: string;
  prompt_version: string;
  arm: Arm;
  seed: number;
  generation: number;
  purpose: string;
  origin: string;
  attempt_index: number;
  messages: ChatMessage[];
  final_answer: Record<string, unknown> | null;
  stop_reason: string;
  passed: boolean;
  verify_detail: string;
  steps: number;
  input_tokens: number;
  output_tokens: number;
  latency_ms: number;
  sentry_trace_id: string | null;
  created_at: string;
}

export interface FailureRecord {
  failure_id: string;
  run_id: string;
  cause: string;
  cause_confidence: number;
  cause_probabilities: Record<string, number>;
  learnable_p: number;
  included: boolean;
  exclusion_reason: string | null;
  jev_model: string;
}

export interface GenerationRecord {
  generation_id: string;
  arm: Arm;
  seed: number;
  generation: number;
  status: string;
  parent_model_version: string;
  parent_prompt_version: string;
  candidate_model_version: string | null;
  candidate_prompt_version: string | null;
  current_model_version: string;
  current_prompt_version: string;
  traffic_runs: number;
  failures_mined: number;
  failures_included: number;
  variants_created: number;
  examples_built: number;
  eval_score: number | null;
  parent_eval_score: number | null;
  regression_score: number | null;
  notes: string;
  started_at: string;
  finished_at: string | null;
}

export interface Company {
  company_id: string;
  name: string;
  industry: string | null;
  split: string;
}

export interface JudgeParseResult {
  template_id: string | null;
  template_confidence: number;
  params: Record<string, unknown>;
  interpretation: string;
  valid: boolean;
  error: string | null;
}

export interface JudgeRunResult {
  task_id: string;
  run: RunResult;
  sentry_trace_url: string | null;
}

export const ARMS: Arm[] = ["A", "B", "C", "D", "E"];
export const SEEDS = [0, 1, 2];
export const FINISHED = new Set(["promoted", "rejected"]);

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(url, { cache: "no-store", ...init });
  } catch {
    throw new ApiError(503, `service unreachable: ${url}`);
  }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail ?? JSON.stringify(body);
    } catch {}
    throw new ApiError(res.status, typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return res.json() as Promise<T>;
}

export function getJSON<T>(url: string): Promise<T> {
  return request<T>(url);
}

export function postJSON<T>(url: string, body: unknown): Promise<T> {
  return request<T>(url, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
}

/** Base URLs: server components call services directly; the browser goes through the /api rewrites. */
export function serviceUrl(service: "agent" | "train" | "env"): string {
  if (typeof window !== "undefined") return `/api/${service}`;
  const env = {
    agent: process.env.AGENT_SERVICE_URL ?? "http://localhost:8002",
    train: process.env.TRAIN_SERVICE_URL ?? "http://localhost:8003",
    env: process.env.ENV_SERVICE_URL ?? "http://localhost:8001",
  };
  return env[service];
}

/** Score of the version in use after a generation: candidate if promoted, else the parent's (SPEC 9.9). */
export function currentScore(g: GenerationRecord): number | null {
  if (g.status === "promoted") return g.eval_score;
  if (g.status === "rejected") return g.parent_eval_score;
  return null;
}

/** Latest finished D-s0 generation + 1, matching the agent's judge flow (SPEC 9.12 step 5). */
export function nextJudgeGeneration(gens: GenerationRecord[]): number {
  const finished = gens.filter((g) => FINISHED.has(g.status)).map((g) => g.generation);
  return (finished.length ? Math.max(...finished) : 0) + 1;
}

export function traceUrl(traceId: string | null, org: string | undefined): string | null {
  // TODO(VERIFY): format from Sentry's frontend routes (docs/SENTRY.md section 10); confirm by clicking a trace
  return traceId && org ? `https://${org}.sentry.io/explore/traces/trace/${traceId}/` : null;
}
