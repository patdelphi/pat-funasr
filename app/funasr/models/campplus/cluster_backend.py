#!/usr/bin/env python3
# -*- encoding: utf-8 -*-
# Copyright FunASR (https://github.com/alibaba-damo-academy/FunASR). All Rights Reserved.
#  MIT License  (https://opensource.org/licenses/MIT)
# Modified from 3D-Speaker (https://github.com/alibaba-damo-academy/3D-Speaker)

"""
程序说明：
提供 CAM++ 说话人嵌入聚类；scikit-learn 不可用时使用 NumPy 后备实现，
确保便携运行时仍能完成基础说话人分离。
"""

import scipy
import torch
import numpy as np

try:
    import sklearn
    from sklearn.cluster._kmeans import k_means as sklearn_k_means
    from sklearn.cluster import HDBSCAN as sklearn_hdbscan
except ImportError:
    sklearn = None
    sklearn_k_means = None
    sklearn_hdbscan = None


def _as_numpy(value):
    """将 CPU Tensor 或数组转换为 NumPy 数组。"""
    if isinstance(value, torch.Tensor):
        return value.detach().cpu().numpy()
    return np.asarray(value)


def _cosine_similarity(X):
    """不依赖 sklearn 的余弦相似度矩阵。"""
    values = _as_numpy(X).astype(np.float64, copy=False)
    norms = np.linalg.norm(values, axis=1, keepdims=True)
    normalized = values / np.maximum(norms, np.finfo(np.float64).eps)
    return np.matmul(normalized, normalized.T)


def _numpy_k_means(X, cluster_count, max_iterations=100):
    """确定性 NumPy K-Means，仅作为缺少 sklearn 时的聚类后备。"""
    values = _as_numpy(X).astype(np.float64, copy=False)
    sample_count = values.shape[0]
    cluster_count = max(1, min(int(cluster_count), sample_count))

    # 首个中心取范数最大的样本，其余中心使用最远点初始化，避免随机结果。
    first_index = int(np.argmax(np.linalg.norm(values, axis=1)))
    center_indexes = [first_index]
    min_distances = np.sum((values - values[first_index]) ** 2, axis=1)
    while len(center_indexes) < cluster_count:
        next_index = int(np.argmax(min_distances))
        center_indexes.append(next_index)
        candidate_distances = np.sum((values - values[next_index]) ** 2, axis=1)
        min_distances = np.minimum(min_distances, candidate_distances)

    centers = values[center_indexes].copy()
    labels = np.zeros(sample_count, dtype=np.int64)
    for _ in range(max_iterations):
        distances = np.sum((values[:, None, :] - centers[None, :, :]) ** 2, axis=2)
        next_labels = np.argmin(distances, axis=1)
        if np.array_equal(next_labels, labels):
            labels = next_labels
            break
        labels = next_labels
        for cluster_index in range(cluster_count):
            members = values[labels == cluster_index]
            if len(members):
                centers[cluster_index] = members.mean(axis=0)
    return labels


class SpectralCluster:
    r"""A spectral clustering mehtod using unnormalized Laplacian of affinity matrix.
    This implementation is adapted from https://github.com/speechbrain/speechbrain.
    """

    def __init__(self, min_num_spks=1, max_num_spks=15, pval=0.022):
        """Initialize SpectralCluster.
        
            Args:
                min_num_spks: TODO.
                max_num_spks: TODO.
                pval: TODO.
            """
        self.min_num_spks = min_num_spks
        self.max_num_spks = max_num_spks
        self.pval = pval

    def __call__(self, X, oracle_num=None):
        # Similarity matrix computation
        """Internal: call  .
        
            Args:
                X: TODO.
                oracle_num: TODO.
            """
        sim_mat = self.get_sim_mat(X)

        # Refining similarity matrix with pval
        prunned_sim_mat = self.p_pruning(sim_mat)

        # Symmetrization
        sym_prund_sim_mat = 0.5 * (prunned_sim_mat + prunned_sim_mat.T)

        # Laplacian calculation
        laplacian = self.get_laplacian(sym_prund_sim_mat)

        # Get Spectral Embeddings
        emb, num_of_spk = self.get_spec_embs(laplacian, oracle_num)

        # Perform clustering
        labels = self.cluster_embs(emb, num_of_spk)

        return labels

    def get_sim_mat(self, X):
        # Cosine similarities
        """Get sim mat.
        
            Args:
                X: TODO.
            """
        if sklearn is not None:
            M = sklearn.metrics.pairwise.cosine_similarity(X, X)
        else:
            M = _cosine_similarity(X)
        return M

    def p_pruning(self, A):
        """P pruning.
        
            Args:
                A: TODO.
            """
        if A.shape[0] * self.pval < 6:
            pval = 6.0 / A.shape[0]
        else:
            pval = self.pval

        n_elems = int((1 - pval) * A.shape[0])

        # For each row in a affinity matrix
        for i in range(A.shape[0]):
            low_indexes = np.argsort(A[i, :])
            low_indexes = low_indexes[0:n_elems]

            # Replace smaller similarity values by 0s
            A[i, low_indexes] = 0
        return A

    def get_laplacian(self, M):
        """Get laplacian.
        
            Args:
                M: TODO.
            """
        M[np.diag_indices(M.shape[0])] = 0
        D = np.sum(np.abs(M), axis=1)
        D = np.diag(D)
        L = D - M
        return L

    def get_spec_embs(self, L, k_oracle=None):
        """Get spec embs.
        
            Args:
                L: TODO.
                k_oracle: TODO.
            """
        lambdas, eig_vecs = scipy.linalg.eigh(L)

        if k_oracle is not None:
            num_of_spk = k_oracle
        else:
            lambda_gap_list = self.getEigenGaps(
                lambdas[self.min_num_spks - 1 : self.max_num_spks + 1]
            )
            num_of_spk = np.argmax(lambda_gap_list) + self.min_num_spks

        emb = eig_vecs[:, :num_of_spk]
        return emb, num_of_spk

    def cluster_embs(self, emb, k):
        """Cluster embs.
        
            Args:
                emb: TODO.
                k: TODO.
            """
        if sklearn_k_means is not None:
            _, labels, _ = sklearn_k_means(emb, k)
            return labels
        return _numpy_k_means(emb, k)

    def getEigenGaps(self, eig_vals):
        """Geteigengaps.
        
            Args:
                eig_vals: TODO.
            """
        eig_vals_gap_list = []
        for i in range(len(eig_vals) - 1):
            gap = float(eig_vals[i + 1]) - float(eig_vals[i])
            eig_vals_gap_list.append(gap)
        return eig_vals_gap_list


