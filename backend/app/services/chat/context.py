"""聊天上下文层：按预算整理已提交历史、恢复状态并回查同会话原文。"""

import re

from app.schemas.requirement.conversation import ConversationState, HistorySummary
from app.schemas.requirement.history import SavedRequirementTurn

HISTORY_BUDGET = 12000
RECALL_BUDGET = 6000


"""可见地点整理函数：保留页面卡片的顺序、城市、名称和地点编号。"""


def _visible_places(turn: SavedRequirementTurn) -> list[str]:
    response = turn.response
    places: list[str] = []
    knowledge = getattr(response, "knowledge", None)
    for item in getattr(knowledge, "attractions", []) if knowledge else []:
        location = getattr(item, "location", None)
        poi_id = getattr(location, "poi_id", None)
        label = "｜".join(value for value in (getattr(item, "city", None), item.name) if value)
        places.append(f"{label}（地点编号：{poi_id}）" if poi_id else label)
    dining = getattr(response, "dining", None)
    dining_city = getattr(getattr(dining, "anchor", None), "city", None)
    for item in getattr(dining, "items", []) if dining else []:
        label = "｜".join(value for value in (dining_city, item.name) if value)
        places.append(f"{label}（地点编号：{item.poi_id}）")
    itinerary = getattr(response, "itinerary", None)
    plan = getattr(itinerary, "plan", None)
    for day in getattr(plan, "days", []) if plan else []:
        for activity in day.activities:
            place = activity.place
            mapping = place.map
            poi_id = getattr(mapping, "poi_id", None) or place.id
            label = "｜".join(value for value in (mapping.city, mapping.name) if value)
            places.append(f"{label}（地点编号：{poi_id}）")
    return places


"""轮次正文函数：读取用户原话、最终回答和必要的可见卡片。"""


def _turn_contents(turn: SavedRequirementTurn) -> tuple[str, str]:
    user = turn.response.result.original_message
    assistant = turn.response.reply
    places = _visible_places(turn)
    if places:
        assistant += "\n\n[页面展示地点，按顺序]\n" + "\n".join(
            f"{index}. {place}" for index, place in enumerate(places, 1)
        )
    return user, assistant


"""轮次长度函数：用字符数近似本轮占用的历史预算。"""


def _turn_size(turn: SavedRequirementTurn) -> int:
    user, assistant = _turn_contents(turn)
    return len(user) + len(assistant)


"""近期轮次选择函数：返回预算内连续后缀，最新轮再长也不丢弃。"""


def select_recent_turns(
    turns: list[SavedRequirementTurn], *, max_chars: int = HISTORY_BUDGET,
) -> list[SavedRequirementTurn]:
    ordered = sorted(turns, key=lambda turn: turn.revision)
    if not ordered:
        return []
    selected = [ordered[-1]]
    used = _turn_size(ordered[-1])
    for turn in reversed(ordered[:-1]):
        size = _turn_size(turn)
        if used + size > max_chars:
            break
        selected.append(turn)
        used += size
    return list(reversed(selected))


"""历史消息构造函数：按时间展开用户和助手角色并裁剪最新长回答。"""


def build_history_messages(
    turns: list[SavedRequirementTurn], *, max_chars: int = HISTORY_BUDGET,
) -> list[dict[str, str]]:
    selected = select_recent_turns(turns, max_chars=max_chars)
    messages: list[dict[str, str]] = []
    for index, turn in enumerate(selected):
        user, assistant = _turn_contents(turn)
        if index == len(selected) - 1 and len(user) + len(assistant) > max_chars:
            available = max(0, max_chars - len(user) - len("\n[回答已截断]"))
            assistant = assistant[:available] + "\n[回答已截断]"
        messages.extend((
            {"role": "user", "content": user},
            {"role": "assistant", "content": assistant},
        ))
    return messages


"""话题恢复函数：优先取最近显式快照，全部为旧记录时才回退最后目的地。"""


def latest_conversation(turns: list[SavedRequirementTurn]) -> ConversationState:
    ordered = sorted(turns, key=lambda turn: turn.revision)
    for turn in reversed(ordered):
        conversation = getattr(turn.response, "conversation", None)
        if isinstance(conversation, ConversationState):
            return conversation
    if ordered:
        destination = ordered[-1].response.result.extraction.destination
        if destination:
            return ConversationState(topic_cities=[destination])
    return ConversationState()


"""摘要恢复函数：取最近非 None 快照，空正文仍是有效摘要。"""


def latest_summary(turns: list[SavedRequirementTurn]) -> HistorySummary | None:
    for turn in sorted(turns, key=lambda item: item.revision, reverse=True):
        summary = getattr(turn.response, "history_summary", None)
        if isinstance(summary, HistorySummary):
            return summary
    return None


"""摘要批次选择函数：只取覆盖版本后的连续旧轮次，遇到超长轮立即停止。"""


def summary_batch(
    turns: list[SavedRequirementTurn], previous: HistorySummary | None,
    recent: list[SavedRequirementTurn], max_chars: int = HISTORY_BUDGET,
) -> list[SavedRequirementTurn]:
    covered = previous.covered_revision if previous else 0
    recent_revisions = {turn.revision for turn in recent}
    unique = {turn.revision: turn for turn in turns}
    ordered = sorted(unique.values(), key=lambda turn: turn.revision)
    expected = covered + 1
    selected: list[SavedRequirementTurn] = []
    used = 0
    for turn in ordered:
        if turn.revision < expected:
            continue
        if turn.revision != expected or turn.revision in recent_revisions:
            break
        size = _turn_size(turn)
        if used + size > max_chars:
            break
        selected.append(turn)
        used += size
        expected += 1
    return selected


