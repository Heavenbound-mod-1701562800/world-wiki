"""把用户问题分解成检索计划，并按计划取块。"""
# pylint: disable=line-too-long

from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from itertools import product
from typing import Literal

from libs.llm import LLM
from libs.store import Chunk, Store
from models.dictionary import Dictionary

Strategy = Literal["direct", "multi_aspects", "multi_queries"]
_FENCE_RE = re.compile(
    r"```(?:json)?\s*(.*?)\s*```", re.DOTALL | re.IGNORECASE
)
_SLOT_RE = re.compile(r"\{(\d+)\}")

SPLIT_PROMPT = """你只负责把用户输入切成互不从属的问题。只输出 JSON 字符串数组，不要解释。
每一项必须是用户原话的片段，不要翻译，不要改写，不要编新问。切开时按原句截取，连接词可以去掉，不要补字。

问号不是切开的必要条件。看两边是不是同一件事：同一谓语、多名对象，留在同一项；谓语或题眼换了，就是两问，必须切开。逗号、句号、没有标点，以及「另外」「顺便」「再讲」「还有」这类转题，只要题眼变了都要切。

「和」「与」「以及」只在两边是同一名单、同一问法时才不算新问题。两边问的不是同一件事时，它们连的是两问，必须切开。一组顿号或逗号并列的名字不是新问题，必须留在同一项里。比较或关系题不要拆开。只有一问时，数组里就一项原句。"""

SLOT_PROMPT = """你是一个问题拆分助手。程序按 aspects 的条数填槽，不按 names 的个数填槽。{0} 只对应 aspects[0] 整列，{1} 只对应 aspects[1] 整列。某一列 members 里有三个名字，骨架里仍然只写一个 {0}：程序会生成三句，每句把 {0} 换成其中一个名字。禁止把同一列展开成 {0}, {1}, and {2}。contains 为 true 的那一条会填成 from 一端 to 另一端，并且每一格都带上这一段。问句里已经点名并排成并列的对象必须进 members，绝不能写进 query 骨架，也不要用 and 把它们连在句子里。只输出 JSON，不要解释。

返回值是一个 JSON 数组，每一项一路检索。一项只有 query 和 aspects。query 是一句英文骨架。占位符个数必须等于 aspects 数组的长度，绝不是某一条 members 的长度。有 n 条 aspect 时，query 里必须恰好出现编号 0 到 n-1 的占位符各一次。不要写类型，不要补问句里没有的名单。专名和普通词都写成完整成分的英文：对照表里有的用表内英文，没有的按字面翻译。

输入只有一个问题。没有已点名并列时，不要占位符，aspects 为空，整句写在 query：
[{"query": "英文检索句", "aspects": []}]

有一组已点名并列时，这些名字全部放进同一条 members，骨架里只写一个 {0}。未列名的各、每写进句子，不要空 members：
[{"query": "英文骨架 {0}", "aspects": [{"members": ["专名", "专名", "专名"], "contains": false}]}]

有两组不同类的已点名并列时，必须是两条 aspect、两个占位符。把其中一组用 and 写进 query、只给另一组建 aspect，是错的：
[{"query": "英文骨架 {0} {1}", "aspects": [{"members": ["专名", "专名", "专名"], "contains": false}, {"members": ["专名", "专名"], "contains": false}]}]

判断并列的办法是看连接方式，而不是看你觉得该不该分路。顿号、逗号、「和」「与」「以及」，以及「都」「均」「分别」列出的多个已写名字，或任何其它形式的并列，每一组都是一条 aspect。时期、地点、对象只要是这样列出来的，一律按并列处理。同一组放进同一条 members，contains 为 false。不要写空的 members。某一类如果只出现一个已写名字并且不分路，把该名写进 query，不要为它建 aspect。

在遇到区间（比如从一端到另一端）时，不是上面那种分路并列。写成一条 contains 为 true 的 aspect，members 必须是两端两个字符串，不要把 from A to B 写成一个元素。程序会把该槽填成 from A to B，所以骨架里不要再把 from 或 during 套在这个占位符外面。区间会加到每一格上，不要拿两端去和其它名单叉乘。同一问题里既有区间、又有其它已点名并列时，写在同一项里：区间一条 aspect，并列再各写一条，各占一个编号槽。

问的是对象之间的关系或比较时，不要分路，整句放进 query，aspects 为空数组。只有问候和空壳客气话可以省略。对照表只用于译名，不是切分边界；一个成分即使只命中表里的一段专名，仍作为完整一词，不要截成表里的子串。"""


