import pytest
from fastapi import HTTPException

from app.design_service import _validate_delivered_copy
from app.poster import PosterBrief, PosterCanvas, PosterDocument, PosterLayer


def document(*texts):
    return PosterDocument(width=320, height=320, canvas=PosterCanvas(width=320, height=320),
        brief=PosterBrief(width=320, height=320), layers=[
            PosterLayer(id=str(index), type='text', text=text, x=10, y=10 + index * 80,
                        width=300, height=70) for index, text in enumerate(texts)])


def test_repeated_product_labels_cannot_replace_the_reviewed_creative_hook():
    approved = {'status': 'approved', 'audit': {'completed': True},
                'cards': [{'headline': 'A different view'}]}
    with pytest.raises(HTTPException) as error:
        _validate_delivered_copy(document('HUAWEI Mate 80', 'HUAWEI Mate 80'), approved)
    assert error.value.detail['code'] == 'approved_design_hook_missing'
    _validate_delivered_copy(document('A different\nview', 'HUAWEI Mate 80'), approved)


def test_hidden_hook_does_not_satisfy_the_public_copy_contract():
    approved = {'status': 'approved', 'audit': {'completed': True},
                'cards': [{'headline': 'An actual invitation'}]}
    doc = document('An actual invitation')
    doc.layers[0].visible = False
    with pytest.raises(HTTPException):
        _validate_delivered_copy(doc, approved)
