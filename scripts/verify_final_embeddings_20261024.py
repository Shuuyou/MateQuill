"""Exercise the configured embedding service with public synthetic examples only."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


async def main():
    settings = json.loads((ROOT/'runtime/owned-launch-settings.json').read_text(encoding='utf-8'))
    os.environ.update({
        'DIVERSITY_EMBEDDING_BASE_URL': settings['IMAGE_BASE_URL'],
        'DIVERSITY_EMBEDDING_API_KEY': settings['IMAGE_API_KEY'],
        'DIVERSITY_EMBEDDING_MODEL': 'text-embedding-3-large',
    })
    from app.regional_diversity import configured_embeddings, _validated_vectors, _cosine, select_candidates
    texts = ['课间把手机上的课堂照片传到电脑整理笔记',
             '下课后把手机拍的板书同步到笔记本做课堂整理',
             '下班路上拍摄地铁站灯箱的夜景字样']
    started = time.monotonic()
    report = {'model_requested': os.environ['DIVERSITY_EMBEDDING_MODEL'],
              'inputs': texts, 'source': 'synthetic public editorial examples',
              'documentation': 'https://developers.openai.com/api/reference/resources/embeddings/methods/create'}
    try:
        vectors = _validated_vectors(await configured_embeddings(texts), len(texts))
        report.update(status='computed', vector_count=len(vectors), dimension=len(vectors[0]),
                      similar_pair_cosine=round(_cosine(vectors[0], vectors[1]), 5),
                      different_pair_cosine=round(_cosine(vectors[0], vectors[2]), 5))
        history = [{'id': 'public-history', 'customer_task': texts[0], 'angle': '课堂板书整理'}]
        candidates = [{'id': 'near', 'customer_task': texts[1], 'angle': '板书整理'},
                      {'id': 'different', 'customer_task': texts[2], 'angle': '夜景取字'}]
        before = hashlib.sha256(json.dumps(candidates, ensure_ascii=False).encode()).hexdigest()
        report['selection'] = await select_candidates(candidates, history=history)
        report['inputs_unchanged'] = before == hashlib.sha256(json.dumps(candidates, ensure_ascii=False).encode()).hexdigest()
    except Exception as exc:
        report.update(status='failed', error_type=type(exc).__name__)
    report['elapsed_seconds'] = round(time.monotonic()-started, 2)
    destination = ROOT/'runtime/final-embedding-verification.json'
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=True))


if __name__ == '__main__':
    asyncio.run(main())