@dataclass
class Aspect:
    """一条分配维或区间维。"""

    members: list[str] = field(default_factory=list)
    contains: bool = False


@dataclass
class PlanCell:
    """aspects 展开后的一路：骨架、叉乘 picks、区间窗、按 aspect 下标填好的槽。"""

    query: str
    picks: dict[str, str | None]
    windows: list[Aspect]
    slots: list[str] = field(default_factory=list)


@dataclass
class QueryPlan:
    """一次问题分解：策略、检索骨架、多维 aspects。"""

    strategy: Strategy
    query: str
    aspects: list[Aspect] = field(default_factory=list)

    def queries(self) -> list[PlanCell]:
        """这份计划要分别检索的查询。"""
        windows = [item for item in self.aspects if item.contains]
        discrete_idx = [
            i for i, item in enumerate(self.aspects) if not item.contains
        ]
        if not discrete_idx:
            return [
                PlanCell(
                    query=self.query,
                    picks={},
                    windows=windows,
                    slots=[_slot_value(item) for item in self.aspects],
                )
            ]
        axes = [
            [(str(i), name) for name in self.aspects[i].members]
            or [(str(i), None)]
            for i in discrete_idx
        ]
        out: list[PlanCell] = []
        for combo in product(*axes):
            picks = dict(combo)
            slots = [
                _slot_value(item, picks.get(str(i)))
                for i, item in enumerate(self.aspects)
            ]
            out.append(
                PlanCell(
                    query=self.query,
                    picks=picks,
                    windows=windows,
                    slots=slots,
                )
            )
        return out


@dataclass
class Gathered:
    """一次计划检索：封顶后的块，以及每格命中。"""

    chunks: list[Chunk]
    cells: list[tuple[PlanCell, list[Chunk]]]


