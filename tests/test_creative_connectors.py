import pytest
from pydantic import ValidationError
from app.creative_connectors import (get_connector,list_connectors,register_connector,
    EditorialConnector,CreativePlan,PLATFORM_PLAYBOOKS)
from app.models import TaskInput,EvidenceBundle,Fact,Asset,RunResult

@pytest.fixture
def bundle():
    return EvidenceBundle(product='测试型号',facts=[Fact(id='f1',product='测试型号',label='系统',value='示例系统',quote='示例系统',source_id='s1')],
        assets=[Asset(id='a1',product='测试型号',url='',source_id='s1',verified=True)])

def plan(**overrides):
    return {'reader_tension':'文件需要交付','reader_gain':'完成核对','cards':[
        {'id':'c1','headline':'带文件来核对','body':'确认软件版本','fact_ids':['f1'],'asset_ids':['a1'],'visual_intent':'说明核对对象'}],
        'promise_payoff':[{'promise':'一份核对清单','payoff_card_ids':['c1']}],**overrides}

def test_four_platforms_have_versioned_metadata_and_compatible_profiles():
    entries=list_connectors()
    assert len(entries)>=4
    assert {e['platform'] for e in entries}>={'xiaohongshu','wechat','moments','poster'}
    assert all(e['version'] and e['id'] and isinstance(e['styles'],list) for e in entries)
    assert [PLATFORM_PLAYBOOKS[p]['fact_budget'] for p in ('xiaohongshu','wechat','moments','poster')]==[3,4,1,2]

@pytest.mark.parametrize('platform',['xiaohongshu','wechat','moments','poster'])
def test_brief_contains_schema_but_never_promises_reach(platform,bundle):
    c=get_connector(platform)
    brief=c.build_brief(TaskInput(platform=platform,scene='检查项目文件'),bundle,{'priority_fact_ids':['f1']})
    assert brief['fact_budget']==brief['playbook']['fact_budget']
    assert 'cards' in brief['output_schema']['properties']
    assert brief['network_required'] is False and brief['publishing'] is False
    assert brief['asset_policy']['force_nine_images'] is False
    assert '不是用户经历' in brief['adaptation']

@pytest.mark.parametrize(('scene','expected'),[('大学新生课程','xhs_course_list'),('设计文件交稿','xhs_final_file'),('差旅投屏会议','xhs_travel_cable'),('普通办公','xiaohongshu_task_first')])
def test_selects_context_not_random_research_example(scene,expected,bundle):
    assert get_connector('xiaohongshu').build_brief(TaskInput(scene=scene),bundle,{})['creative_playbook']['id']==expected

def test_explicit_style_and_platform_validation(bundle):
    c=get_connector('xiaohongshu','xhs_final_file')
    assert c.build_brief(TaskInput(),bundle,{})['creative_playbook']['id']=='xhs_final_file'
    with pytest.raises(ValueError): get_connector('wechat','xhs_final_file')
    with pytest.raises(ValueError): get_connector('xiaohongshu','https://untrusted/plugin.py')
    with pytest.raises(ValueError): get_connector('unknown')

def test_normalization_locks_service_metadata_and_preserves_fields(bundle):
    c=get_connector('xiaohongshu')
    p=c.normalize_plan(plan(connector_id='malicious',connector_version='fake',platform='poster'),TaskInput(),bundle)
    assert p['connector_id']==c.connector_id and p['connector_version']==c.version and p['platform']=='xiaohongshu'
    assert p['cards'][0]['body']=='确认软件版本'
    assert p['cards'][0]['layout']=='task_checklist'
    assert c.normalize_plan({})=={}
    assert c.normalize_plan(None)=={}
    with pytest.raises(ValidationError): c.normalize_plan({'cards':[{'id':'c','block_index':-1}]})

def test_invalid_references_and_payoff_block(bundle):
    c=get_connector('xiaohongshu')
    p=c.normalize_plan(plan(cards=[{'id':'c1','fact_ids':['invented'],'asset_ids':['missing']}],
                            promise_payoff=[{'promise':'核对清单','payoff_card_ids':['absent']}]))
    codes={n.category for n in c.review(p,None,bundle) if n.severity=='error'}
    assert codes=={'creative_fact_reference','creative_asset_reference','creative_payoff_reference'}

def test_duplicate_ids_and_unpaid_promises_block(bundle):
    c=get_connector('xiaohongshu')
    p=c.normalize_plan(plan(cards=[{'id':'same'},{'id':'same'}],promise_payoff=[{'promise':'看完有清单'}]))
    errors={n.category for n in c.review(p,None,bundle) if n.severity=='error'}
    assert 'creative_duplicate_card' in errors and 'creative_unfulfilled_promise' in errors

def test_official_asset_cannot_be_claimed_as_real_photo(bundle):
    c=get_connector('xiaohongshu')
    p=c.normalize_plan(plan(cards=[{'id':'c1','asset_ids':['a1'],'truth_status':'real_photo'}]))
    assert any(n.category=='creative_photo_truth' and n.severity=='error' for n in c.review(p,None,bundle))

def test_metadata_tampering_and_block_reference_fail(bundle):
    c=get_connector('xiaohongshu')
    p=c.normalize_plan(plan(cards=[{'id':'c1','block_index':5}]))
    p['connector_version']='tampered'
    errors={n.category for n in c.review(p,RunResult(),bundle) if n.severity=='error'}
    assert errors=={'creative_metadata','creative_block_reference'}

