import asyncio

import pytest

from app.design_service import _collect_catalog_design_materials, _timed_design_stage


def test_catalog_readers_overlap_without_sharing_mapping_or_changing_order(monkeypatch):
    async def run():
        started = asyncio.Event()
        state, assets = {}, {'original': {'sha256': 'same'}}

        async def subjects(product, snapshot, max_subjects):
            assert max_subjects == 8
            await asyncio.wait_for(started.wait(), 1)
            assert 'extra' not in snapshot
            return {'subject': {'role': 'packshot'}}, [{'id': 's1'}]

        async def brand(product, snapshot):
            snapshot['extra'] = {'role': 'kv'}
            started.set()
            return {'brand': {'role': 'kv'}}, [{'id': 's2'}]

        monkeypatch.setattr('app.design.product_subjects.collect_product_subjects', subjects)
        monkeypatch.setattr('app.design_brand_materials.collect_brand_materials', brand)
        results = await _collect_catalog_design_materials(state, 'product', assets)
        assert list(results[0][0]) == ['subject']
        assert list(results[1][0]) == ['brand']
        assert list(assets) == ['original']
        assert {r['stage'] for r in state['stage_timing']} == {'product_subjects', 'brand_materials'}
        assert all(r['status'] == 'completed' and r['duration_ms'] >= 0 for r in state['stage_timing'])
    asyncio.run(run())


def test_timing_records_failure_and_retains_bounded_history():
    async def run():
        state = {'stage_timing': [{'old': True} for _ in range(100)]}

        async def failed():
            raise ValueError('synthetic')

        with pytest.raises(ValueError):
            await _timed_design_stage(state, 'art_direction', failed())
        assert len(state['stage_timing']) == 100
        assert state['stage_timing'][-1]['status'] == 'incomplete'
        assert 'synthetic' not in str(state['stage_timing'])
    asyncio.run(run())


def test_catalog_failure_cancels_and_joins_other_reader(monkeypatch):
    async def run():
        started, cancelled = asyncio.Event(), asyncio.Event()

        async def subjects(*args, **kwargs):
            await started.wait()
            raise ValueError('synthetic')

        async def brand(*args):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        monkeypatch.setattr('app.design.product_subjects.collect_product_subjects', subjects)
        monkeypatch.setattr('app.design_brand_materials.collect_brand_materials', brand)
        with pytest.raises(ValueError):
            await _collect_catalog_design_materials({}, 'product', {})
        assert cancelled.is_set()
    asyncio.run(run())
