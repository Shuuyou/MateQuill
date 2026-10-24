import asyncio
import hashlib
from app.design_review import validate_marketing_visual_review, review_design, refine_design
from test_design_review import doc, render, visual


def review_payload(decision='accept'):
    dimensions = {key: {'status': 'pass', 'rationale': 'No unsupported implication observed', 'findings': []}
                  for key in ('product','world','legal','empathy','community','timeliness')}
    if decision != 'accept':
        dimensions['empathy'] = {'status': decision, 'rationale': 'Publisher mocks reader hardship',
            'findings': [{'quote':'', 'region':'center headline', 'reason':'Reader is the butt of joke',
                          'action':'Change the core relationship and preserve the night-life scene'}]}
    return {'schema_version':'1', 'phase':'final', 'decision':decision, 'dimensions':dimensions,
        'creative_effectiveness':{'status':'pass', 'hook':'Night light contrast', 'local_value':'Walkable evening scene',
            'brand_connection':'Camera use', 'natural_action':'Try a photo', 'distinctiveness':'Concrete scene',
            'preserve':['Night light contrast'], 'improvements':[]},
        'revision_brief':'Choose an inviting observation rather than mocking reader hardship' if decision != 'accept' else ''}


def test_final_visual_core_schema_and_actual_pixel_binding():
    png = render(doc())
    result = validate_marketing_visual_review(review_payload(), png)
    assert result['review_completed'] and result['action'] == 'deliver'
    assert result['render_sha256'] == hashlib.sha256(png).hexdigest()
    broken = review_payload()
    del broken['dimensions']['empathy']
    assert validate_marketing_visual_review(broken, png)['action'] == 'incomplete'


def test_visual_nonexistent_quote_does_not_pass():
    payload = review_payload('revise')
    payload['dimensions']['empathy']['findings'][0]['quote'] = 'invented quote'
    assert validate_marketing_visual_review(payload, render(doc()), public_text='real title')['action'] == 'incomplete'


def test_high_aesthetic_score_never_offsets_reselect_or_applies_layout_edits():
    document = doc()
    png = render(document)
    called = []
    def reviewer(pixels, current):
        called.append(True)
        return {**visual(score=100, edits=[{'id':current.layers[0].id,'changes':{'x':9}}]),
            'marketing_review':validate_marketing_visual_review(review_payload('reselect'), pixels)}
    result = asyncio.run(refine_design(document, render, reviewer=reviewer))
    assert result['review']['marketing_action'] == 'reselect'
    assert result['review']['errors'] > 0 and result['accepted_rounds'] == 0
    assert len(called) == 1


def test_review_bound_to_other_pixels_cannot_be_reused():
    document = doc()
    result = asyncio.run(review_design(render(document), document, vision_reviewer=lambda *_:
        {**visual(), 'marketing_review':validate_marketing_visual_review(review_payload(), b'other image')}))
    assert result['marketing_action'] == 'incomplete'

def test_integrated_vlm_reviews_actual_png_and_public_copy_with_shared_schema(monkeypatch):
    import base64
    import json
    import httpx
    from app.design_review import review_with_model
    document = doc()
    png = render(document)
    actual_client = httpx.AsyncClient
    captured = []
    def handler(request):
        body = json.loads(request.content)
        content = body['messages'][1]['content']
        assert base64.b64decode(content[1]['image_url']['url'].split(',')[1]) == png
        metadata = json.loads(content[0]['text'])
        assert 'Store invitation' in metadata['public_text']
        assert 'marketing_review schema' in body['messages'][0]['content']
        assert '不按字段索引机械复制所有条件' in body['messages'][0]['content']
        assert '正常到店咨询或体验邀请不自动等于保证有现成样张' in body['messages'][0]['content']
        assert 'requirements' not in metadata['context']['marketing_context']['task']
        assert metadata['context']['marketing_context']['task']['editable_editorial_brief'] == 'A previous editorial suggestion'
        assert metadata['context_roles']['text_review_history'] == 'historical_diagnostic_not_new_requirement'
        captured.append(body)
        checks = [{'category':name, 'severity':'info', 'message':'Observed in final pixels: '+name}
                  for name in ('composition','typography','imagery','platform','specificity')]
        return httpx.Response(200, json={'choices':[{'message':{'content':json.dumps({
            'review_completed':True,'checks':checks,'score':99,'edits':[],
            'marketing_review':review_payload('reselect')})}}]})
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kw:actual_client(transport=httpx.MockTransport(handler),**kw))
    result = asyncio.run(review_with_model(png,document,'https://vision.example/v1','CANARY','vl',
        design_context={'marketing_context':{'published_copy':{'body':'Store invitation'},
            'task':{'requirements':'A previous editorial suggestion'},
            'text_review_history':[{'revision_brief':'Prior optional improvement'}]}}))
    assert len(captured) == 1
    assert result['marketing_review']['decision'] == 'reselect'
    assert result['marketing_review']['render_sha256'] == hashlib.sha256(png).hexdigest()

