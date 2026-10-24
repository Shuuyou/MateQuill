"""Durable poster jobs: the queued twin of POST /api/posters."""
from fastapi.responses import JSONResponse


async def execute_poster_job(parameters: dict, request) -> JSONResponse:
    """Execute a claimed poster job through the shared poster pipeline.

    ``parameters`` is {'task', 'brief', 'source_run_id'} as enqueued by
    poster_routes.create_poster_job; ``request`` is the worker's synthetic
    Request kept only for budget-key utilities. Progress stages are reported
    inside the shared pipeline; the tenant usage unit is charged here exactly
    like the synchronous route does.
    """
    from .poster_routes import PosterRequest, _run_poster_pipeline
    from .generation_jobs import generation_budget
    payload=PosterRequest.model_validate({'task':parameters.get('task'),
        'brief':parameters.get('brief') or {},
        'source_run_id':parameters.get('source_run_id') or ''})
    with generation_budget():
        result=await _run_poster_pipeline(payload.task,payload.brief,payload.source_run_id,request)
    return JSONResponse(result)
