"""Versioned editorial recommendations, not claims about upload requirements."""
from pydantic import BaseModel, Field

class PlatformDesignConnector(BaseModel):
    platform: str
    surface: str
    version: str='2026-10-22.1'
    width: int
    height: int
    safe_margin: float=.065
    headline_char_budget: int=24
    suggested_pages: int=3
    title_cover_relation: str
    image_sequence: list[str]=Field(default_factory=list)
    review_rubric: list[str]=Field(default_factory=list)
    specification_kind: str='editorial_recommendation'

ROWS={
 '小红书':('note_cover',1080,1440,24,3,'图片与原生标题互补；有正文时兑现生活动机，无正文时图内信息独立完整',['cover','detail','invitation']),
 '朋友圈':('photo',1080,1080,16,1,'真实照片优先，可无字；不重复朋友圈全文',['photo']),
 '微信公众号':('article_cover',1800,766,22,3,'封面只表达文章主旨，正文图承担解释',['cover','explanation','detail']),
 'B站':('video_cover',1920,1080,18,1,'用已提供的真实演示帧兑现视频问题',['cover']),
 '抖音':('video_cover',1080,1920,18,1,'动作与对象先被看懂，标题避开上下界面',['cover']),
}
ALIASES={'xiaohongshu':'小红书','rednote':'小红书','moments':'朋友圈','微信朋友圈':'朋友圈','wechat':'微信公众号','公众号':'微信公众号','bilibili':'B站','douyin':'抖音'}

def get_platform_connector(platform: str='小红书', surface: str='') -> PlatformDesignConnector:
    single_poster=platform in ('poster','海报')
    if single_poster: platform='小红书'
    key=ALIASES.get(platform,platform)
    if key not in ROWS: raise ValueError('unsupported_design_platform')
    default,w,h,budget,pages,relation,sequence=ROWS[key]
    surface=surface or default
    permitted={default,'image_note','article_body','poster'}
    if key=='小红书': permitted.update({'title_only','title_body'})
    if surface not in permitted: raise ValueError('unsupported_design_surface')
    if surface=='image_note': w,h,pages,sequence=1080,1440,3,['cover','detail','invitation']
    if surface=='article_body': w,h,pages,sequence=1440,1080,1,['explanation']
    if surface=='poster': w,h,pages,sequence=1080,1440,1,['cover']
    if single_poster: pages,sequence=1,['cover']
    return PlatformDesignConnector(platform=key,surface=surface,width=w,height=h,headline_char_budget=budget,
        suggested_pages=pages,title_cover_relation=relation,image_sequence=sequence,
        safe_margin=.10 if key=='抖音' and surface=='video_cover' else .065,
        review_rubric=['缩略图第一视线明确','文字与图片承担不同任务','具体账号表达自然','准确产品主体清晰'])
