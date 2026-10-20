"""Persistent store profiles and evidence-neutral, diversity-aware campaign plans.

Plans are editorial instructions. Product claims remain the responsibility of the
existing evidence-backed workflow receiving each variant's TaskInput.
"""
from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from itertools import product as combinations
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .models import TaskInput


class StoreInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(min_length=1, max_length=100)
    audience: str = Field(min_length=1, max_length=300)
    core_tasks: list[str] = Field(default_factory=list, max_length=12)
    voice: str = Field(default='清楚、自然，像当面解释', max_length=200)
    location: str = Field(default='', max_length=200)
    materials: list[str] = Field(default_factory=list, max_length=20)

    @field_validator('name', 'audience')
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError('请填写门店名称和主要顾客')
        return value.strip()

    @field_validator('core_tasks', 'materials')
    @classmethod
    def bounded_items(cls, values):
        if any(not item.strip() or len(item) > 500 for item in values):
            raise ValueError('每项内容请填写 1 至 500 字')
        return list(dict.fromkeys(item.strip() for item in values))


class CampaignInput(BaseModel):
    brief: str = Field(min_length=1, max_length=3000)
    product: str = Field(min_length=1, max_length=200)
    platform: str = Field(default='xiaohongshu', pattern=r'^[a-z][a-z0-9_-]*$', max_length=64)
    store_ids: list[str] = Field(default_factory=list, max_length=100)
    scene: str = Field(default='', max_length=1500)
    goal: str = Field(default='引出具体咨询', max_length=300)
    ecosystem_products: list[str] = Field(default_factory=list, max_length=4)
    media_ids: list[str] = Field(default_factory=list, max_length=12)

    @field_validator('brief', 'product')
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError('请填写产品和本次推广任务')
        return value.strip()


MIGRATIONS = (
    """CREATE TABLE stores (
        id TEXT PRIMARY KEY, profile TEXT NOT NULL,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
    CREATE TABLE campaigns (
        id TEXT PRIMARY KEY, payload TEXT NOT NULL, created_at TEXT NOT NULL);
    CREATE TABLE campaign_variants (
        id TEXT PRIMARY KEY, campaign_id TEXT NOT NULL REFERENCES campaigns(id),
        store_id TEXT NOT NULL REFERENCES stores(id), payload TEXT NOT NULL);
    CREATE INDEX campaign_variant_parent ON campaign_variants(campaign_id);""",
)


class StoreRepository:
    def __init__(self, path=None):
        self.path = Path(path or os.getenv('MATEQUILL_STORE_DB', 'runtime/stores.sqlite3'))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as connection:
            connection.execute('BEGIN IMMEDIATE')
            version = connection.execute('PRAGMA user_version').fetchone()[0]
            for index, migration in enumerate(MIGRATIONS, start=1):
                if index > version:
                    for statement in migration.split(';'):
                        if statement.strip():
                            connection.execute(statement)
                    connection.execute(f'PRAGMA user_version={index}')

    @contextmanager
    def connection(self):
        connection = sqlite3.connect(self.path, timeout=10)
        connection.execute('PRAGMA foreign_keys=ON')
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def list_stores(self):
        with self.connection() as connection:
            rows = connection.execute('SELECT * FROM stores ORDER BY created_at,id').fetchall()
        return [self._store(row) for row in rows]

    @staticmethod
    def _store(row):
        return {'id': row['id'], **json.loads(row['profile']),
                'created_at': row['created_at'], 'updated_at': row['updated_at']}

    def save_store(self, profile: StoreInput, store_id=None):
        now = datetime.now(timezone.utc).isoformat()
        body = json.dumps(profile.model_dump(), ensure_ascii=False)
        with self.connection() as connection:
            if store_id:
                cursor = connection.execute('UPDATE stores SET profile=?,updated_at=? WHERE id=?',
                                            (body, now, store_id))
                if not cursor.rowcount:
                    raise KeyError(store_id)
            else:
                store_id = uuid4().hex
                connection.execute('INSERT INTO stores VALUES (?,?,?,?)', (store_id, body, now, now))
            return self._store(connection.execute('SELECT * FROM stores WHERE id=?', (store_id,)).fetchone())

    def save_campaign(self, plan):
        with self.connection() as connection:
            connection.execute('INSERT INTO campaigns VALUES (?,?,?)',
                               (plan['campaign_id'], json.dumps(plan, ensure_ascii=False),
                                datetime.now(timezone.utc).isoformat()))
            for variant in plan['variants']:
                connection.execute('INSERT INTO campaign_variants VALUES (?,?,?,?)',
                                   (variant['id'], plan['campaign_id'], variant['store_id'],
                                    json.dumps(variant, ensure_ascii=False)))

    def get_campaign(self, campaign_id):
        with self.connection() as connection:
            row = connection.execute('SELECT payload FROM campaigns WHERE id=?', (campaign_id,)).fetchone()
        if not row:
            raise KeyError(campaign_id)
        return json.loads(row['payload'])


