# BTC 5m 回放器：可运行交付

本次是在现有回放后端上补齐旧录制的一键入口，不重写成交内核，不修改旧冻结策略，不联网交易。

## 立即运行

在 `research/polymarket-5m` 目录，使用 Python 3.11 或更新版本：

```sh
python3 replay_cli.py doctor
python3 replay_cli.py demo
```

Demo 是合成验收数据，不是历史收益；再次执行复用已验证结果，不产生新回测。

## 真实旧录制：一条命令从 ZIP 到逐笔报告

旧 ZIP 的 CSV/JSONL 适配需要 NumPy，不再因为不相关的 pyarrow 依赖而启动失败。真正读取 Parquet 才需要 pyarrow。完整研究依赖见 `requirements-replay.txt`。

以下日期只用于已经用于开发的旧历史录制：录制观察区间必须早于 2026-10-10 00:00 UTC；所有结算现金统一延后至 2026-10-16 00:00 UTC。这是显式保守资金假设，不是观测到的到账时间，不可据此认证历史实盘收益。

```sh
python3 replay_legacy_pipeline.py \
  --input /path/to/recordings \
  --cache-root .replay-cache \
  --development-end-before 2026-10-10T00:00:00Z \
  --settlement-cutoff-ms 1792108800000 \
  --accept-legacy-assumptions \
  --strategy mine500_catalog:M500-439 \
  --strategy mine500_catalog:M500-669 \
  --strategy mine500_catalog:M500-677 \
  --mode shared --cash 1000 --shares 5 \
  --latencies-ms 500,600,1000 \
  --out replay-runs
```

`--input` 可以是多个 ZIP，也可以是目录。首次转换按原文件与代码的内容哈希缓存；再次运行先验证缓存与完整压缩流，不能用损坏文件蒙混过关。新录制进入新缓存，旧录制不重复解压。兼容的非重叠录制合并为一个时间序列，共享本金不按文件重置。缺失文件、非法日期范围、不兼容或重叠录制会报错，不静默丢弃。

这个入口明确用于旧 Up-L1 历史敏感性模拟：缺少 Down 盘口就拒绝 Down 订单，不用 1-Up 伪造盘口；费用、最小订单、tick 的历史已知时点不足会保留限制。`--accept-legacy-assumptions` 不能省略，防止把这条路径误当 strict 证据。

标准 strict 录制和已归一化事件继续使用 `replay_cli.py run --input ...`；JPR/MAR 的冻结特征和模型分数适配继续使用原有研究入口。缺失特征不会被自动补造。

## 换策略，无需重做录制转换

新模块提供 `catalog()` 和 `signal(candidate, features)`；通过 `--plugin-dir` 和 `--strategy module:ID` 传入。也可用首次输出的 `dataset_cache_path` 直接执行 `replay_cli.py run --cache ...`。独立策略用 `--mode isolated`；共享账户用 `--mode shared`，策略顺序就是预先固定的竞争优先级。不同延迟和压力场景不能相加。

输出保留 `coverage.json`、`metrics.json`、逐笔 `attempts.jsonl.gz`、`markets.csv`、`daily.csv`、`cash_ledger.csv` 和中文报告。无信号、缺特征、零成交、部分成交、过期盘口和缺失执行输入均保留。

## 验收

```sh
python3 replay_cli.py verify
python3 -m unittest test_legacy_recording_cache test_legacy_up_l1_adapter -v
```

新增 `test_replay_legacy_pipeline.py` 的八项测试覆盖一键执行、校验后复用、损坏缓存、目录与缺文件、时间边界和缺少 Arrow 时的真实适配路径；测试数据全部为合成数据。

## 边界

可运行回放器已经与历史数据覆盖完整性分开。此次修复和入口不等于九月、十月全量历史已经全部导入，也不等于完成连续七天或二十天回测。本后端支持单腿主动买入、持有到结算和共享现金开发仿真；尚未验证 maker 排队、动态对冲、卖空或加仓。代码不保证策略盈利，显示盘口容量也不等于实际成交。
