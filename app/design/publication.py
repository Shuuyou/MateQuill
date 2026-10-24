"""Publication responsibilities, independent of canvas size and visual style.

The source post stays immutable. This contract describes the intended image
package to the writer, art director and final reviewer, without new staff input.
"""
from __future__ import annotations

import re
from pydantic import BaseModel, Field
from .connectors import ALIASES


def no_overlay_text_requested(instruction):
    """An explicit caller request, never a proposal's self-declared exemption.

    No headline alone can still allow a caption, so it is not the same policy.
    Original product markings inside source pixels are not overlay typography.
    """
    if not isinstance(instruction, str):
        return False
    text = re.sub(r'“[^”]*”|「[^」]*」|‘[^’]*’|"[^"]*"', '', instruction.casefold())
    if re.search(r'(?:不要|别|不能|不是|并非)(?:改成|改为|变成|做成|做|制作|生成|采用|用)?(?:一张)?(?:无字(?:图|照片|海报)?|纯照片)', text):
        return False
    direct = any(token in text for token in ('不要文字', '不要加文字', '不加文字', '不添加文字',
        '不加字', '无文字', '纯照片', '无叠字', '不叠字', '不叠加文字', '不添加叠字',
        'no overlay text', 'no text', 'without text'))
    wordless = re.search(r'无字(?=$|[\s，。；、！!?;,.]|(?:图片|配图|海报|照片|画面|版本|版|图)(?:$|[\s，。；、！!?;,.]|即可|就好))', text)
    return direct or bool(wordless)


class PublicationStrategy(BaseModel):
    mode: str
    version: str = '2026-10-23.1'
    selection_basis: str
    image_job: str
    native_title_job: str = ''
    body_job: str = ''
    information_layers: list[str] = Field(default_factory=list)
    naturalness_checks: list[str] = Field(default_factory=list)
    companion_copy: dict = Field(default_factory=dict)


def publication_strategy(platform, surface='', instruction='', source=None):
    """Explicit requested format wins; otherwise use actual companion copy.

    A question mark is not a format signal and the word 海报 in an XHS task
    does not silently discard the platform's native title/body.
    """
    source = source or {}
    source = source.get('result') or source
    key = ALIASES.get(platform, platform)
    title = str(source.get('title') or '').strip()
    body = '\n'.join(str(source.get(k) or '').strip() for k in ('intro', 'body'))
    blocks = [str(row.get('text') or '').strip() for row in source.get('blocks', [])
              if isinstance(row, dict) and row.get('text')]
    body = '\n'.join([body.strip(), *blocks, str(source.get('cta') or '').strip()]).strip()
    basis = 'actual_companion_copy'
    if platform in ('poster', '海报') or surface == 'poster':
        mode, basis = 'standalone_poster', 'explicit_surface'
    elif key == '朋友圈':
        mode = 'moments_companion'
    elif key == '小红书':
        if surface in ('title_only', 'title_body'):
            mode, basis = 'xhs_' + surface, 'explicit_surface'
        elif re.search(r'不(?:要|写|带|加)正文|无正文|只有(?:图片|图).{0,5}标题|仅(?:图片|图).{0,5}标题', instruction):
            mode, basis = 'xhs_title_only', 'explicit_instruction'
        else:
            mode = 'xhs_title_body' if body else 'xhs_title_only'
    else:
        mode = 'article_companion'
    common = ['读起来像这个发布身份会说的话，避免假装顾客亲历和品牌发布会口吻',
              '产品、标题、图形共享一个具体创意；装饰不能压过完整可识别的主产品',
              '有用信息形成主次，不用空泛巨字、机械参数堆积或重复CTA充数',
              '好奇与生活共鸣须由图文兑现，不贬低职业、旧设备或消费能力']
    rows = {
        'standalone_poster': ('单独转发也能看懂：产品身份、一个明确价值及相关依据完整成立', '', '',
            ['产品与核心主张', '一至三个与主题有关的真实要点', '必要适用条件或有依据的活动信息']),
        'moments_companion': ('熟人信息流里的自然视觉停顿，清楚展示产品并给出一个具体看点', '',
            '像门店朋友补充选机建议或话题，不把整段朋友圈搬进图里',
            ['产品看点', '可选短引题或有用细节，数量由本次内容决定']),
        'xhs_title_only': ('图片与原生标题共同完成内容：图里要兑现好奇、产品识别和实用价值，不能等不存在的正文解释',
            '提供图片之外的进入理由，与画面形成互补；不机械重复图中文字', '',
            ['一眼看懂的主张与完整产品', '支撑主题的细节或知识', '相关条件在图内完整表达']),
        'xhs_title_body': ('封面给出清楚看点和可兑现的好奇，后续图按需提供细节或可保存的信息',
            '提供生活动机或具体问题，不照抄封面',
            '兑现标题与图的承诺，补充场景、取舍、依据及自然互动；不虚构第一人称体验',
            ['封面主张与产品', '按需要提供的细节、对照或选机思路']),
        'article_companion': ('先表达文章或视频的主要看点，解释信息交由正文与后续内容',
            '准确表达正文主题', '提供完整解释与依据', ['主题与产品', '必要解释图']),
    }
    image_job, title_job, body_job, layers = rows[mode]
    companion = {}
    if mode not in ('standalone_poster', 'moments_companion') and title:
        companion['title'] = title[:300]
    if mode in ('moments_companion', 'xhs_title_body', 'article_companion') and body:
        companion['body'] = body[:9000]
    return PublicationStrategy(mode=mode, selection_basis=basis, image_job=image_job,
        native_title_job=title_job, body_job=body_job, information_layers=layers,
        naturalness_checks=common, companion_copy=companion)