def test_valid_text_only_cards_allowed_and_style_advice_is_warning(bundle):
    c=get_connector('xiaohongshu')
    p=c.normalize_plan(plan(cards=[{'id':'c1','headline':'填下软件版本','layout':'task_checklist','truth_status':'diagram','visual_intent':'可填写检查清单'}]))
    assert not any(n.severity=='error' for n in c.review(p,None,bundle))
    assert any(n.severity=='warning' for n in c.review(p,None,bundle))

def test_explicit_extension_does_not_change_generator_branches(bundle, monkeypatch):
    from app.creative_connectors import core
    monkeypatch.setattr(core, '_REGISTRY', dict(core._REGISTRY))
    monkeypatch.setattr(core, '_DEFAULTS', dict(core._DEFAULTS))
    c=EditorialConnector('test_extension','测试扩展',connector_id='test.extension',version='2.0',
                         profile={'fact_budget':1},playbooks=[])
    register_connector(c,default=True,replace=True)
    assert get_connector('test_extension').connector_id=='test.extension'
    with pytest.raises(ValueError): register_connector(c)
    desc=c.describe();desc['name']='mutated'
    assert c.describe()['name']=='测试扩展'


def test_task_style_selection_and_invalid_style(bundle):
    task=TaskInput(creative_style='xhs_travel_cable',scene='课程')
    assert get_connector('xiaohongshu').build_brief(task,bundle,{})['creative_playbook']['id']=='xhs_travel_cable'
    with pytest.raises(ValueError):
        get_connector('xiaohongshu').build_brief(TaskInput(creative_style='unknown'),bundle,{})

def test_license_and_source_metadata_are_preserved(bundle):
    c=get_connector('xiaohongshu')
    raw=plan(cards=[{'id':'c1','licenses':{'a1':'待确认'},'source_disclosures':{'a1':{'provider':'ima','status':'unverified'}}}])
    p=c.normalize_plan(raw)
    assert p['cards'][0]['licenses']==raw['cards'][0]['licenses']
    assert p['cards'][0]['source_disclosures']==raw['cards'][0]['source_disclosures']
@pytest.mark.parametrize(('alias','canonical'), [('设计提案','illustrative'),('文字卡','diagram'),('产品素材','official_material'),('待拍摄','pending_capture'),('条件设想','hypothetical')])
def test_truth_status_known_aliases_only(alias,canonical):
    c=get_connector('xiaohongshu')
    p=c.normalize_plan(plan(cards=[{'id':'c1','truth_status':alias}]))
    assert p['cards'][0]['truth_status']==canonical
    with pytest.raises(ValidationError):
        c.normalize_plan(plan(cards=[{'id':'c1','truth_status':'totally_unknown'}]))

def test_poster_is_single_screen_in_schema_and_audit(bundle):
    c=get_connector('poster')
    brief=c.build_brief(TaskInput(platform='poster'),bundle,{})
    assert brief['output_schema']['properties']['cards']['maxItems']==1
    p=c.normalize_plan(plan(cards=[{'id':'c1'},{'id':'c2'}],promise_payoff=[]))
    assert any(n.category=='creative_poster_pages' and n.severity=='error' for n in c.review(p,None,bundle))

def test_builtin_rules_are_data_driven_and_extension_rule_is_required(bundle):
    required={'name','structure','tone','min_images','max_images','cta'}
    for platform in ('xiaohongshu','wechat','moments','poster'):
        brief=get_connector(platform).build_brief(TaskInput(platform=platform),bundle,{})
        assert required.issubset(brief['platform_rule'])
    missing=EditorialConnector('missing','Missing',profile={'fact_budget':1},playbooks=[])
    with pytest.raises(ValueError,match='platform_rule'):
        missing.build_brief({'platform':'missing'},bundle,{})
    rule={'name':'New','structure':'任务→依据','tone':'直接','min_images':0,'max_images':2,'cta':'核对一项任务'}
    custom=EditorialConnector('custom','Custom',profile={'fact_budget':1,'platform_rule':rule},playbooks=[])
    assert custom.build_brief({'platform':'custom'},bundle,{})['platform_rule']==rule
    invalid=EditorialConnector('bad','Bad',profile={'fact_budget':1,'platform_rule':{**rule,'min_images':3}},playbooks=[])
    with pytest.raises(ValueError,match='limits'):
        invalid.build_brief({'platform':'bad'},bundle,{})


def test_library_flow_wins_over_generic_student_without_inventing_course_details(bundle):
    c=get_connector('xiaohongshu')
    task=TaskInput(audience='大学生',scene='图书馆整理手机截图与参考资料')
    book=c.build_brief(task,bundle,{})['creative_playbook']
    assert book['id']=='xhs_library_flow'
    assert '课表' not in book['title'] and len(book['cards'])==5
    assert any(x['id']=='xhs_library_flow' for x in c.describe()['styles'])

def test_generic_student_identity_does_not_select_course_story(bundle):
    c=get_connector('xiaohongshu')
    task=TaskInput(audience='大学学生',scene='日常办公')
    assert c.build_brief(task,bundle,{})['creative_playbook']['id']=='xiaohongshu_task_first'