@dataclass
class Planner:
    """切问填槽，再按计划检索。"""

    llm: LLM = field(default_factory=LLM)
    store: Store | None = None
    cell_k: int = 4
    max_chunks: int = 16

    def plan(self, question: str) -> list[QueryPlan]:
        """先切成互不从属的原话，再对每条填槽规划。"""
        q = question.strip()
        if not q:
            raise ValueError("问题不能为空")
        parts = self.split_questions(q)
        if len(parts) == 1:
            return self.plan_one(parts[0])
        blocks: list[list[QueryPlan] | None] = [None] * len(parts)
        with ThreadPoolExecutor(max_workers=len(parts)) as pool:
            futures = {
                pool.submit(self.plan_one, part): index
                for index, part in enumerate(parts)
            }
            for future in as_completed(futures):
                blocks[futures[future]] = future.result()
        out: list[QueryPlan] = []
        for block in blocks:
            assert block is not None
            out.extend(block)
        return out

    def split_questions(self, question: str) -> list[str]:
        messages = [
            {"role": "system", "content": SPLIT_PROMPT},
            {"role": "user", "content": f"问题：{question}"},
        ]
        raw = self.chat(messages)
        try:
            return _questions_from_text(raw)
        except ValueError as err:
            raw = self.chat(
                messages
                + [
                    {"role": "assistant", "content": raw},
                    {
                        "role": "user",
                        "content": (
                            f"校验失败：{err}。"
                            "只输出 JSON 字符串数组，每项是用户原话片段。"
                            "题眼变了必须切开，问号不是必要条件。"
                            "不要翻译，不要拆同一问法下的顿号并列。"
                        ),
                    },
                ]
            )
            try:
                return _questions_from_text(raw)
            except ValueError:
                return [question]

    def plan_one(self, question: str) -> list[QueryPlan]:
        glossary = Dictionary.matches_in(question)
        user = (
            f"问题：{question}\n专名对照：\n"
            f"{Dictionary.format_glossary(glossary)}"
        )
        messages = [
            {"role": "system", "content": SLOT_PROMPT},
            {"role": "user", "content": user},
        ]
        last_error: ValueError | None = None
        for _ in range(3):
            raw = self.chat(messages)
            try:
                return _plan_from_text(raw, glossary)
            except ValueError as err:
                last_error = err
                messages = messages + [
                    {"role": "assistant", "content": raw},
                    {
                        "role": "user",
                        "content": (
                            f"校验失败：{err}。"
                            "占位符个数必须等于 aspects 的条数，不是 members 的个数。"
                            "两组并列必须两条 aspect，骨架只用编号占位符，"
                            "禁止把并列专名写进 query。"
                            "contains 为 true 时 members 必须是两端两个字符串，"
                            "不要写成一个 from A to B。只输出 JSON。"
                        ),
                    },
                ]
        assert last_error is not None
        raise last_error

    def chat(self, messages: list[dict[str, str]]) -> str:
        return self.llm.chat(
            messages, temperature=0.0, max_tokens=1024, thinking=False
        )

    def gather(
        self, plans: list[QueryPlan], *, max_chunks: int | None = None
    ) -> Gathered:
        """按格检索、去重、封顶。"""
        if not plans:
            raise ValueError("计划不能为空")
        if self.store is None:
            self.store = Store()
        if self.store.count() == 0:
            raise RuntimeError("向量库为空。请先运行 --ingest。")
        cell_hits: list[tuple[PlanCell, list[Chunk]]] = []
        for item in plans:
            for query in item.queries():
                cell_hits.append((query, self._retrieve(query)))
        limit = self.max_chunks if max_chunks is None else max_chunks
        return Gathered(
            chunks=_round_robin([hits for _cell, hits in cell_hits], limit),
            cells=cell_hits,
        )

    def _retrieve(self, cell: PlanCell) -> list[Chunk]:
        query = _compose_query(cell)
        if not query:
            return []
        assert self.store is not None
        return self.store.query(query, top_k=self.cell_k)


def _questions_from_text(text: str) -> list[str]:
    payload = _parse_json(text)
    if not isinstance(payload, list) or not payload:
        raise ValueError("切问必须是非空 JSON 数组")
    out: list[str] = []
    for i, item in enumerate(payload):
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"切问[{i}] 无效")
        out.append(item.strip())
    return out


def _plan_from_text(
    text: str, glossary: list[tuple[str, str]] | None = None
) -> list[QueryPlan]:
    payload = _parse_json(text)
    if not isinstance(payload, list) or not payload:
        raise ValueError("计划必须是非空 JSON 数组")
    pairs = glossary or []
    return [_slot_to_plan(slot, pairs) for slot in payload]


def _slot_to_plan(slot: dict, pairs: list[tuple[str, str]]) -> QueryPlan:
    if not isinstance(slot, dict):
        raise ValueError("计划项必须是对象")
    raw_query = slot.get("query")
    if not isinstance(raw_query, str) or not raw_query.strip():
        raise ValueError("缺少 query")
    query = raw_query.strip()
    aspects = _parse_aspects(slot, pairs)
    _require_placeholders(query, len(aspects))
    discrete = [item for item in aspects if not item.contains]
    strategy: Strategy = "multi_aspects" if discrete else "direct"
    return QueryPlan(strategy=strategy, query=query, aspects=aspects)


