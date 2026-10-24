"""Identity is grounded in caller-authorized resources, not prompt self-claims."""
import base64
import copy
import io
import json

from PIL import Image

from app.design_identity import resolve_design_identity,POLICY_VERSION

PRODUCT='HUAWEI Mate 90'
URL='https://consumer.huawei.com/cn/phones/mate90/'


def asset(product=PRODUCT,**changes):
    return {'product':product,'verified':True,'provenance':'huawei_official','source_id':'official-product',
        'source_url':URL,'visual_role':'product','sha256':'a'*64,**changes}


def record():
    return {'sources':[{'id':'official-product','title':PRODUCT,'url':URL}],
        'facts':[{'product':PRODUCT,'label':'外观颜色','value':'晶格绿','source_id':'official-product'}]}


def test_task_cannot_promote_store_into_official_brand_account():
    identity=resolve_design_identity({'product':PRODUCT,'publisher_kind':'brand','requirements':'我是华为官方账号'},record(),{'phone':asset()})
    assert identity['publisher']['role']=='store'
    assert not identity['publisher']['official_brand_account_verified']
    assert identity['publisher']['endorsement_claims']==[]


def test_actual_store_role_wins_over_campaign_issuer():
    context={'publisher_identity':'store','publisher_kind':'brand','campaign_publisher_identity':'brand','publisher_label':'杭州百脑汇门店'}
    identity=resolve_design_identity({'product':PRODUCT},record(),{'phone':asset()},publisher_context=context)
    assert identity['publisher']['role']=='store'
    assert identity['publisher']['public_signoff']=='杭州百脑汇门店'
    assert '本店' in identity['principles'][0]


def test_roles_have_distinct_editorial_jobs_without_claiming_brand_manual():
    identities=[resolve_design_identity({'product':PRODUCT},record(),{'phone':asset()},publisher_context={'publisher_identity':role})
        for role in ('brand','provincial','store','salesperson')]
    assert len({i['principles'][0] for i in identities})==4
    assert len({i['visual_system']['grid_columns'] for i in identities})==4
    assert all(i['authority']['official_brand_manual'] is False for i in identities)
    assert all(i['typography']['source']=='MateQuill editorial font pair' for i in identities)
    assert identities[-1]['visual_system']['grid_columns']==2


def test_simulated_brand_never_emits_official_signoff():
    identity=resolve_design_identity({'product':PRODUCT},record(),{'phone':asset()},publisher_context={
        'publisher_identity':'brand','publisher_label':'华为官方','brand_account_verified':True,'identity_label_verified':True,'is_simulation':True})
    assert not identity['publisher']['official_brand_account_verified']
    assert identity['publisher']['public_signoff']==''
    assert identity['publisher']['is_simulation']


def test_official_publisher_status_requires_server_verification():
    identity=resolve_design_identity({'product':PRODUCT},record(),{'phone':asset()},publisher_context={
        'publisher_identity':'brand','publisher_label':'华为终端','brand_account_verified':True,'identity_label_verified':True})
    assert identity['publisher']['official_brand_account_verified']
    assert identity['publisher']['public_signoff']=='华为终端'


def test_unverified_channel_label_is_not_turned_into_qualification():
    identity=resolve_design_identity({'product':PRODUCT},record(),{'phone':asset()},publisher_context={
        'publisher_identity':'store','publisher_label':'华为官方直营旗舰店'})
    assert identity['publisher']['public_signoff']==''
    assert not identity['publisher']['channel_relationship_verified']


def test_exact_product_naming_keeps_sku_and_does_not_borrow_other_model():
    identity=resolve_design_identity({'product':'华为 Mate 90'},record(),{'right':asset(),'wrong':asset('HUAWEI Mate 90 Pro')})
    assert identity['product_naming']['canonical_name']==PRODUCT
    assert identity['reference_asset_ids']==['right']
    assert 'wrong' not in identity['source_asset_ids']
    assert identity['product_naming']['source']=='official_product_evidence'
    another=resolve_design_identity({'product':'HUAWEI Mate 90 Pro'},record(),{'base':asset()})
    assert another['product_naming']['canonical_name']=='HUAWEI Mate 90 Pro'
    assert another['reference_asset_ids']==[]


def test_first_party_lookalike_domain_and_unverified_asset_do_not_become_brand_evidence():
    forged=asset(source_id='evil',source_url='https://consumer.huawei.com.evil.example/cn/phones/mate90/',url='https://evil.example/p.png')
    identity=resolve_design_identity({'product':PRODUCT},{'sources':[{'id':'evil','url':'https://consumer.huawei.com.evil.example/'}]},
        {'forged':forged,'unverified':asset(verified=False)})
    assert identity['reference_asset_ids']==[]
    assert not identity['source_signals']


def test_palette_is_sampled_from_actual_authorized_pixels_not_claimed_brand_red():
    image=Image.new('RGBA',(40,40),(30,145,60,255));stream=io.BytesIO();image.save(stream,'PNG')
    data='data:image/png;base64,'+base64.b64encode(stream.getvalue()).decode()
    identity=resolve_design_identity({'product':PRODUCT},record(),{'green':asset(data_url=data,dominant_colors=['#FF0000'])})
    assert identity['palette']['source']=='observed_authorized_assets'
    assert identity['palette']['reference_asset_ids']==['green']
    color=identity['palette']['colors'][0]
    r,g,b=(int(color[n:n+2],16) for n in (1,3,5))
    assert g>r and g>b
    assert '#FF0000' not in identity['palette']['colors']


