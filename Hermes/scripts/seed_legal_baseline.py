"""
One-time seed of the federal-laws baseline into the legal RAG.

Run via:
  cd Hermes
  python scripts/seed_legal_baseline.py

What it does:
  1. Ensures the Qdrant collection exists.
  2. For each statute in BASELINE_STATUTES (152-ФЗ, ЗоЗПП, ГК глава 30
     купля-продажа, НК — УСН/НПД-релевантные статьи, 54-ФЗ, 44-ФЗ), uses
     hand-curated short summaries (NOT verbatim text — we don't want to
     mirror federal-law full text without provenance).
  3. Embeds + upserts as `scope='global'`. Every tenant sees these.

WHY hand-curated summaries (MVP):

  Real federal-law ingest is non-trivial — pravo.gov.ru ships XML but
  the chunking has to preserve article numbers / hierarchical context
  per statute. That's a one-week project on its own. For MVP we ship a
  dense summary so the Legal skill has *something* to cite, then refine
  later.

  Citations point to source_url so the seller can read the actual law
  on pravo.gov.ru. The skill's prompt explicitly states "если в
  источниках нет нужной нормы — признай это" — so when the summary is
  too coarse, the LLM refuses to overstate rather than hallucinating
  detail.

Idempotency: re-running drops existing 'global'-scope rows for the same
source first, then re-inserts. Safe to re-run on every deploy.
"""
from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

# Allow running as `python scripts/seed_legal_baseline.py` from Hermes/
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text as _sql_text  # noqa: E402

from services.db import tenant_session  # noqa: E402
from services.legal_rag import (  # noqa: E402
    ChunkInput,
    ensure_collection,
    ingest_chunks,
)


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("seed_legal_baseline")


# Curated short summaries. Each is a single chunk — long enough to be
# useful, short enough that the LLM doesn't have to discard most of it.
BASELINE_STATUTES: list[ChunkInput] = [
    ChunkInput(
        source="ЗоЗПП",
        article="ст. 18",
        title="Права потребителя при обнаружении недостатков",
        chunk_text=(
            "При продаже товара ненадлежащего качества потребитель вправе по своему выбору "
            "потребовать: 1) замены на товар той же марки; 2) замены на товар другой марки "
            "с пересчётом цены; 3) соразмерного уменьшения цены; 4) безвозмездного устранения "
            "недостатков; 5) возврата уплаченной суммы. Требования предъявляются продавцу "
            "(или уполномоченному лицу) в течение гарантийного срока, а если он не установлен — "
            "в пределах двух лет со дня передачи товара."
        ),
        chunk_index=0,
        source_url="http://www.consultant.ru/document/cons_doc_LAW_305/",
        version_tag="ru-zozpp-2024",
    ),
    ChunkInput(
        source="ЗоЗПП",
        article="ст. 25",
        title="Право обмена непродовольственного товара надлежащего качества",
        chunk_text=(
            "Потребитель вправе обменять непродовольственный товар надлежащего качества на "
            "аналогичный в течение 14 дней, не считая дня покупки, если товар не подошёл по форме, "
            "габаритам, фасону, расцветке, размеру или комплектации. Обмен возможен, если товар "
            "не был в употреблении, сохранён товарный вид, потребительские свойства, пломбы, ярлыки. "
            "Перечень товаров, не подлежащих обмену, утверждён Правительством РФ."
        ),
        chunk_index=0,
        source_url="http://www.consultant.ru/document/cons_doc_LAW_305/",
        version_tag="ru-zozpp-2024",
    ),
    ChunkInput(
        source="152-ФЗ",
        article="ст. 6",
        title="Обработка персональных данных — основания",
        chunk_text=(
            "Обработка персональных данных допускается на основании: согласия субъекта; "
            "договора с субъектом; для защиты жизни/здоровья; для осуществления возложенных "
            "законом функций; для статистических и исследовательских целей при обезличивании. "
            "Селлер на маркетплейсе обрабатывает ПДн покупателей (имя, адрес, контакты) на "
            "основании договора купли-продажи, заключаемого через оферту маркетплейса."
        ),
        chunk_index=0,
        source_url="http://www.consultant.ru/document/cons_doc_LAW_61801/",
        version_tag="ru-152fz-2024",
    ),
    ChunkInput(
        source="ГК РФ",
        article="ст. 469",
        title="Качество товара — общее правило",
        chunk_text=(
            "Продавец обязан передать товар, качество которого соответствует договору. Если в "
            "договоре нет условий о качестве — товар должен быть пригоден для целей, для которых "
            "товар такого рода обычно используется. Если продавец при заключении договора был "
            "поставлен в известность о конкретных целях покупателя — товар должен быть пригоден "
            "для этих целей. Описание в карточке маркетплейса — часть договора."
        ),
        chunk_index=0,
        source_url="http://www.consultant.ru/document/cons_doc_LAW_9027/",
        version_tag="ru-gk2-2024",
    ),
    ChunkInput(
        source="ГК РФ",
        article="ст. 475",
        title="Последствия передачи товара ненадлежащего качества",
        chunk_text=(
            "При существенных нарушениях требований к качеству (неустранимые недостатки, "
            "которые проявляются вновь после их устранения, требуют несоразмерных расходов) "
            "покупатель вправе отказаться от исполнения договора и потребовать возврата уплаченной "
            "суммы, либо потребовать замены товара."
        ),
        chunk_index=0,
        source_url="http://www.consultant.ru/document/cons_doc_LAW_9027/",
        version_tag="ru-gk2-2024",
    ),
    ChunkInput(
        source="НК РФ",
        article="ст. 346.43 / ст. 346.20",
        title="УСН и НПД для селлеров — режимы налогообложения",
        chunk_text=(
            "Селлеры обычно применяют один из режимов: УСН «доходы» (6% от выручки, регионы "
            "могут снижать до 1%); УСН «доходы минус расходы» (15%, регионы могут снижать до 5%); "
            "НПД для самозанятых (4% при продаже физлицам, 6% — юрлицам, лимит дохода 2.4 млн "
            "₽/год). Совмещение УСН и НПД запрещено. Выбор режима — при регистрации ИП или в "
            "течение 30 дней после."
        ),
        chunk_index=0,
        source_url="http://www.consultant.ru/document/cons_doc_LAW_28165/",
        version_tag="ru-nk-2024",
    ),
    ChunkInput(
        source="54-ФЗ",
        article="ст. 1.2",
        title="Онлайн-кассы — обязанности селлера",
        chunk_text=(
            "Селлер обязан применять ККТ при расчётах с покупателями, если он работает по УСН "
            "или ОСН. При торговле через маркетплейс кассу пробивает агент (маркетплейс), если "
            "это закреплено в договоре-оферте. Самозанятые (НПД) ККТ не применяют — выдают чек "
            "через приложение «Мой налог»."
        ),
        chunk_index=0,
        source_url="http://www.consultant.ru/document/cons_doc_LAW_200743/",
        version_tag="ru-54fz-2024",
    ),
    ChunkInput(
        source="Постановление №2463",
        article="перечень товаров",
        title="Товары, не подлежащие обмену/возврату",
        chunk_text=(
            "Не подлежат обмену/возврату надлежащего качества: товары личной гигиены, парфюмерия, "
            "товары санитарии, бельевые трикотажные изделия, чулочно-носочные, посуда, "
            "бытовая мебель (мебельные гарнитуры), оружие, ювелирные изделия из драгметаллов, "
            "автомобили, мотоциклы, прицепы. Списки уточняются Постановлением Правительства РФ "
            "№2463 от 31.12.2020."
        ),
        chunk_index=0,
        source_url="http://government.ru/docs/41271/",
        version_tag="ru-pp2463-2020",
    ),
]