def test_visual_context_reuses_current_research_and_does_not_repeat_place_envelope():
    import json
    from app.design_service import _visual_marketing_context
    from app.gtm_agent.place_context import PLACE_CONTEXT_MARKER
    source = {'title':'A real title', 'body':'Use a 50MP camera',
        'facts':[{'id':'camera', 'value':'50MP'}, {'id':'unrelated','value':'unrelated value'}],
        'images':[{'caption':'Real caption','data_url':'SECRET-LARGE-BINARY'}],
        'strategy':{'marketing_context':{'as_of':'2026-10-23T01:00:00+08:00',
            'policy_version':'current', 'policies':[{'id':'policy'}],
            'research':[{'query':'current topic','sources':[{'source_id':'r1','excerpt':'Observed now'}]}]},
            'marketing_review':{'decision':'accept'}, 'marketing_review_history':[{'decision':'reselect'},{'decision':'accept'}]}}
    projected = _visual_marketing_context(source,
        {'requirements':'Keep wit'+PLACE_CONTEXT_MARKER+'LARGE-STRUCTURED-PLACE-ENVELOPE','scene':'Actual scene'},
        {'evidence':[{'quote':'Local source'}]}, {'publisher_identity':'store'})
    assert projected['marketing_context']['as_of'].startswith('2026-10-23')
    assert projected['marketing_context']['research'][0]['sources'][0]['source_id'] == 'r1'
    assert projected['previous_text_review']['decision'] == 'accept'
    assert len(projected['text_review_history']) == 2
    assert projected['task']['requirements'] == 'Keep wit'
    assert projected['product_facts'] == [{'id':'camera','value':'50MP'}]
    assert 'SECRET-LARGE-BINARY' not in json.dumps(projected)

def test_final_visual_resolves_known_surface_and_rejects_unknown_textual_id():
    png = render(doc())
    payload = review_payload('revise')
    finding = payload['dimensions']['empathy']['findings'][0]
    finding['region'] = 'image_copy:0'
    surfaces = {'image_copy:0':'Actual headline on final image'}
    result = validate_marketing_visual_review(payload,png,
        public_text='Actual headline on final image',public_surfaces=surfaces)
    assert result['review_completed']
    assert result['dimensions']['empathy']['findings'][0]['quote'] == surfaces['image_copy:0']
    finding['region'] = 'image_copy:999'
    result = validate_marketing_visual_review(payload,png,public_text='Actual headline on final image',public_surfaces=surfaces)
    assert result['action'] == 'incomplete'
    finding['region'] = 'pixel: center human figure beside camera'
    result = validate_marketing_visual_review(payload,png,public_text='Actual headline on final image',public_surfaces=surfaces)
    assert result['review_completed']


def test_visual_marketing_schema_failure_retains_bounded_structural_diagnostic():
    payload = review_payload()
    del payload['dimensions']['empathy']
    result = validate_marketing_visual_review(payload, render(doc()))
    assert result['action'] == 'incomplete' and not result['review_completed']
    diagnostic = result['diagnostic']
    assert diagnostic['stage'] == 'marketing_visual_schema'
    assert diagnostic['error_class'] == 'validation'
    assert {'loc': ['dimensions', 'empathy'], 'type': 'missing', 'msg': 'Field required'} in diagnostic['errors']


def test_visual_schema_diagnostic_never_leaks_unknown_field_payload_or_raw_input():
    import json
    payload = review_payload()
    secret = 'PRIVATE-CANARY-product-source-and-key'
    payload['dimensions']['empathy'][secret] = {'input': secret, 'api_key': secret}
    for index in range(20):
        payload['dimensions']['empathy']['unknown'+str(index)] = secret
    result = validate_marketing_visual_review(payload, render(doc()))
    diagnostic = result['diagnostic']
    assert result['action'] == 'incomplete'
    assert len(diagnostic['errors']) <= 8 and diagnostic['error_count'] >= 20
    assert secret not in json.dumps(diagnostic)
    assert all(set(error) == {'loc', 'type', 'msg'} for error in diagnostic['errors'])
    assert '<field>' in diagnostic['errors'][0]['loc']


def test_nonexistent_quote_diagnostic_is_not_a_repair_or_retry_permission():
    import json
    payload = review_payload('revise')
    payload['dimensions']['empathy']['findings'][0]['quote'] = 'PRIVATE-CANARY-invented-public-quote'
    result = validate_marketing_visual_review(payload, render(doc()), public_text='actual title')
    assert result['diagnostic']['boundary'] == 'nonexistent_public_quote'
    assert result['action'] == 'incomplete' and not result['review_completed']
    assert 'PRIVATE-CANARY' not in json.dumps(result)


def test_refinement_cannot_exchange_marketing_deliver_for_new_revise_even_with_fewer_errors():
    document = doc()
    title = next(layer for layer in document.layers if layer.role == 'headline')
    def reviewer(pixels, current):
        if current.version == document.version:
            result = visual(60, [{'id': title.id, 'changes': {'font_family': 'serif'}}])
            result['checks'].extend([
                {'category': 'composition', 'severity': 'error', 'message': '视觉重心需调整'},
                {'category': 'typography', 'severity': 'error', 'message': '标题风格需调整'},
            ])
            result['marketing_review'] = validate_marketing_visual_review(review_payload(), pixels)
        else:
            result = visual(99)
            result['marketing_review'] = validate_marketing_visual_review(review_payload('revise'), pixels)
        return result
    result = asyncio.run(refine_design(document, render, reviewer=reviewer))
    assert len(result['reviews']) == 2
    assert result['reviews'][1]['errors'] < result['reviews'][0]['errors']
    assert result['reviews'][1]['marketing_action'] == 'revise'
    assert result['accepted_rounds'] == 0
    assert result['review']['marketing_action'] == 'deliver'
