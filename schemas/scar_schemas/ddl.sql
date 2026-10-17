-- scar_schemas/ddl.sql (part 1 of 2) — SHARED CONTRACT
CREATE SCHEMA IF NOT EXISTS ledger;
CREATE SCHEMA IF NOT EXISTS app;

-- owner: Person 1
CREATE TABLE app.tasks (
  task_id TEXT PRIMARY KEY,
  company_id TEXT NOT NULL,
  split TEXT NOT NULL CHECK (split IN ('train','eval')),
  family TEXT NOT NULL,
  template_id TEXT NOT NULL,
  difficulty INT NOT NULL,
  origin TEXT NOT NULL,
  parent_failure_id TEXT,
  body JSONB NOT NULL,
  created_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX ON app.tasks (split, family, origin);

-- owner: Person 1
CREATE TABLE app.train_pool (
  position INT PRIMARY KEY,                      -- 0..TRAIN_POOL_SIZE-1
  task_id TEXT NOT NULL REFERENCES app.tasks(task_id)
);

-- owner: Person 1
CREATE TABLE app.eval_sets (
  eval_set_id TEXT PRIMARY KEY,
  body JSONB NOT NULL                            -- EvalSet
);

-- owner: Person 1 (written by /tools/execute)
CREATE TABLE app.sandbox_entries (
  entry_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  company_id TEXT NOT NULL,
  entry_date DATE NOT NULL,
  lines JSONB NOT NULL,                          -- list[JournalLine]
  created_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX ON app.sandbox_entries (run_id);

-- owner: Person 1 (arms B and C; system_v1 itself lives in a file owned by Person 2)
CREATE TABLE app.prompt_versions (
  prompt_version TEXT PRIMARY KEY,
  body JSONB NOT NULL,                           -- PromptVersion
  created_at TIMESTAMPTZ NOT NULL
);

-- owner: Person 1 (arm B)
CREATE TABLE app.lessons (
  lesson_id TEXT PRIMARY KEY,
  lineage TEXT NOT NULL,                         -- e.g. "B-s0"
  generation INT NOT NULL,
  failure_id TEXT NOT NULL,
  text TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL
);

-- owner: Person 2
CREATE TABLE app.runs (
  run_id TEXT PRIMARY KEY,
  task_id TEXT NOT NULL,
  model_version TEXT NOT NULL,
  prompt_version TEXT NOT NULL,
  arm TEXT NOT NULL,
  seed INT NOT NULL,
  generation INT NOT NULL,
  purpose TEXT NOT NULL,
  origin TEXT NOT NULL,
  split TEXT NOT NULL,
  family TEXT NOT NULL,
  passed BOOLEAN NOT NULL,
  stop_reason TEXT NOT NULL,
  body JSONB NOT NULL,                           -- RunResult
  created_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX ON app.runs (arm, seed, generation, purpose, passed);
CREATE INDEX ON app.runs (task_id, model_version, prompt_version);

-- owner: Person 2
CREATE TABLE app.failures (
  failure_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL UNIQUE,
  arm TEXT NOT NULL,
  seed INT NOT NULL,
  generation INT NOT NULL,
  included BOOLEAN NOT NULL,
  body JSONB NOT NULL,                           -- FailureRecord
  created_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX ON app.failures (arm, seed, generation, included);

-- owner: Person 3
CREATE TABLE app.training_examples (
  example_id TEXT PRIMARY KEY,
  snapshot_id TEXT NOT NULL,
  lineage TEXT NOT NULL,
  task_id TEXT NOT NULL,
  source TEXT NOT NULL,
  body JSONB NOT NULL,                           -- TrainingExample
  created_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX ON app.training_examples (lineage, snapshot_id);

-- owner: Person 3
CREATE TABLE app.model_versions (
  version TEXT PRIMARY KEY,
  status TEXT NOT NULL,
  body JSONB NOT NULL,                           -- ModelVersion
  created_at TIMESTAMPTZ NOT NULL
);

-- owner: Person 3
CREATE TABLE app.generations (
  generation_id TEXT PRIMARY KEY,
  arm TEXT NOT NULL,
  seed INT NOT NULL,
  generation INT NOT NULL,
  status TEXT NOT NULL,
  body JSONB NOT NULL,                           -- GenerationRecord
  updated_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX ON app.generations (arm, seed, generation);

-- scar_schemas/ddl.sql (part 2 of 2) — SHARED CONTRACT, owner: Person 1
CREATE TABLE ledger.companies (
  company_id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  industry TEXT,
  split TEXT NOT NULL CHECK (split IN ('train','eval'))
);

CREATE TABLE ledger.accounts (
  account_id TEXT PRIMARY KEY,
  company_id TEXT NOT NULL REFERENCES ledger.companies(company_id),
  name TEXT NOT NULL,
  account_type TEXT NOT NULL CHECK (account_type IN ('asset','liability','equity','income','expense'))
);

CREATE TABLE ledger.vendors (
  vendor_id TEXT PRIMARY KEY,
  company_id TEXT NOT NULL REFERENCES ledger.companies(company_id),
  name TEXT NOT NULL
);

CREATE TABLE ledger.transactions (
  txn_id TEXT PRIMARY KEY,
  company_id TEXT NOT NULL REFERENCES ledger.companies(company_id),
  txn_date DATE NOT NULL,
  amount NUMERIC(14,2) NOT NULL,          -- positive = money spent (7.4)
  account_id TEXT NOT NULL REFERENCES ledger.accounts(account_id),
  vendor_id TEXT REFERENCES ledger.vendors(vendor_id),
  description TEXT,
  txn_type TEXT
);
CREATE INDEX ON ledger.transactions (company_id, txn_date);

-- ground truth for planted anomalies; NEVER readable by the agent
CREATE TABLE ledger.planted_anomalies (
  anomaly_id TEXT PRIMARY KEY,
  company_id TEXT NOT NULL,
  kind TEXT NOT NULL,                     -- e.g. 'duplicate_payment'
  txn_ids JSONB NOT NULL,
  details JSONB NOT NULL DEFAULT '{}'
);

-- role used ONLY by the sql_query tool: three tables, current company only
CREATE ROLE scar_sql_tool NOLOGIN;
GRANT scar_sql_tool TO scar;              -- the app's DB user can SET ROLE to it
GRANT USAGE ON SCHEMA ledger TO scar_sql_tool;
GRANT SELECT ON ledger.accounts, ledger.vendors, ledger.transactions TO scar_sql_tool;
ALTER TABLE ledger.accounts ENABLE ROW LEVEL SECURITY;
ALTER TABLE ledger.vendors ENABLE ROW LEVEL SECURITY;
ALTER TABLE ledger.transactions ENABLE ROW LEVEL SECURITY;
CREATE POLICY company_only ON ledger.accounts FOR SELECT TO scar_sql_tool
  USING (company_id = current_setting('app.company_id', true));
CREATE POLICY company_only ON ledger.vendors FOR SELECT TO scar_sql_tool
  USING (company_id = current_setting('app.company_id', true));
CREATE POLICY company_only ON ledger.transactions FOR SELECT TO scar_sql_tool
  USING (company_id = current_setting('app.company_id', true));
