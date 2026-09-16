"""景点业务层：核对模型景点与门票的依据，将正文和地图结果组成卡片。"""

import re
from decimal import Decimal

from app.schemas.document.answer import (
    AttractionCard,
    AttractionDraft,
    MapLookup,
    TicketInfo,
    WebEvidence,
)
from app.schemas.document.search import SearchHit

"""证据整理函数：本地片段用1至5编号，网页用6至25编号，避免补查来源串号。"""


def evidence_texts(hits: list[SearchHit], web: list[WebEvidence]) -> dict[int, str]:
    return {
        **{index: hit.chunk.text + "\n" + "\n".join(hit.chunk.section_path)
           for index, hit in enumerate(hits, 1)},
        **{item.id: item.content + "\n" + item.title for item in web},
    }


"""详情整理函数：先核对推荐主体，再单独核对门票和地址，失败字段不连累整张卡片。"""


def prepare_attractions(
    attractions: list[AttractionDraft], texts: dict[int, str], sandbox_ids: set[int],
    lookups: list[MapLookup] | None = None,
) -> bool:
    cores = [item.model_copy(update={"ticket": TicketInfo(), "address_evidence": None})
             for item in attractions]
    for core in cores:
        anchors = [index for index, text in texts.items()
                   if core.city in text and core.name in text]
        if anchors and not set(core.source_ids).intersection(anchors) and len(core.source_ids) < 5:
            core.source_ids = [*core.source_ids, anchors[0]]
    validate_attractions(cores, texts, sandbox_ids, lookups)
    incomplete = False
    for item, core in zip(attractions, cores, strict=True):
        for field in ("ticket", "address_evidence"):
            detail = getattr(item, field)
            if detail is None or (field == "ticket" and detail.status == "unknown"):
                continue
            trial = core.model_copy(update={field: detail})
            # 来源只填一次也可以；编号必须确实存在，详情仍经过原句与地点核对。
            if detail.source_id in texts and detail.source_id not in trial.source_ids:
                if len(trial.source_ids) < 5:
                    trial.source_ids = [*trial.source_ids, detail.source_id]
            try:
                validate_attractions([trial], texts, sandbox_ids, lookups)
            except ValueError:
                incomplete = True
                if field == "ticket":
                    clear_ticket_claims(core)
                else:
                    clear_address_claims(core, detail.text)
                continue
            core = trial
        item.ticket = core.ticket
        item.address_evidence = core.address_evidence
        item.source_ids = core.source_ids
        item.description, item.reason = core.description, core.reason
    return incomplete


"""收费清理函数：未通过票据核对时，正文也不能重复那个收费结论。"""


def clear_ticket_claims(item: AttractionDraft) -> None:
    for field in ("description", "reason"):
        if re.search(r"门票|票价|免费|收费|\d+\s*元", getattr(item, field)):
            setattr(item, field, f"可先了解{item.name}，具体收费信息尚未核实。")


"""地址清理函数：补充地址未通过时，也移除正文中的地址或门牌描述。"""


def clear_address_claims(item: AttractionDraft, address: str | None = None) -> None:
    for field in ("description", "reason"):
        body = getattr(item, field)
        if (address and address in body) or re.search(r"地址|位于|坐落|[路街巷弄].{0,12}号", body):
            setattr(item, field, f"可先了解{item.name}，详细地址尚未核实。")


"""景点校验函数：地点必须来自同条证据，门票金额和适用说明必须能找到原句。"""


