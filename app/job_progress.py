"""Stage-level progress events projected into the durable job body.

Pipeline code calls report() anywhere inside a worker-claimed job; the active
sink (bound by generation_jobs.execute_claimed) folds each event into the job
body under a monotonic seq so polling clients can render real progress. When
no job is active (sync endpoints, tests, scripts) reporting is a no-op.
"""
from contextvars import ContextVar
import time

# Canonical stage orders per operation. Percent is index-based, monotonic,
# and capped below 100 until the job finishes — never fake precision.
STAGE_ORDERS = {
    'content': ['understanding', 'retrieval', 'drafting', 'reviewing', 'repairing', 'finalizing'],
    'design': ['materials', 'copy', 'concepts', 'rendering', 'reviewing'],
    'poster': ['copy', 'layout', 'background_image', 'visual_review'],
    # Agent turns may run any pipeline in sequence; the union order keeps the
    # percent monotonic without assuming which tool the planner picks.
    'agent': ['understanding', 'materials', 'retrieval', 'copy', 'drafting',
              'concepts', 'layout', 'rendering', 'background_image',
              'reviewing', 'visual_review', 'repairing', 'finalizing'],
}
_sink = ContextVar('matequill_job_progress_sink', default=None)

_TERMINAL = {'succeeded', 'failed', 'cancelled'}
_DETAIL_LABELS = {'facts': '依据', 'assets': '素材', 'sources': '来源'}
_DETAIL_PHASES = {'store_research': '调研门店与周边'}


def _detail_text(detail):
    """Events carry display strings only; never leak structured internals."""
    if isinstance(detail, str):
        return detail[:240]
    if not isinstance(detail, dict):
        return None
    parts = []
    for key, value in detail.items():
        label = _DETAIL_LABELS.get(key)
        if label:
            parts.append(f'{label} {value}')
        elif key == 'phase':
            parts.append(_DETAIL_PHASES.get(str(value), str(value)))
        else:
            parts.append(f'{key} {value}')
    return ' · '.join(parts)[:240] or None


def bind(job_id, operation, *, resource='generation_job', terminal=None):
    """Return a token binding a persistence sink for report() callers."""
    order = STAGE_ORDERS.get(operation, [])
    state = {'seq': 0, 'percent': 0}
    target = resource
    terminal_states = _TERMINAL if terminal is None else terminal

    def sink(stage, detail, artifact):
        state['seq'] += 1
        index = order.index(stage) if stage in order else -1
        if index >= 0:
            state['percent'] = max(state['percent'], min(98, round(index / max(len(order), 1) * 100)))
        event = {'seq': state['seq'], 'ts': time.time(), 'stage': stage,
                 'percent': state['percent']}
        text = _detail_text(detail)
        if text:
            event['detail'] = text
        if artifact:
            event['artifact'] = artifact
        _persist(job_id, event, target, terminal_states)

    return _sink.set(sink)


def unbind(token):
    _sink.reset(token)


def report(stage, detail=None, artifact=None):
    """Emit a stage event. stage should be a canonical name when known."""
    sink = _sink.get()
    if sink is not None:
        sink(stage, detail, artifact)


def _persist(job_id, event, resource, terminal):
    from .tenancy import load_body, save_body
    try:
        body = load_body(resource, job_id, action='write')
    except Exception:
        return
    if body.get('status') in terminal:
        return
    body['progress'] = {k: event[k] for k in ('seq', 'ts', 'stage', 'percent') if k in event}
    events = list(body.get('events') or [])
    events.append(event)
    body['events'] = events[-100:]
    save_body(resource, job_id, body)