# Independent editorial axes, not alternate adjective/temperature presets.
ANGLES = (
    ('question', '先回答顾客最在意的一个问题', '将顾客任务拆成一个可回答的问题'),
    ('checklist', '把这次任务准备成一份清单', '用任务清单帮助顾客做好准备'),
    ('walkthrough', '把一个日常任务走一遍', '从任务起点到完成动作组织内容'),
    ('tradeoff', '围绕一个选择讲清取舍', '解释选择时应该比较什么'),
    ('learn', '给第一次接触的人一份上手路线', '把陌生概念转成可执行步骤'),
    ('myth', '拆解一个容易混淆的概念', '用准确边界化解误解'),
)
NARRATIVES = (
    ('qa', '一问一答：疑问、解释、下一步'),
    ('cards', '三张清单卡：目标、核对点、体验动作'),
    ('timeline', '任务时间线：准备、执行、收尾'),
    ('decision', '决策分支：如果在意什么，就先看什么'),
    ('tutorial', '步骤图解：每一步只解决一个问题'),
    ('contrast', '概念对照：相似说法背后的具体区别'),
)
VISUALS = (
    ('annotated', '产品原图配局部注解，重点信息靠近对应部位'),
    ('editorial', '留白编辑式排版，大标题与一张主体产品图'),
    ('sequence', '分镜连续图，把任务步骤排成可滑动的卡片'),
    ('desk', '以顾客任务为线索设计桌面构图，产品外观使用核对后的素材'),
    ('diagram', '关系图解，清楚标注设备与动作的对应关系'),
    ('detail', '产品细节与说明卡交替，给关键条件留出阅读空间'),
)


def _pick_direction(store, used):
    signal = ' '.join([store['audience'], *store['core_tasks']])
    preferred = 2 if any(w in signal for w in ('交稿', '会议', '出差')) else (
        1 if any(w in signal for w in ('学生', '入学', '家长')) else 0)
    candidates = combinations(range(len(ANGLES)), range(len(NARRATIVES)), range(len(VISUALS)))

    def score(candidate):
        novelty = sum(min(sum(a != b for a, b in zip(candidate, previous)), 3) for previous in used)
        return (candidate not in used, novelty, candidate[0] == preferred,
                -sum(candidate), tuple(-v for v in candidate))

    return max(candidates, key=score)


def _angle_title(angle_id, customer_task):
    """Show a concrete editorial subject while retaining structural axis IDs."""
    task = customer_task.strip().strip('“”"。！？!?')
    if len(task) > 28:
        task = task[:27] + '…'
    return {
        'question': f'{task}，先聊一个关键问题',
        'checklist': f'{task}，先留好这份清单',
        'walkthrough': f'从{task}到完成，一步步讲清',
        'tradeoff': f'{task}，找到更适合的选择',
        'learn': f'{task}，从第一步开始',
        'myth': f'{task}，把容易混淆的地方讲清',
    }[angle_id]


def plan_campaign(request: CampaignInput, stores: list[dict], *, campaign_id=None):
    ids = list(dict.fromkeys(request.store_ids))
    by_id = {store['id']: store for store in stores}
    unknown = [store_id for store_id in ids if store_id not in by_id]
    if unknown:
        raise KeyError(unknown[0])
    campaign_id = campaign_id or uuid4().hex
    if not ids:
        return {'campaign_id': campaign_id, 'status': 'needs_input', 'variants': [],
                'questions': [{'field': 'store_ids', 'label': '这次为哪家门店准备内容？'}],
                'diversity': {'variant_count': 0, 'distinct_directions': 0}}
    used, variants = [], []
    for store_id in ids:
        store = by_id[store_id]
        direction = _pick_direction(store, used)
        used.append(direction)
        angle = ANGLES[direction[0]]
        narrative = NARRATIVES[direction[1]]
        visual = VISUALS[direction[2]]
        customer_task = store['core_tasks'][0] if store['core_tasks'] else (request.scene or request.brief)
        angle_title = _angle_title(angle[0], customer_task)
        cta = f'聊聊你在“{customer_task}”时最想解决的一个问题。'
        why = f'面向“{store["audience"]}”，从“{customer_task}”切入；{angle[2]}。'
        instructions = {
            'purpose': '为这家门店创作本次任务的独立内容',
            'brief': request.brief,
            'store_profile_user_supplied': {key: store[key] for key in
                                            ('name', 'audience', 'core_tasks', 'voice', 'location', 'materials')},
            'angle': angle_title, 'narrative_device': narrative[1], 'visual_direction': visual[1],
            'suggested_cta': cta,
            'writing_rules': [
                '门店画像和材料描述是用户输入，仅用来确定选题与风格；不能作为产品功能、性能或价格依据。',
                '产品信息逐项使用本次检索到的证据，跨设备功能核对具体型号与条件。',
                '可设计待拍摄分镜；不能写成已发生的接待故事、亲历评价或虚构当地活动。',
                '不把门店位置推断成人流、学校分布或顾客偏好；不把材料描述当作已上传图片。',
            ],
        }
        task = TaskInput(product=request.product, audience=store['audience'],
                         scene=customer_task, platform=request.platform, goal=request.goal,
                         ecosystem_products=request.ecosystem_products, media_ids=request.media_ids,
                         requirements=json.dumps(instructions, ensure_ascii=False))
        variants.append({'id': uuid4().hex, 'store_id': store_id, 'store_name': store['name'],
                         'audience': store['audience'], 'customer_task': customer_task,
                         'angle': angle_title, 'narrative_device': narrative[1],
                         'visual_direction': visual[1], 'cta': cta, 'why': why,
                         'direction_ids': [angle[0], narrative[0], visual[0]], 'task': task.model_dump()})
    dimensions = ('angle', 'narrative_device', 'visual_direction', 'audience', 'customer_task')
    pairs = [(a, b) for i, a in enumerate(variants) for b in variants[i + 1:]]
    return {'campaign_id': campaign_id, 'status': 'ready', 'variants': variants, 'questions': [],
            'diversity': {'variant_count': len(variants), 'distinct_directions': len(set(used)),
                          'distinct_by_dimension': {key: len({v[key] for v in variants}) for key in dimensions},
                          'minimum_pairwise_changed_dimensions': min(
                              (sum(a[key] != b[key] for key in dimensions) for a, b in pairs), default=0),
                          'measurement': '规划字段差异，最终文稿由原有生成与审校流程产出'}}
