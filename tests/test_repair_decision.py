"""Accepted prose is not regenerated for an advisory heuristic alone."""
from app.models import AuditItem
from test_marketing_generation_loop import ReviewedModel, execute


def advice(monkeypatch):
    monkeypatch.setattr('app.workflow.editorial_review', lambda task, result: [
        AuditItem(severity='warning', category='editorial', message='段落可更简洁')])


def test_accepted_draft_keeps_advice_without_extra_paid_rewrite(monkeypatch):
    advice(monkeypatch)
    model = ReviewedModel(['accept'])
    result = execute(model)
    assert result.status == 'ready'
    assert model.repairs == model.reselections == 0
    assert model.verifications == 1
    assert not result.strategy['repair_decision']['required']
    assert result.strategy['repair_decision']['editorial_advice']
    assert result.editorial_review[0].severity == 'warning'


def test_advice_never_suppresses_real_rejected_review(monkeypatch):
    advice(monkeypatch)
    model = ReviewedModel(['revise', 'accept'])
    result = execute(model)
    assert result.status == 'ready'
    assert model.repairs == 1 and model.verifications == 2
    assert result.strategy['repair_decision']['required']
    assert result.strategy['repair_decision']['errors']


def test_accepted_marketing_review_does_not_override_factual_error(monkeypatch):
    model = ReviewedModel(['accept', 'accept'])
    original = model.verify

    async def verify(result, bundle):
        items = await original(result, bundle)
        if model.verifications == 1:
            items.append(AuditItem(severity='error', category='semantic', message='产品事实需修正'))
        return items

    model.verify = verify
    result = execute(model)
    assert result.status == 'ready'
    assert model.repairs == 1 and model.verifications == 2
    assert result.strategy['repair_decision']['errors'][0]['category'] == 'semantic'
