import json
import sqlite3
import pytest
from scripts.restart_final_upgrade_preview import close_interrupted


def database():
    db = sqlite3.connect(':memory:')
    for name in ('mq_agent_dispatch', 'mq_job_dispatch'):
        db.execute(f'CREATE TABLE {name}(id TEXT,status TEXT,claim_token TEXT,lease_until REAL)')
    db.execute('CREATE TABLE mq_resources(kind TEXT,id TEXT,body TEXT,version INTEGER,updated_at REAL)')
    return db


def put(db, kind, identifier, body):
    db.execute('INSERT INTO mq_resources VALUES(?,?,?,1,0)', (kind, identifier, json.dumps(body)))


def body(db, kind, identifier):
    return json.loads(db.execute('SELECT body FROM mq_resources WHERE kind=? AND id=?', (kind, identifier)).fetchone()[0])


def test_maintenance_closes_old_work_without_deleting_ready_artifacts_or_checkpoints():
    db = database()
    put(db, 'run', 'saved', {'status': 'ready', 'title': '已有文案'})
    put(db, 'design_work', 'checkpoint', {'plan': {'source': 'unchanged'}})
    put(db, 'agent_conversation', 'conversation', {'active_turn_id': 'turn', 'messages': [],
        'current_artifact': {'kind': 'run', 'id': 'saved'}})
    put(db, 'agent_turn', 'turn', {'conversation_id': 'conversation', 'status': 'running'})
    put(db, 'generation_job', 'job', {'status': 'running', 'parameters': {'design_id': 'checkpoint'}})
    db.execute("INSERT INTO mq_agent_dispatch VALUES('turn','running','claim-a',100)")
    db.execute("INSERT INTO mq_job_dispatch VALUES('job','queued',NULL,0)")
    result = close_interrupted(db, 20)
    assert len(result) == 2
    assert body(db, 'agent_turn', 'turn')['status'] == 'failed'
    assert body(db, 'generation_job', 'job')['status'] == 'cancelled'
    assert body(db, 'agent_conversation', 'conversation')['active_turn_id'] is None
    assert body(db, 'agent_conversation', 'conversation')['current_artifact']['id'] == 'saved'
    assert body(db, 'run', 'saved')['status'] == 'ready'
    assert body(db, 'design_work', 'checkpoint')['plan']['source'] == 'unchanged'
    assert close_interrupted(db, 30) == []


def test_missing_checkpoint_aborts_maintenance_transaction():
    db = database()
    db.execute("INSERT INTO mq_agent_dispatch VALUES('missing','running','claim',100)")
    db.commit()
    with pytest.raises(RuntimeError), db:
        close_interrupted(db, 20)
    assert db.execute('SELECT status FROM mq_agent_dispatch').fetchone()[0] == 'running'
