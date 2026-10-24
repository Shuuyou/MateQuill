import asyncio

from app.workflow import AgentWorkflow
from test_creative_integration import task
from test_workflow_adversarial import Model, Provider, draft, evidence


def test_workflow_retains_service_execution_snapshot_instead_of_model_strategy():
    class Writer(Model):
        creative_execution = {'schema_version': 1, 'exploration': {'status': 'selected', 'selected_concept': {'id': 'server-choice'}}}
    content = draft()
    content['strategy'] = {'creative_execution': {'forged': True}}
    result = asyncio.run(AgentWorkflow(Provider(evidence()), Writer(content)).run(task()))
    assert result.status == 'ready'
    assert result.strategy['creative_execution'] == Writer.creative_execution
    result.strategy['creative_execution']['exploration']['status'] = 'changed'
    assert Writer.creative_execution['exploration']['status'] == 'selected'