def validate_attractions(
    attractions: list[AttractionDraft], texts: dict[int, str], sandbox_ids: set[int],
    lookups: list[MapLookup] | None = None,
) -> None:
    seen: set[tuple[str, str]] = set()
    for item in attractions:
        pair = (item.city, item.name)
        if pair in seen or not set(item.source_ids).issubset(texts):
            raise ValueError("景点重复或缺少依据")
        seen.add(pair)
        if not any(item.city in texts[index] and item.name in texts[index]
                   for index in item.source_ids):
            raise ValueError(
                f"景点名称“{item.name}”和地点“{item.city}”未同时原样出现在证据"
                f"{item.source_ids}中。请复制原文名称和省市州县称呼，不组合新名称或扩写全称。"
            )
        names = [item.name] + [lookup.matched_name for lookup in lookups or []
                              if lookup.status == "found" and lookup.city == item.city
                              and lookup.name == item.name and lookup.matched_name]
        # 已有工具地址就直接使用；模型重抄的地址不能冒充资料原文，也不参与展示。
        if any(lookup.status == "found" and lookup.address and lookup.city == item.city
               and lookup.name == item.name for lookup in lookups or []):
            item.address_evidence = None
        address = item.address_evidence
        if address is not None:
            source = texts.get(address.source_id, "")
            if (address.source_id not in item.source_ids or address.quote not in source
                    or address.text not in address.quote
                    or not any(address.text in sentence and any(name in sentence for name in names)
                               for sentence in re.split(r"[。！？；;\n]", address.quote))
                    or item.city not in source):
                raise ValueError("地址缺少同地点的原文依据")
        ticket = item.ticket
        if ticket.status == "unknown":
            continue
        if (ticket.source_id not in item.source_ids or ticket.source_id in sandbox_ids
                or ticket.quote is None or ticket.quote not in texts[ticket.source_id]):
            raise ValueError("门票原句缺失或来自模拟价格")
        quote = ticket.quote
        if ticket.source_id >= 6 and not any(
            any(name in sentence for name in names)
            and any(word in sentence for word in ("门票", "票价", "免费", "免票", "购票"))
            and (ticket.amount is None or str(ticket.amount) in sentence)
            for sentence in re.split(r"[。！？；;\n]", quote)
        ):
            raise ValueError("网页门票原句缺少对应景点名称，不能借用同篇文章的其他票价")
        if "price" in quote.lower():
            raise ValueError("模拟价格不能作门票")
        if ticket.status == "free" and (
            not any(word in quote for word in ("免费", "免票", "无需门票", "不收门票", "无需购票"))
            or any(word in quote for word in ("不免费", "非免费", "取消免费", "不免票", "取消免票"))
        ):
            raise ValueError("免费缺少明确依据")
        if ticket.status == "free" and (
            any(word in quote for word in (
                "儿童", "老人", "老年", "学生", "身高", "限时", "特定日期",
            ))
            or ticket.applicable_date is not None
            or re.search(r"\d{1,2}月\d{1,2}日|\d{4}-\d{1,2}-\d{1,2}", quote)
            or any(Decimal(value) > 0 for value in re.findall(r"(\d+(?:\.\d+)?)\s*元", quote))
        ):
            raise ValueError("限定人群免费或同时有收费，必须保留部分收费说明")
        if ticket.status in {"paid", "partial"} and not any(
            word in quote for word in ("门票", "票价", "成人票", "儿童票", "入园票")
        ):
            raise ValueError("消费信息不能作为门票")
        if ticket.amount is not None and not (ticket.status == "free" and ticket.amount == 0):
            amounts = [Decimal(value) for value in re.findall(r"(\d+(?:\.\d+)?)\s*元", quote)]
            if ticket.amount not in amounts:
                raise ValueError("票价金额没有对应人民币原句")
        if ticket.ticket_type is not None and ticket.ticket_type not in quote:
            raise ValueError("票种缺少依据")
        # 只统一日期写法：2026年2月22日与2026-02-22是同一天，原句仍原样保存。
        dated_quote = re.sub(
            r"(\d{4})年(\d{1,2})月(\d{1,2})日",
            lambda match: f"{match[1]}-{int(match[2]):02d}-{int(match[3]):02d}", quote,
        )
        if (ticket.applicable_date is not None and ticket.applicable_date not in quote
                and ticket.applicable_date not in dated_quote):
            raise ValueError("适用时间缺少依据")
        # 页面使用自然说明，但其中的数字也必须来自原句，不能另编一个票价或年份。
        numbers = {Decimal(value) for value in re.findall(r"\d+(?:\.\d+)?", quote)}
        if any(Decimal(value) not in numbers
               for value in re.findall(r"\d+(?:\.\d+)?", ticket.summary)):
            raise ValueError("门票说明出现了原句没有的数字")
        # 年份和年龄也是数字，但不能被改成票价；金额须单独按“数字＋元”核对。
        prices = {Decimal(value) for value in re.findall(r"(\d+(?:\.\d+)?)\s*元", quote)}
        if any(Decimal(value) not in prices
               for value in re.findall(r"(\d+(?:\.\d+)?)\s*元", ticket.summary)):
            raise ValueError("门票说明金额与原句不一致")


"""卡片组装函数：坐标只取工具结果，新出现但未核对地图的景点不能混入最终卡片。"""


def build_attraction_cards(
    attractions: list[AttractionDraft], lookups: list[MapLookup],
) -> list[AttractionCard]:
    positions = {(item.city, item.name): item for item in lookups}
    cards: list[AttractionCard] = []
    for item in attractions:
        if (item.city, item.name) not in positions:
            raise ValueError("最终景点未经过地点核对")
        cards.append(AttractionCard(**item.model_dump(), location=positions[item.city, item.name]))
    return cards
