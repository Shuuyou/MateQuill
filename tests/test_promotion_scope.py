from app.workflow import promotion_assertions


def test_explicit_limitations_do_not_become_offer_assertions():
    assert list(promotion_assertions('首发信息不代表本店当前价格、库存或优惠。')) == []
    assert list(promotion_assertions('问清是否有优惠，再决定。')) == []


def test_positive_or_numeric_claims_still_require_support():
    assert list(promotion_assertions('本店优惠500元。')) == ['优惠']
    assert list(promotion_assertions('没有国补，但本店优惠500元。')) == ['优惠']
    assert list(promotion_assertions('不代表没有优惠500元。')) == ['优惠']
    assert list(promotion_assertions('本店可享优惠，购买赠送配件。')) == ['优惠', '赠送']
