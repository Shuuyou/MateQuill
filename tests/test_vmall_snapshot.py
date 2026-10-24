import json

import pytest

from app.vmall_catalog import parse_product_page, load_vmall_products, safe_asset_url, valid_vmall_source


def page(**changes):
    value={'mainData':{'current':{'disPrdId':1001,'name':'HUAWEI Test','brandName':'HUAWEI',
        'base':{'red':{'sbomCode':'red','name':'红色 512GB','photoPath':'/a/','photoName':'red.png',
                       'gbomAttrList':[{'attrName':'颜色','attrValue':'红色'}]},
                'blue':{'sbomCode':'blue','name':'蓝色 1TB','photoPath':'/a/','photoName':'blue.png'}},
        'shopInfo':{'shopName':'销售商','shopCode':'shop-1'}}},
        'sbomCode':'red','extData':{'parameterData':{'list':{'data':[{'name':'机身','data':[]}]}},
        'detailsData':{'data':'<img src="https://fss-cdn.vmall.com/detail.jpg"><img src="https://other.example/x.png">'},
        'productRelDetailResp':{'comments':[{'avatar':'https://res.vmallres.com/customer.png','text':'PRIVATE_NOT_PRODUCT'}]}}}
    value.update(changes)
    return '<script id="__NEXT_DATA__" type="application/json">'+json.dumps({'props':{'pageProps':value}})+'</script>'


def test_product_projection_separates_skus_and_never_harvests_customer_materials():
    result=parse_product_page(page(),{'productId':1001,'isInv':0},source_url='https://item.vmall.com/example')
    assert result['availability']['stock_state']=='out_of_stock'
    assert result['availability']['sale_state']=='listed'
    assert result['parameter_sku_id']=='red' and result['facts']==[]
    assert len(result['variants'])==2
    assert {(a['sku_id'],a['image_url']) for a in result['assets'] if a['scope']=='sku_gallery'}=={
        ('red','https://res.vmallres.com/pimages/a/800_800_red.png'),
        ('blue','https://res.vmallres.com/pimages/a/800_800_blue.png')}
    assert 'PRIVATE_NOT_PRODUCT' not in json.dumps(result)
    assert all('customer.png' not in a['image_url'] for a in result['assets'])
    assert any(a['download_status']=='host_not_enabled' for a in result['assets'])


def test_unknown_stock_remains_unknown_and_wrong_ssr_identity_fails():
    result=parse_product_page(page(),{'productId':1001},source_url='https://item.vmall.com/example')
    assert result['availability']['stock_state']=='unknown'
    with pytest.raises(ValueError,match='identity_mismatch'):
        parse_product_page(page(),{'productId':1002},source_url='https://item.vmall.com/example')


@pytest.mark.parametrize('url',['http://res.vmallres.com/a','https://res.vmallres.com.evil.test/a',
    'https://127.0.0.1/a','https://u:p@res.vmallres.com/a','https://res.vmallres.com:8080/a'])
def test_asset_download_surface_remains_exact_host_https(url):
    assert safe_asset_url(url) is None


def test_loader_does_not_promote_failed_or_unknown_sale_state(tmp_path):
    good=parse_product_page(page(),{'productId':1001},source_url='https://item.vmall.com/example')
    failed={**good,'detail_verified':False}
    unknown={**good,'availability':{'catalog_listed':True,'sale_state':'unknown'}}
    p=tmp_path/'catalog.json';p.write_text(json.dumps({'products':[good,failed,unknown]}),encoding='utf-8')
    assert load_vmall_products(p)==[good]


def test_storefront_source_is_not_an_arbitrary_cdn_or_phishing_url():
    assert valid_vmall_source({'url':'https://www.vmall.com/product/1001.html','sha256':'a'*64})
    for url in ['https://www.vmall.com.evil.test/product/1001.html',
                'https://res.vmallres.com/product/1001.html','https://www.vmall.com/product/unknown.html']:
        assert not valid_vmall_source({'url':url,'sha256':'a'*64})
    assert not valid_vmall_source({'url':'https://www.vmall.com/product/1001.html','sha256':''})