async def _purge_global_for_source(source: str) -> int:
    """Drop existing global rows for a source (so re-runs don't duplicate)."""
    from services.legal_rag import _qdrant_post  # type: ignore[reportPrivateUsage]
    from config import settings

    async with tenant_session(None, admin=True) as s:
        rows = (
            await s.execute(
                _sql_text(
                    "SELECT qdrant_point_id FROM legal_documents "
                    "WHERE scope = 'global' AND source = :src"
                ),
                {"src": source},
            )
        ).all()
        point_ids = [r[0] for r in rows]
        if point_ids:
            await s.execute(
                _sql_text(
                    "DELETE FROM legal_documents "
                    "WHERE qdrant_point_id = ANY(:ids)"
                ),
                {"ids": point_ids},
            )
            await s.commit()
    if point_ids:
        try:
            await _qdrant_post(
                f"/collections/{settings.LEGAL_QDRANT_COLLECTION}/points/delete?wait=true",
                {"points": point_ids},
            )
        except Exception as exc:
            logger.warning(f"qdrant purge failed for {source}: {exc}")
    return len(point_ids)


async def main() -> None:
    await ensure_collection()
    by_source: dict[str, list[ChunkInput]] = {}
    for c in BASELINE_STATUTES:
        by_source.setdefault(c.source, []).append(c)

    total_in = 0
    for source, group in by_source.items():
        purged = await _purge_global_for_source(source)
        if purged:
            logger.info(f"purged {purged} existing chunks for {source}")
        count = await ingest_chunks(group, scope="global", tenant_id=None)
        total_in += count
        logger.info(f"ingested {count} chunks for {source}")

    logger.info(f"DONE — total chunks ingested: {total_in}")


if __name__ == "__main__":
    asyncio.run(main())