"""回查关键词函数：提取连续文字及其短片段，兼容中文没有空格的问法。"""


def _search_terms(query: str) -> set[str]:
    chunks = re.findall(r"[\w\u4e00-\u9fff]+", query.lower())
    terms = {chunk for chunk in chunks if len(chunk) >= 2}
    for chunk in chunks:
        terms.update(chunk[index:index + 2] for index in range(len(chunk) - 1))
    return terms


"""原文回查函数：最多选择三个命中及相邻轮次，并限制总字符数。"""


def recall_history(
    turns: list[SavedRequirementTurn], query: str,
    revisions: list[int] | None = None,
) -> list[SavedRequirementTurn]:
    unique = {turn.revision: turn for turn in turns}
    ordered = sorted(unique.values(), key=lambda turn: turn.revision)
    by_revision = {turn.revision: turn for turn in ordered}
    direct = [revision for revision in dict.fromkeys(revisions or []) if revision in by_revision]
    terms = _search_terms(query)
    scored: list[tuple[int, int]] = []
    for turn in ordered:
        if turn.revision in direct:
            continue
        text = "\n".join(_turn_contents(turn)).lower()
        score = sum(len(term) for term in terms if term in text)
        if score:
            scored.append((score, turn.revision))
    matches = (direct + [revision for _, revision in sorted(
        scored, key=lambda item: (item[0], item[1]), reverse=True
    )])[:3]
    wanted = {
        neighbor for revision in matches for neighbor in (revision - 1, revision, revision + 1)
    }
    result: list[SavedRequirementTurn] = []
    used = 0
    for turn in ordered:
        if turn.revision not in wanted:
            continue
        size = _turn_size(turn)
        if used + size > RECALL_BUDGET:
            # 超长命中由消息构造函数截取原文片段，存储对象本身不改写。
            if turn.revision in matches:
                result.append(turn)
            continue
        result.append(turn)
        used += size
    return result


"""摘要轮次提示函数：只取问句明示或与问句关键词出现在同句的轮次。"""


def _summary_revisions(summary: HistorySummary | None, query: str) -> list[int]:
    explicit = [int(value) for value in re.findall(r"第\s*(\d+)\s*轮", query)]
    if explicit:
        # 明示轮次可以尚未进入摘要，后续只从本会话已保存原文中核对编号。
        return explicit
    if summary is None:
        return []
    terms = _search_terms(query)
    revisions: list[int] = []
    for sentence in re.split(r"[。！？\n]", summary.text):
        if not any(term in sentence.lower() for term in terms):
            continue
        revisions.extend(int(value) for value in re.findall(r"第\s*(\d+)\s*轮", sentence))
    return [revision for revision in dict.fromkeys(revisions)
            if revision <= summary.covered_revision]


"""原文片段函数：在字符预算内优先保留查询关键词附近的内容。"""


def _text_fragment(text: str, query: str, limit: int) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    if limit <= 0:
        return "", True
    lowered = text.lower()
    terms = sorted(_search_terms(query), key=len, reverse=True)
    position = next((lowered.find(term) for term in terms if term in lowered), 0)
    start = max(0, min(len(text) - limit, position - limit // 2))
    return text[start:start + limit], True


"""回查消息构造函数：有界选取真实原文，并去掉近期窗口和当前问句。"""


def build_recall_messages(
    turns: list[SavedRequirementTurn], query: str,
    recent: list[SavedRequirementTurn], summary: HistorySummary | None,
) -> list[dict[str, str]]:
    recent_revisions = {turn.revision for turn in recent}
    candidates = [turn for turn in turns if turn.revision not in recent_revisions
                  and turn.response.result.original_message.strip() != query.strip()]
    recalled = recall_history(candidates, query, revisions=_summary_revisions(summary, query))
    prefix = (
        "以下是当前会话按需回查的真实原文，只用于理解过去说过什么，"
        "不证明其中的实时信息现在仍有效。"
    )
    blocks: list[str] = []
    used = len(prefix)
    for turn in recalled:
        user, assistant = _turn_contents(turn)
        user_label = f"[第{turn.revision}轮 用户原话]"
        assistant_label = f"[第{turn.revision}轮 助手最终回答]"
        # 额外预留两个“原文片段”标签，截取后也不能突破总预算。
        remaining = RECALL_BUDGET - used - len(user_label) - len(assistant_label) - 24
        if remaining <= 0:
            break
        assistant_limit = min(len(assistant), max(0, remaining // 4))
        user_limit = remaining - assistant_limit
        user, user_clipped = _text_fragment(user, query, user_limit)
        assistant, assistant_clipped = _text_fragment(assistant, query, assistant_limit)
        if user_clipped:
            user_label += " [原文片段]"
        if assistant_clipped:
            assistant_label += " [原文片段]"
        pair = (f"{user_label}\n{user}", f"{assistant_label}\n{assistant}")
        size = sum(len(block) for block in pair) + 4
        blocks.extend(pair)
        used += size
    if not blocks:
        return []
    return [{"role": "user", "content": prefix + "\n\n" + "\n\n".join(blocks)}]