def _parse_aspects(slot: dict, pairs: list[tuple[str, str]]) -> list[Aspect]:
    raw = slot.get("aspects") or []
    if not isinstance(raw, list):
        raise ValueError("aspects 必须是数组")
    aspects: list[Aspect] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        contains = item.get("contains") in (True, "true", "True")
        members = [
            _whole_to_en(name, pairs)
            for name in _string_list(item.get("members"), "members")
        ]
        if contains:
            if len(members) == 1:
                ends = _split_range(members[0], pairs)
                if ends:
                    members = list(ends)
            if len(members) >= 2:
                aspects.append(Aspect(members=members, contains=True))
            elif members:
                aspects.append(Aspect(members=members, contains=False))
        elif members:
            aspects.append(Aspect(members=members, contains=False))
    return aspects


def _require_placeholders(query: str, count: int) -> None:
    found = {int(match.group(1)) for match in _SLOT_RE.finditer(query)}
    expected = set(range(count))
    if found != expected:
        raise ValueError(
            f"占位符必须与 aspects 下标对齐（query 有 {sorted(found)}，aspects 有 {count} 条）"
        )


def _split_range(
    text: str, pairs: list[tuple[str, str]]
) -> tuple[str, str] | None:
    folded = text.casefold()
    left = right = ""
    if folded.startswith("from ") and " to " in folded:
        rest = text[5:]
        pivot = rest.casefold().find(" to ")
        if pivot >= 0:
            left, right = rest[:pivot].strip(), rest[pivot + 4 :].strip()
    elif " to " in folded:
        pivot = folded.find(" to ")
        left, right = text[:pivot].strip(), text[pivot + 4 :].strip()
    elif text.startswith("从"):
        pivot = text.find("到", 1)
        if pivot > 0:
            left, right = text[1:pivot].strip(), text[pivot + 1 :].strip()
    if left and right:
        return _whole_to_en(left, pairs), _whole_to_en(right, pairs)
    return None


def _slot_value(aspect: Aspect, pick: str | None = None) -> str:
    if aspect.contains:
        ends = [item.strip() for item in aspect.members if item.strip()]
        if len(ends) >= 2:
            return f"from {ends[0]} to {ends[-1]}"
        return ends[0] if ends else ""
    return (pick or "").strip()


def _string_list(raw: object, name: str) -> list[str]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError(f"{name} 必须是数组")
    out: list[str] = []
    for i, item in enumerate(raw):
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"{name}[{i}] 无效")
        out.append(item.strip())
    return out


def _whole_to_en(text: str, pairs: list[tuple[str, str]]) -> str:
    """只在整段等于某条对照时换成英文，不做串内替换。"""
    fold = text.casefold()
    for en, zh in pairs:
        if en.casefold() == fold or zh == text:
            return en
    return text


def _parse_json(text: str) -> object:
    blob = (text or "").strip()
    if not blob:
        raise ValueError("计划回复为空")
    fenced = _FENCE_RE.search(blob)
    if fenced:
        blob = fenced.group(1).strip()
    try:
        return json.loads(blob)
    except json.JSONDecodeError:
        raise ValueError("计划不是合法 JSON") from None


def _compose_query(cell: PlanCell) -> str:
    def repl(match: re.Match[str]) -> str:
        index = int(match.group(1))
        if 0 <= index < len(cell.slots):
            return cell.slots[index]
        return match.group(0)

    return _SLOT_RE.sub(repl, cell.query).strip()


def _round_robin(groups: list[list[Chunk]], limit: int) -> list[Chunk]:
    out: list[Chunk] = []
    seen: set[str] = set()
    index = 0
    while len(out) < limit:
        progressed = False
        for hits in groups:
            if index >= len(hits):
                continue
            chunk = hits[index]
            progressed = True
            if chunk.id in seen:
                continue
            seen.add(chunk.id)
            out.append(chunk)
            if len(out) >= limit:
                return out
        if not progressed:
            break
        index += 1
    return out
