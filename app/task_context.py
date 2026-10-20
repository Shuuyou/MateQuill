"""Compose selected products and user material around one generation workflow."""
import hashlib

from .models import Source, Asset


class TaskContextProvider:
    def __init__(self, base, task, media_store=None):
        self.base, self.task, self.media_store = base, task, media_store

    async def retrieve(self, product, query=''):
        bundle = (await self.base.retrieve(product, query)).model_copy(deep=True)
        if bundle.conflicts:
            return bundle
        selected = list(dict.fromkeys(name for name in self.task.ecosystem_products if name != product))
        for name in selected:
            extra = await self.base.retrieve(name, query)
            if not extra.facts or extra.conflicts:
                bundle.conflicts.append('请先确认生态组合中的产品资料：'+name)
                continue
            bundle.facts.extend(extra.facts)
            bundle.sources.extend(s for s in extra.sources if s.id not in {x.id for x in bundle.sources})
            bundle.assets.extend(a for a in extra.assets if a.id not in {x.id for x in bundle.assets})
        if selected and self.task.mode == 'local' and not bundle.conflicts:
            from .product_knowledge import capability_fact, load_products
            capability = capability_fact(product, selected, load_products())
            if capability:
                source, fact = capability
                bundle.sources.append(source)
                bundle.facts.insert(0, fact)
        if self.task.media_ids:
            if self.media_store is None:
                from .media_routes import media_store
                self.media_store = media_store()
            for identifier in dict.fromkeys(self.task.media_ids):
                record = self.media_store.get(identifier)
                if not record:
                    bundle.warnings.append('有一份门店素材已过期，可以重新上传。')
                    continue
                path, mime, result = record
                if not mime.startswith('image/'):
                    continue
                observations = result.get('observations') or {}
                caption = observations.get('caption') or '用户上传的场景照片'
                sid = 'upload-source-'+identifier
                excerpt = '用户上传场景素材：'+caption
                bundle.sources.append(Source(id=sid, title='门店上传照片', provider='store_upload',
                    excerpt=excerpt, sha256=hashlib.sha256(excerpt.encode()).hexdigest()))
                bundle.assets.insert(0, Asset(id='upload-'+identifier, product=product,
                    source_id=sid, url='/api/assets/upload-'+identifier, title=caption,
                    provenance='store_upload', verified=True, sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                    visual_role='scene', semantic_basis='user_photo_context', visual_group=identifier,
                    claim_scope='用户选择的场景素材，产品规格与兼容性仍使用产品资料中的事实。'))
                bundle.user_materials.append({'id':identifier, 'kind':'photo', 'observations':observations})
        return bundle
