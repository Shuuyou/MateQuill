"""Evidence-linked sales handoff. Plans are suggestions, never sent messages."""
from __future__ import annotations


def build_sales_plan(task, bundle, strategy):
    supplied = [
        {'dimension': '使用任务', 'observation': task.scene, 'basis': '用户输入：使用场景', 'status': '用户提供'},
        {'dimension': '目标人群', 'observation': task.audience, 'basis': '用户输入：目标人群', 'status': '用户提供'},
        {'dimension': '购买阶段', 'observation': task.stage, 'basis': '用户输入：购买阶段', 'status': '用户提供'},
        {'dimension': '决策阻碍', 'observation': '；'.join(strategy.get('concerns', [])),
         'basis': '任务分析提出的核对问题，不代表客户已确认', 'status': '待确认'},
        {'dimension': '购买约束', 'observation': '预算、具体配置、必需软件及外设清单',
         'basis': '输入尚未提供结构化约束，需要继续询问', 'status': '待确认'},
    ]
    by_id = {f.id: f for f in bundle.facts}
    candidates = [by_id[fid] for fid in strategy.get('priority_fact_ids', []) if fid in by_id]
    fact = next((f for f in candidates if len(f.value) < 160), candidates[0] if candidates else None)
    demos = [
        {'time': '0:00–0:40', 'action': '请顾客选出一个最常用的文件或任务，确认具体应用、版本与外设；不采集无关个人文件。',
         'expected': '明确这次演示要验证什么；无法确认适配时先查证', 'fact_ids': []},
        {'time': '0:40–2:10', 'action': ('对照官方资料介绍这一项产品信息，再由顾客操作或现场核对：' + fact.label + '：' + fact.value
            + '。适用条件：' + ('；'.join(fact.qualifiers) or '以所引原文和具体销售配置为准')) if fact else '先取得型号对应的产品事实，再安排产品能力演示。',
         'expected': '核对配置或实际完成一个顾客任务，不承诺演示一定成功', 'fact_ids': [fact.id] if fact else []},
        {'time': '2:10–3:00', 'action': '请顾客复述是否解决原问题，记录未完成的适配检查，再讨论具体配置与报价。',
         'expected': '得出适合、需再验证或不适合的下一步结论', 'fact_ids': []},
    ]
    onboarding = task.stage == 'onboarding'
    followup = [
        {'day': 1, 'purpose': '确认上手阻碍' if onboarding else '确认真正顾虑',
         'message': '开始使用后，最先卡住的是哪个步骤？可以只描述问题，不发送账号密码。' if onboarding else
                    '上次提到的使用场景里，你最想先确认哪个软件或外设？我可以先整理需要核对的条件。',
         'trigger': '顾客同意接收跟进，且没有拒绝或退订', 'stop_if': '已解决、已拒绝或不希望继续联系'},
        {'day': 3, 'purpose': '核对实际任务',
         'message': '之前提到的问题现在解决了吗？如果仍有阻碍，我们先核对应用版本和设备条件。' if onboarding else
                    '如果还在比较，可以带一个常用文件核对真实工作流程；具体能否完成，以软件适配和现场结果为准。',
         'trigger': '上一轮问题未解决且顾客愿意继续沟通', 'stop_if': '已成交转上手支持、已解决或拒绝联系'},
        {'day': 7, 'purpose': '结束本轮或约定下一步',
         'message': '这次使用是否还有未解决的问题？如果没有，我就结束这轮跟进。' if onboarding else
                    '选购计划有没有变化？如果暂时不需要，我就结束这轮联系；有具体需求时再一起核对。',
         'trigger': '仍有有效需求且获得跟进许可', 'stop_if': '无需求、无许可或明确拒绝'},
    ]
    return {
        'status': 'suggested',
        'insight_note': '五维是当前产品设计，尚未取得赛题官方维度定义；推断均需顾客确认。',
        'insights': supplied,
        'demo_script': demos,
        'demo_fallback': '设备、应用或演示条件不满足时停止能力承诺，记录待查证项；不以静态图片冒充成功操作。',
        'next_action': '先确认必需软件与购买约束，再预约一个能验证真实任务的演示。',
        'followup_anchor': '获得顾客同意并约定开始跟进的日期；不是自动推定购买日期',
        'followups': followup,
        'delivery': '仅生成草稿与计划，未发送消息，未创建外部预约',
        'quote': {'status': 'needs_rules', 'message': '待导入经授权、带有效期的价格与叠加规则；当前不提供正式报价。'},
        'compatibility_checklist': ['列出必需应用、版本、插件、授权方式', '列出外设型号、驱动与接口要求',
                                    '逐项核对官方适配资料；资料缺失时安排实测，不能凭操作系统名称保证兼容'],
    }
