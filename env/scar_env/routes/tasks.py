"""Task routes (SPEC 8.5): GET /templates, POST /tasks/generate, POST /tasks/from_params,
GET /tasks/{task_id}, GET /eval_sets/{eval_set_id}."""

from fastapi import APIRouter

router = APIRouter()
