# EPM-matched_market-02

市场概率与剩余时间的条件校准策略。状态：研究候选；开发样本已回放，独立样本外与实际成交优势尚未确认。

## 编号与代码

- 策略编号：`EPM-matched_market-02`。
- `matched_market` 是模型规格；`02` 表示每份至少 0.02 美元的预测净优势门槛。
- 100 份是执行数量，不产生另一个策略编号。
- 模型与候选登记：[mine500_endpoint_model.py](../mine500_endpoint_model.py)。
- 100 份开发回放：[mine500_fixed100.py](../mine500_fixed100.py)。
- 执行引擎：[mine500_replay_v2.py](../mine500_replay_v2.py)。
- 独立账本核验：[mine500_fixed100_audit.py](../mine500_fixed100_audit.py)。
- 冻结模型后续评估：[mine500_fixed100_holdout.py](../mine500_fixed100_holdout.py)。

## 信号

模型在市场隐含概率上预测一个修正量，得到 Up 概率 `p`。拟合输入来自 `mid_up` 与 `tau_s` 的固定变换，包括概率的 logit、剩余时间分段及交互项。采用每棵树最多 8 叶的梯度提升残差模型。

为了与外部信息模型作同样本比较，仍要求 `ep_r_5s`、`ep_r_15s`、`ep_r_30s`、`ep_r_60s` 有效且在决策时已可用。这四项限定输入支持范围，并非本模型使用的预测变量；不能删除这一条件后沿用原结果。

设 Up/Down 决策卖一分别为 `a_up`、`a_down`，当时市场费率系数为 `r`：

```text
edge_up   = p     - a_up   - r * a_up   * (1 - a_up)
edge_down = 1 - p - a_down - r * a_down * (1 - a_down)
```

最大值至少为 `0.02` 时选择优势更大的一边；相等时选 Up。所有模型与特征的可用时间都必须不晚于本地决策时间。每场只采用首次有效信号，不因后来未成交而重试该场。

## 固定 100 份执行

- 初始现金 1,000 美元，每单请求 100 份；现金不足以预留整笔订单时拒单，不借款或自动缩量。
- 本地决策至模拟撮合的总延迟分别为 500、600、1000ms；总延迟包含场内等待，不再次叠加。
- 决策卖一固定为限价；延迟后的历史盘口决定全部、部分或零成交；保留所有失败尝试。
- 计入费用与跨场、跨日资金占用，持有至结算。基准在收到官方结果后按记录中的时间释放模拟资金；没有额外 24 小时锁资。真实赎回到账仍需单独验证。
- 三个延迟情景使用三个替代账户，不能相加。盘口深度表示容量上界，不保证真实 FAK 成交。

## 复现已完成开发回放

代码已在研究分支 `codex/btc5m-mine500-20261010`。固定 100 份入口最初提交为 `1b859337666b78e8170b10563ef24c5f15cc424e`。

使用获授权的研究结果包，解压后找到 `endpoint_research_20261011` 与 `fixed100_20261011`。从 `research/polymarket-5m` 执行，替换以下路径：

```bash
python mine500_restore_fixed100_inputs.py --source-root /absolute/endpoint_research_20261011 --development /absolute/fixed100_20261011/development
python mine500_fixed100.py --source-root /absolute/endpoint_research_20261011 --out /absolute/new_capacity_result
python mine500_fixed100_audit.py --source-root /absolute/endpoint_research_20261011 --development /absolute/new_capacity_result
```

恢复器验证输入哈希并重接已保存分数；100 份回放不会重新训练。输出目录必须尚不存在。程序同时登记并执行基准、额外每份 1 美分成本、半数可用盘口深度三种设定，各含三个延迟档，共九种替代情景；它们不是九个独立策略。

原始录制、逐笔台账、拟合权重、模型分数和结果包保留在获授权的研究工件中，不随本策略说明公开发布。仅有仓库代码不足以复现私有数据上的数值结果。

## 后续留出评估

已有协议 `EPM-fixed100-20261012-20261019-v1` 使用原先保存的 Oct9 权重，窗口为 `2026-10-12T00:00:00Z` 至 `2026-10-19T00:00:00Z`。不要重新运行 freeze 来覆盖旧协议，也不要将开发回放的逐日模型更新混同于这份冻结权重。

```bash
python mine500_fixed100_holdout.py run --protocol /absolute/holdout/protocol.json --input /absolute/new_closed_window.jsonl.gz --out /absolute/fresh_holdout_result
```

必须使用原协议、完整且更晚的未触碰输入，并检查输出的 admission 状态；命令成功写出文件不等于通过输入门槛，更不等于策略盈利。缺数据时收益保持 `null`。

本策略与 `historical100_runner.py` 中的历史 MIX R1/R2/R3 是不同实验。MIX 多月任务的结果不能作为本策略的 OOS 结果。
