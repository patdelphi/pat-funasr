"""
程序说明：
验证说话人聚类后端在未安装 scikit-learn 的便携运行时中仍可工作。
"""

from __future__ import annotations

import numpy as np
import torch


def test_cluster_backend_without_sklearn_can_cluster(monkeypatch):
    """缺少 sklearn 时应使用 NumPy 后备实现，而不是留下未定义名称。"""
    from funasr.models.campplus import cluster_backend

    monkeypatch.setattr(cluster_backend, "sklearn", None)
    monkeypatch.setattr(cluster_backend, "sklearn_k_means", None)
    embeddings = torch.tensor(
        [[1.0, 0.0]] * 10 + [[0.0, 1.0]] * 10,
        dtype=torch.float32,
    )

    labels = cluster_backend.ClusterBackend()(embeddings, oracle_num=2)

    assert labels.shape == (20,)
    assert len(set(labels.tolist())) == 2


def test_short_embedding_sequence_respects_oracle_speaker_count():
    """短音频仍必须遵守用户明确指定的说话人数。"""
    from funasr.models.campplus import cluster_backend

    embeddings = torch.tensor(
        [[1.0, 0.0]] * 5 + [[0.0, 1.0]] * 5,
        dtype=torch.float32,
    )

    labels = cluster_backend.ClusterBackend()(embeddings, oracle_num=2)

    assert labels.shape == (10,)
    assert len(set(labels.tolist())) == 2
