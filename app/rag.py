"""Lightweight, explainable Markdown retrieval for policy/SOP questions."""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

from .models import Source, ToolResult


_STOPWORDS = {
    "的",
    "了",
    "和",
    "与",
    "是",
    "在",
    "有",
    "请",
    "一下",
    "什么",
    "怎么",
    "如何",
    "哪些",
    "我们",
    "需要",
    "可以",
    "the",
    "a",
    "an",
    "of",
    "to",
    "and",
    "is",
}

_SYNONYM_GROUPS: Tuple[Tuple[str, ...], ...] = (
    ("授权", "权限", "审批", "approval", "authorization"),
    ("分级", "等级", "tier", "level", "分类"),
    ("sop", "标准作业", "标准流程", "操作规范", "流程"),
    ("机构", "医院", "客户", "org", "institution"),
    ("销售", "营收", "收入", "revenue", "sales"),
    ("拜访", "走访", "visit", "沟通"),
    ("合规", "规范", "制度", "policy", "compliance"),
)

_DOC_TYPE_FILES = {
    "product": "01_company_and_products.md",
    "metrics": "02_metrics_definitions.md",
    "playbook": "03_sales_playbook.md",
    "policy": "04_pricing_and_discount_policy.md",
    "support": "05_customer_success_and_escalation.md",
    "data": "06_data_dictionary.md",
    "authorization": "07_new_product_authorization.md",
}


@dataclass(frozen=True)
class KnowledgeChunk:
    file: str
    title: str
    section: str
    text: str
    order: int

    @property
    def citation(self) -> str:
        anchor = re.sub(r"\s+", "-", self.section.strip()) or "document"
        return f"[KB:{self.file}#{anchor}]"


class MarkdownKnowledgeBase:
    """In-memory lexical index designed for a small internal knowledge base."""

    def __init__(self, directory: Path) -> None:
        self.directory = Path(directory)
        self.chunks: List[KnowledgeChunk] = []
        self._document_frequency: Counter[str] = Counter()
        self.refresh()

    def refresh(self) -> None:
        chunks: List[KnowledgeChunk] = []
        if self.directory.exists():
            for path in sorted(self.directory.rglob("*.md")):
                try:
                    text = path.read_text(encoding="utf-8")
                except (OSError, UnicodeError):
                    continue
                relative = str(path.relative_to(self.directory))
                chunks.extend(self._parse_document(relative, text))
        self.chunks = chunks
        self._document_frequency = Counter()
        for chunk in chunks:
            self._document_frequency.update(set(tokenize(f"{chunk.title} {chunk.section} {chunk.text}")))

    @staticmethod
    def _parse_document(filename: str, text: str) -> List[KnowledgeChunk]:
        title = Path(filename).stem
        section = title
        sections: List[Tuple[str, List[str]]] = []
        current: List[str] = []
        for line in text.splitlines():
            heading = re.match(r"^\s{0,3}(#{1,6})\s+(.+?)\s*$", line)
            if heading:
                if current and any(item.strip() for item in current):
                    sections.append((section, current))
                section = heading.group(2).strip().strip("#")
                if len(heading.group(1)) == 1:
                    title = section
                current = []
            else:
                current.append(line)
        if current and any(item.strip() for item in current):
            sections.append((section, current))
        if not sections and text.strip():
            sections = [(section, [text])]

        chunks: List[KnowledgeChunk] = []
        order = 0
        for heading, lines in sections:
            paragraphs = _paragraphs(lines)
            buffer = ""
            for paragraph in paragraphs:
                if buffer and len(buffer) + len(paragraph) > 900:
                    chunks.append(KnowledgeChunk(filename, title, heading, buffer.strip(), order))
                    order += 1
                    buffer = paragraph
                else:
                    buffer = f"{buffer}\n{paragraph}".strip()
            if buffer:
                chunks.append(KnowledgeChunk(filename, title, heading, buffer.strip(), order))
                order += 1
        return chunks

    def retrieve(
        self,
        query: str,
        *,
        top_k: int = 4,
        filters: Optional[Mapping[str, Any]] = None,
    ) -> List[Tuple[KnowledgeChunk, float]]:
        if not self.chunks:
            return []
        query_tokens = expand_terms(set(tokenize(query)))
        query_compact = re.sub(r"\s+", "", query.casefold())
        filters = dict(filters or {})
        file_filter = str(filters.get("file", "")).casefold()
        doc_type = str(filters.get("doc_type", "")).casefold()
        if doc_type and not file_filter:
            file_filter = _DOC_TYPE_FILES.get(doc_type, doc_type).casefold()
        section_filter = str(filters.get("section", "")).casefold()
        scored: List[Tuple[KnowledgeChunk, float]] = []
        chunk_count = len(self.chunks)
        for chunk in self.chunks:
            if file_filter and file_filter not in chunk.file.casefold():
                continue
            if section_filter and section_filter not in chunk.section.casefold():
                continue
            title_tokens = expand_terms(set(tokenize(f"{chunk.title} {chunk.section}")))
            body_tokens = tokenize(chunk.text)
            counts = Counter(body_tokens)
            score = 0.0
            matched = 0
            for token in query_tokens:
                frequency = counts.get(token, 0)
                if not frequency and token not in title_tokens:
                    continue
                matched += 1
                df = self._document_frequency.get(token, 0)
                inverse_frequency = math.log(1.0 + (chunk_count + 1) / (df + 1))
                score += inverse_frequency * (1.0 + min(frequency, 3) * 0.25)
                if token in title_tokens:
                    score += 1.8
            compact_heading = re.sub(r"\s+", "", chunk.section.casefold())
            compact_body = re.sub(r"\s+", "", chunk.text.casefold())
            if query_compact and len(query_compact) >= 3 and query_compact in compact_body:
                score += 5.0
            for group in _SYNONYM_GROUPS:
                if any(term in query.casefold() for term in group) and any(
                    term in f"{compact_heading} {compact_body}" for term in group
                ):
                    score += 1.2
            if matched:
                score *= 0.65 + 0.35 * matched / max(len(query_tokens), 1)
                scored.append((chunk, score))
        scored.sort(key=lambda item: (-item[1], item[0].file, item[0].order))
        return scored[: max(1, min(int(top_k), 10))]

    def search_knowledge(
        self,
        query: str,
        *,
        top_k: int = 4,
        filters: Optional[Mapping[str, Any]] = None,
    ) -> ToolResult:
        ranked = self.retrieve(query, top_k=top_k, filters=filters)
        if not ranked:
            warning = "知识库中没有找到足以支持回答的条目。"
            if not self.chunks:
                warning = f"知识库目录为空或不存在：{self.directory}"
            return ToolResult(
                name="search_knowledge",
                data={"query": query, "matches": []},
                summary="未找到可引用的内部知识依据。",
                sources=[],
                confidence=0.0,
                warnings=[warning],
            )

        best_score = ranked[0][1]
        matches: List[Dict[str, Any]] = []
        sources: List[Source] = []
        answer_parts: List[str] = []
        seen_citations: Set[str] = set()
        query_tokens = expand_terms(set(tokenize(query)))
        for chunk, score in ranked:
            excerpt = _best_excerpt(chunk.text, query_tokens)
            matches.append(
                {
                    "file": chunk.file,
                    "title": chunk.title,
                    "section": chunk.section,
                    "excerpt": excerpt,
                    "score": round(score, 3),
                    "citation": chunk.citation,
                }
            )
            if chunk.citation not in seen_citations:
                sources.append(
                    Source(
                        id=chunk.citation[1:-1],
                        type="knowledge_base",
                        file=f"knowledge_base/{chunk.file}",
                        section=chunk.section,
                        citation=chunk.citation,
                        description=f"{chunk.title} / {chunk.section}",
                        score=round(score, 3),
                    )
                )
                seen_citations.add(chunk.citation)
            if excerpt:
                answer_parts.append(f"- {excerpt} {chunk.citation}")
        confidence = min(0.98, 0.48 + math.log1p(max(best_score, 0.0)) / 4.0)
        summary = "根据内部知识库：\n" + "\n".join(answer_parts[:4])
        return ToolResult(
            name="search_knowledge",
            data={"query": query, "matches": matches},
            summary=summary,
            sources=sources,
            confidence=confidence,
        )

    def status(self) -> Dict[str, Any]:
        return {
            "knowledge_dir": str(self.directory),
            "documents": len({chunk.file for chunk in self.chunks}),
            "chunks": len(self.chunks),
        }