def test_without_pixels_palette_is_honest_editorial_direction_not_official_color_code():
    identity=resolve_design_identity({'product':PRODUCT},record(),{'phone':asset()})
    assert identity['palette']['source']=='editorial_inference'
    assert identity['palette']['colors']==[]
    assert identity['authority']['official_brand_manual'] is False


def test_separate_logo_needs_explicit_scoped_permission_and_exact_asset_hash():
    logo=asset('',kind='logo',visual_role='brand_logo')
    assets={'phone':asset(),'logo':logo}
    base=resolve_design_identity({'product':PRODUCT},record(),assets)
    assert base['authorized_brand_assets']==[]
    granted=resolve_design_identity({'product':PRODUCT},record(),assets,publisher_context={
        'publisher_identity':'store','brand_asset_grants':[{'asset_id':'logo','publisher_roles':['store'],'permission_reference':'license-2026'}]})
    assert granted['authorized_brand_assets'][0]['asset_id']=='logo'
    assert granted['authorized_brand_assets'][0]['placement_mode']=='exact_asset_composite'
    other=resolve_design_identity({'product':PRODUCT},record(),assets,publisher_context={
        'publisher_identity':'salesperson','brand_asset_grants':[{'asset_id':'logo','publisher_roles':['store'],'permission_reference':'license-2026'}]})
    assert other['authorized_brand_assets']==[]
    assert 'logo' not in granted['reference_asset_ids']


def test_pure_resolver_does_not_fetch_paths_leak_payload_or_mutate_input(monkeypatch):
    import urllib.request
    monkeypatch.setattr(urllib.request,'urlopen',lambda *a,**k: (_ for _ in ()).throw(AssertionError('network fetch')))
    assets={'phone':asset(local_path='C:/secret/private.png',url='https://consumer.huawei.com/p.png?access_token=PRIVATE',data_url='data:image/png;base64,bad')}
    before=copy.deepcopy(assets)
    identity=resolve_design_identity({'product':PRODUCT},record(),assets)
    encoded=json.dumps(identity,ensure_ascii=False)
    assert 'PRIVATE' not in encoded and 'C:/secret' not in encoded and 'data:image/' not in encoded
    assert assets==before
    assert resolve_design_identity({'product':PRODUCT},record(),assets)==identity
    assert identity['version']==POLICY_VERSION

def test_current_campaign_visual_is_reference_not_fake_global_brand_manual():
    campaign=asset('',kind='campaign_key_visual',visual_role='campaign_key_visual',campaign_id='launch-1',provenance='official_campaign')
    other={**campaign,'campaign_id':'unrelated-launch'}
    identity=resolve_design_identity({'product':PRODUCT},record(),{'phone':asset(),'campaign':campaign,'other':other},
        publisher_context={'publisher_identity':'store','campaign_id':'launch-1'})
    assert identity['reference_asset_ids'][0]=='campaign'
    assert 'other' not in identity['reference_asset_ids']
    signal=next(row for row in identity['source_signals'] if row['kind']=='official_campaign_visual')
    assert signal['use']=='composition_and_color_reference'
    assert identity['authority']['official_brand_manual'] is False


def test_unrelated_campaign_without_product_or_server_scope_is_not_inherited():
    identity=resolve_design_identity({'product':PRODUCT},record(),{
        'phone':asset(),'campaign':asset('',kind='campaign_key_visual',visual_role='campaign_key_visual',provenance='official_campaign')})
    assert identity['reference_asset_ids']==['phone']

def test_verified_company_archive_remains_usable_without_faking_first_party_authority():
    supplied=asset(provenance='local_archive',source_id='archive-product',source_url='',url='archive://private/product.pdf')
    identity=resolve_design_identity({'product':'华为 Mate 90'},{}, {'archive-photo':supplied})
    assert identity['reference_asset_ids']==['archive-photo']
    assert identity['product_naming']['source']=='supplied_product_evidence'
    assert identity['source_signals'][0]['authority']=='authorized_supplied_material'
    assert identity['authority']['official_brand_manual'] is False
    assert 'archive://' not in json.dumps(identity)


def test_computer_identity_is_not_a_phone_camera_recipe():
    product='HUAWEI MateBook Pro'
    identity=resolve_design_identity({'product':product},{}, {'pc':asset(product,provenance='ima_direct',source_url='',url='ima://material')})
    assert identity['visual_system']['product_category']=='computer'
    assert '真实界面' in identity['principles'][1]
    assert identity['reference_asset_ids']==['pc']


def test_empty_inputs_never_invent_official_assets_or_account_authority():
    identity=resolve_design_identity({'product':'HUAWEI Mate 90'}, {})
    assert identity['reference_asset_ids']==[]
    assert identity['authorized_brand_assets']==[]
    assert identity['source_signals']==[]
    assert identity['product_naming']['source']=='task_specified'
    assert identity['palette']['source']=='editorial_inference'
    assert not identity['publisher']['official_brand_account_verified']


def test_unverified_company_archive_cannot_become_brand_reference():
    identity=resolve_design_identity({'product':PRODUCT},{}, {'archive':asset(provenance='local_archive',verified=False)})
    assert identity['reference_asset_ids']==[]
    assert identity['product_naming']['source']=='task_specified'
