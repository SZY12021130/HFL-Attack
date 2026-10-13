# baseline/：明文鲁棒聚合基线

SFLBR 论文对比表中的**明文鲁棒聚合基线**集合，供"明文 FedAvg + 鲁棒防御"实验使用。
每个算法一个子包，统一接口：

```python
def aggregate(updates: List[Dict[str, torch.Tensor]],
              weights: Optional[List[float]] = None,
              **params) -> Dict[str, torch.Tensor]:
```

服务器明文聚合路径（`AggregationServer._apply_defense`）按 `defense.aggregation_rule`
从 `AGGREGATION_REGISTRY` 查找规则（未知规则回退 fedavg 并告警）。

## 总览表

| 方案 | 注册名 | 出处 | 威胁模型前提 | SFLBR 对比表行 |
|------|--------|------|--------------|----------------|
| FedAvg | `fedavg` | McMahan et al. AISTATS'17 | 无（无防御基线，所有客户端须诚实） | Plain FedAvg |
| Krum | `krum` | Blanchard et al. NDSS'17 | n > 2f+2 | Krum |
| Multi-Krum | `multi_krum` | Blanchard et al. NDSS'17（变体） | n > 2f+2 | Multi-Krum |
| 坐标中值 | `median` | Chen et al. POMACS'17 | 无严格上限（建议恶意 < 1/2） | Median |
| 截断均值 | `trimmed_mean` | Chen et al. POMACS'17 | **恶意比例 < trim_ratio** | T-mean |
| 几何中位数 | `geometric_median` | Weiszfeld（POMACS'17 对比） | 同 median | GeoMed |
| Centered Clipping | `centered_clipping` | Karimireddy et al. ICML'21 | 无严格上限，抗 ALIE/IPM 合谋 | CC |
| DnC | `dnc` | Shejwalkar & Houmansadr USENIX Sec'21（Su & Xu 2019 筛选） | 良性更新成紧致簇 | DnC |
| DP-BREM | `dp_brem` | El Mhamdi et al. ICML'18 | 有状态（服务器动量 + DP 噪声） | DP-BREM |
| RFLBA | `rflba` | Cao et al. NDSS'21（FLTrust 思路） | 恶意方向与良性方向可分 | RFLBA |

各算法的原理、关键公式与参数表见子包 README.md。

## 使用示例（明文模式）

`configs/config.yaml`：

```yaml
# 关闭加密 → 标准明文 FedAvg 路径（客户端上传明文更新）
encryption:
  enabled: false

# 明文模式下从 baseline/ 注册表选择鲁棒聚合规则
defense:
  aggregation_rule: "krum"      # fedavg/median/trimmed_mean/krum/multi_krum/
                                # geometric_median/centered_clipping/dnc/dp_brem/rflba
  anomaly_filter: null          # 可选: norm_clip/cosine_similarity/mad/multi_criteria
  params:                       # 规则超参数（覆盖注册表默认值）
    f: 2

# 攻击照旧在客户端明文域生效（加密前篡改 → 明文模式天然可用）
attack:
  enabled: true
  name: "model:sign_flipping"
  malicious_count: 2
```

运行：`python main.py`（或 `python main.py --config your.yaml`）。

⚠️ 明文轮要求 `ppbr.enabled: false` 且 `threshold.enabled: false`，冲突时启动拒绝并报错。

## 与加密路径的关系

- 密文路径（`encryption.enabled: true`，默认）：行为完全不变，CKKS 密文聚合，鲁棒规则
  需要明文坐标值**不生效**（密钥分离架构的固有约束，见根 README"已知限制"）。
- 明文路径（`encryption.enabled: false`）：跳过 CKKS 初始化与全部加密 API，
  客户端明文上传 → `server.aggregate_plain_updates()` 按注册表规则聚合 → 明文评估。
  密文类指标（encryption_time / expansion_ratio 等）记 0，report 自动走明文模板。
- `dp_brem` 是**有状态**规则（跨轮动量）：服务器按规则名维护 `state`，
  与纯规则型基线的差异见其 `README.md`。

## 向后兼容

实现已从 `ckks_fed/defenses/aggregation_rules.py` 迁移至此包；旧模块仅 re-export
`baseline/` 的实现（`from ckks_fed.defenses import median_aggregate` 等用法不变）。