class UmapHdbscan:
    r"""
    Reference:
    - Siqi Zheng, Hongbin Suo. Reformulating Speaker Diarization as Community Detection With
      Emphasis On Topological Structure. ICASSP2022
    """

    def __init__(
        self, n_neighbors=20, n_components=60, min_samples=10, min_cluster_size=10, metric="cosine"
    ):
        """Initialize UmapHdbscan.
        
            Args:
                n_neighbors: TODO.
                n_components: TODO.
                min_samples: TODO.
                min_cluster_size: Size/dimension parameter.
                metric: TODO.
            """
        self.n_neighbors = n_neighbors
        self.n_components = n_components
        self.min_samples = min_samples
        self.min_cluster_size = min_cluster_size
        self.metric = metric

    def __call__(self, X):
        """Internal: call  .
        
            Args:
                X: TODO.
            """
        import umap.umap_ as umap

        umap_X = umap.UMAP(
            n_neighbors=self.n_neighbors,
            min_dist=0.0,
            n_components=min(self.n_components, X.shape[0] - 2),
            metric=self.metric,
        ).fit_transform(X)
        if sklearn_hdbscan is None:
            raise RuntimeError("HDBSCAN requires scikit-learn")
        labels = sklearn_hdbscan(
            min_samples=self.min_samples,
            min_cluster_size=self.min_cluster_size,
            allow_single_cluster=True,
        ).fit_predict(umap_X)
        return labels


class ClusterBackend(torch.nn.Module):
    r"""Perfom clustering for input embeddings and output the labels.
    Args:
        model_dir: A model dir.
        model_config: The model config.
    """

    def __init__(self, merge_thr=0.78):
        """Initialize ClusterBackend.
        
            Args:
                merge_thr: TODO.
            """
        super().__init__()
        self.model_config = {"merge_thr": merge_thr}
        # self.other_config = kwargs

        self.spectral_cluster = SpectralCluster()
        self.umap_hdbscan_cluster = UmapHdbscan()

    def forward(self, X, **params):
        # clustering and return the labels
        """Forward pass for training.
        
            Args:
                X: TODO.
                **params: Additional keyword arguments.
            """
        k = params["oracle_num"] if "oracle_num" in params else None
        assert len(X.shape) == 2, "modelscope error: the shape of input should be [N, C]"
        if X.shape[0] < 20:
            if k is not None:
                # 短音频样本不足以稳定估算人数，但用户明确指定人数时仍应遵守。
                return _numpy_k_means(X, k)
            return np.zeros(X.shape[0], dtype="int")
        if X.shape[0] < 2048 or k is not None:
            # unexpected corner case
            labels = self.spectral_cluster(X, k)
        elif sklearn_hdbscan is not None:
            labels = self.umap_hdbscan_cluster(X)
        else:
            # 便携运行时未安装 sklearn 时继续使用谱聚类，避免运行期 NameError。
            labels = self.spectral_cluster(X, k)

        if k is None and "merge_thr" in self.model_config:
            labels = self.merge_by_cos(labels, X, self.model_config["merge_thr"])

        return labels

    def merge_by_cos(self, labels, embs, cos_thr):
        # merge the similar speakers by cosine similarity
        """Merge by cos.
        
            Args:
                labels: TODO.
                embs: TODO.
                cos_thr: TODO.
            """
        assert cos_thr > 0 and cos_thr <= 1
        while True:
            spk_num = labels.max() + 1
            if spk_num == 1:
                break
            spk_center = []
            for i in range(spk_num):
                spk_emb = embs[labels == i].mean(0)
                spk_center.append(spk_emb)
            assert len(spk_center) > 0
            spk_center = np.stack(spk_center, axis=0)
            norm_spk_center = spk_center / np.linalg.norm(spk_center, axis=1, keepdims=True)
            affinity = np.matmul(norm_spk_center, norm_spk_center.T)
            affinity = np.triu(affinity, 1)
            spks = np.unravel_index(np.argmax(affinity), affinity.shape)
            if affinity[spks] < cos_thr:
                break
            for i in range(len(labels)):
                if labels[i] == spks[1]:
                    labels[i] = spks[0]
                elif labels[i] > spks[1]:
                    labels[i] -= 1
        return labels