def tokenize(text: str) -> List[str]:
    normalized = str(text or "").casefold()
    latin = re.findall(r"[a-z][a-z0-9_-]{1,}|\d+(?:\.\d+)?", normalized)
    chinese_runs = re.findall(r"[\u4e00-\u9fff]+", normalized)
    chinese: List[str] = []
    for run in chinese_runs:
        if len(run) <= 4:
            chinese.append(run)
        for width in (2, 3, 4):
            chinese.extend(run[index : index + width] for index in range(max(0, len(run) - width + 1)))
    return [token for token in latin + chinese if token not in _STOPWORDS and len(token) > 1]


def expand_terms(tokens: Set[str]) -> Set[str]:
    expanded = set(tokens)
    for group in _SYNONYM_GROUPS:
        if any(any(term in token or token in term for token in tokens) for term in group):
            expanded.update(group)
    return expanded


def _paragraphs(lines: Sequence[str]) -> List[str]:
    paragraphs: List[str] = []
    current: List[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            if current:
                paragraphs.append(" ".join(current))
                current = []
            continue
        if re.match(r"^(?:[-*+] |\d+[.)、] )", stripped) and current:
            paragraphs.append(" ".join(current))
            current = [stripped]
        else:
            current.append(stripped)
    if current:
        paragraphs.append(" ".join(current))
    return paragraphs


def _best_excerpt(text: str, query_tokens: Set[str], limit: int = 720) -> str:
    compact = re.sub(r"\s+", " ", text).strip()
    # Knowledge chunks are already section-bounded.  Returning the complete
    # small section avoids dropping the second half of a rule/table (for
    # example, a response time and its corresponding recovery target).
    if len(compact) <= limit:
        return compact
    candidates = [item.strip() for item in re.split(r"(?<=[。！？；.!?;])\s+|\n+", text) if item.strip()]
    if not candidates:
        return ""
    ranked = sorted(
        enumerate(candidates),
        key=lambda item: (
            -sum(1 for token in query_tokens if token in item[1].casefold()),
            item[0],
        ),
    )
    chosen = " ".join(item[1] for item in ranked[:3])
    chosen = re.sub(r"\s+", " ", chosen).strip()
    return chosen if len(chosen) <= limit else chosen[: limit - 1].rstrip() + "…"
