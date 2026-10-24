from app.gtm_agent.tools import project_catalog


def test_catalog_search_discovers_new_products_beyond_the_first_fifty():
    rows=[{'id':str(i),'name':'旧产品'+str(i)} for i in range(100)]
    rows += [{'id':'new','name':'HUAWEI Mate 80 Pro','aliases':['华为Mate80Pro'],'parameter_sku_id':'actual-sku'}]
    default=project_catalog(rows)
    assert default['truncated'] and len(default['products'])==50 and default['total_count']==101
    matched=project_catalog(rows,'mate80 pro')
    assert matched['match_count']==1 and matched['products'][0]['parameter_sku_id']=='actual-sku'
    assert project_catalog(rows,'华为Mate80Pro')['products'][0]['id']=='new'
    assert len(rows)==101


def test_no_catalog_match_does_not_guess_a_neighbouring_generation():
    assert project_catalog([{'name':'Mate 80 Pro'}],'Mate 90')['match_count']==0
