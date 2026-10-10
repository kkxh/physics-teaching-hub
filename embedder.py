"""向量嵌入后端（Phase 5 实验分支 pilot）：接口隔离，可插拔。

本模块只管「文本 → 向量」这一件事：不碰数据库、不碰题目语义、
不下载模型。权重在使用者本机（经 Ollama 加载），hub 仓库不分发、
不内置任何模型文件。

工程约定：
- Embedder 是 Protocol：调用方只依赖接口，换后端只加实现类；
- 纯标准库实现（urllib），不新增第三方依赖；
- Ollama 不可用时抛 EmbedderError，由调用方决定是中断还是跳过。
"""

from __future__ import annotations

import json
import math
import struct
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Protocol, Sequence


class EmbedderError(Exception):
    """向量化失败：服务不可用、模型未拉取、返回格式异常等。"""


class Embedder(Protocol):
    """文本向量化接口：实现方只管把文本变成向量。"""

    @property
    def model_name(self) -> str:
        """模型标识，写入 question_embeddings.model，用于追溯向量来源。"""
        ...

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """批量向量化；空输入返回空列表；输出顺序与输入一致。"""
        ...


@dataclass
class OllamaEmbedder:
    """经本地 Ollama 的 /api/embeddings 取向量。

    用前请先在终端执行：ollama pull <model>（如 embeddinggemma）
    并确认 ollama serve 正在运行。

    prefix：任务指令前缀。EmbeddingGemma 2 这类模型用短前缀区分任务，
    不加也能跑但精度会打折。相似题检索用对称任务前缀，例如
    "task: sentence similarity | query: "（查询与入库用同一前缀）。
    """

    host: str = "http://localhost:11434"
    model: str = "embeddinggemma"
    timeout: float = 60.0
    prefix: str = ""

    # 相似题检索推荐的前缀（对称任务：查询与入库用同一个）
    SIMILARITY_PREFIX = "task: sentence similarity | query: "
    # 知识点标签建议的前缀（对称任务：题干与标签用同一个）
    CLASSIFICATION_PREFIX = "task: classification | query: "

    @property
    def model_name(self) -> str:
        return self.model

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        items = [str(t) for t in texts]
        if not items:
            return []
        vectors: list[list[float]] = []
        for text in items:
            vectors.append(self._embed_one(self.prefix + text))
        return vectors

    def _embed_one(self, text: str) -> list[float]:
        payload = json.dumps(
            {"model": self.model, "prompt": text}, ensure_ascii=False
        ).encode("utf-8")
        request = urllib.request.Request(
            f"{self.host}/api/embeddings",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.URLError as exc:
            raise EmbedderError(
                f"Ollama 不可用（{self.host}）：{exc}；"
                "请确认 ollama serve 正在运行。"
            ) from exc
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise EmbedderError(f"Ollama 返回了解析不了的内容：{exc}") from exc
        embedding = body.get("embedding")
        if not isinstance(embedding, list) or not embedding or not all(
            isinstance(x, (int, float)) for x in embedding
        ):
            raise EmbedderError(
                f"Ollama 没有返回合法向量（模型 {self.model!r} 是否已 pull？）。"
            )
        return [float(x) for x in embedding]


def cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    """余弦相似度；任一零向量返回 0.0；维度不一致抛 ValueError。"""
    if len(a) != len(b):
        raise ValueError(f"向量维度不一致：{len(a)} vs {len(b)}")
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def pack_vector(vector: Sequence[float]) -> bytes:
    """向量 → BLOB（float32 小端），供 question_embeddings.vector 存取。"""
    return struct.pack(f"<{len(vector)}f", *[float(x) for x in vector])


def unpack_vector(blob: bytes) -> list[float]:
    """BLOB → 向量；长度不是 4 的倍数时抛 ValueError。"""
    if len(blob) % 4 != 0:
        raise ValueError(f"向量 BLOB 长度异常：{len(blob)} 字节")
    return list(struct.unpack(f"<{len(blob) // 4}f", blob))


def rank_by_similarity(
    query: Sequence[float],
    candidates: Sequence[tuple[int, Sequence[float]]],
    top_k: int,
) -> list[tuple[int, float]]:
    """按与 query 的余弦相似度排序，返回 [(id, 相似度)] 前 top_k（降序）。

    top_k <= 0 时返回空列表；candidates 为空时返回空列表。
    """
    if top_k <= 0 or not candidates:
        return []
    scored = [
        (candidate_id, cosine_similarity(query, vector))
        for candidate_id, vector in candidates
    ]
    scored.sort(key=lambda item: item[1], reverse=True)
    return scored[:top_k]
