# BTC 5m 回放器：直接运行

本交付复用当前分支已存在的 `replay_lab.py`、`replay_engine.py`、缓存、策略加载器与报告模块；新增 `replay_cli.py` 易用入口和 8 项入口测试。没有重写成交内核，也没有修改旧的冻结策略。

## 1. 立即验证

需要 Python 3.11 或更新版本。在本文件所在目录运行：

```sh
python3 replay_cli.py doctor
python3 replay_cli.py demo
```

`doctor` 检查导入和 Python 版本。`demo` 自动生成明确标注的合成测试数据、运行回放并核对手算结果，无需登录、API key 或行情订阅。

再次执行 `demo` 应返回 `cache_hit: true`、`new_backtests: 0`。输出在 `demo-output/acceptance.json` 和 `demo-output/runs/<hash>/`。

合成数据包括六类市场：全成、部分成交、零成交、过期盘口拒单、无信号、缺特征。三个延迟分别是 500、600、1000 毫秒；每个场景有 4 次尝试。市场不是成交笔数，三个延迟是不能加总的替代场景。这里所有金额仅用于程序验算，不是历史收益或交易建议。

## 2. 放入真实本地录制

对于符合现有 strict 录制格式的 ZIP/TGZ，或已经归一化的 JSONL/JSONL.GZ：

```sh
python3 replay_cli.py run --input /你的路径/recording.zip --strategy mine500_catalog:M500-439
```

默认缓存保存在当前工作目录 `.replay-cache`，报告在 `replay-runs`。也可指定 `--cache-root` 和 `--out`。多份兼容且不重叠的录制可一起传给 `--input`。不兼容的老 ZIP 不会被伪造为 strict 数据；旧 Up-L1、relay 数据的专用适配流程见 `REPLAY_LAB.md`。

加入自己的纯特征策略：

```sh
python3 replay_cli.py run --input /你的路径/events.jsonl.gz --plugin-dir /你的路径/策略目录 --strategy my_rules:ONE
```

`my_rules.py` 的最小接口：

```python
def catalog():
    return [{"id": "ONE", "required_features": ["r_5s"]}]

def signal(candidate, features):
    return "Up" if features["r_5s"] > 1 else None
```

示例规则仅展示接口。输入必须实际具有所需特征及其可用时间，不能用未来信息补值。信号返回 `Up`、`Down` 或 `None`。接口和依赖限制见 `REPLAY_LAB.md`。

## 3. 重复实验不重新解压录制

首次运行输出中会给出数据/结果路径。也可先只导入：

```sh
python3 replay_cli.py import --input /你的路径/events.jsonl.gz
python3 replay_cli.py inspect --cache .replay-cache/datasets/实际哈希
python3 replay_cli.py run --cache .replay-cache/datasets/实际哈希 --strategy mine500_catalog:M500-439 --shares 5,10,20 --stresses base,half_depth,plus_1cent
```

源数据、规则、模块内容和成本参数不变时复用已验证结果；变化则生成新实验身份。损坏缓存会报错，不会静默重算覆盖证据。

共享本金用 `--mode shared --cash 1000`，并预先固定策略列表顺序。不同延迟、下单规模和成本情景不能当成同一个账户加总。多日连续账本、旧录制适配与 JPR/MAR 已冻结特征/分数接入均保留现有入口，见 `REPLAY_LAB.md` 和 `FROZEN_JPR_MAR.md`。`replay_cli.py batch` 转交给现有多日批处理器。

## 4. 报告

每次完成输出 `registration.json`、`completion.json`、`coverage.json`、`metrics.json`；每个场景另有 `metrics.csv`、`markets.csv`、`daily.csv`、`cash_ledger.csv`、`attempts.jsonl.gz`、`audit.json` 和中文 `report.md`。

覆盖统计和交易统计分开；未触发、缺特征、拒单、部分成交、未结算都不删除。`--require-full-days 7` 是完整日期数据存在性门槛，不是自动补齐七天，更不证明数据源连续健康。

## 5. 跑完整测试

基础 demo 不需要第三方库。完整测试含 NumPy、scikit-learn 等旧特征一致性测试，可在虚拟环境中运行：

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-replay.txt
.venv/bin/python replay_cli.py verify
```

本次新增 8 项入口验收测试；完整验收数量和通过记录见 GitHub Actions 的 `offline-replay-source-and-tests` 工件。独立验收执行当前分支全部 `test_replay_*.py`、6 项旧录制缓存测试，并检查 demo 首次与再次运行。

## 交付边界

这是离线开发研究回放器，不联网下单、不部署实盘、不自动训练模型。支持买入单腿 Up/Down、主动成交 FAK、持有到官方结算；不能冒充已验证的 maker 排队、动态对冲、卖空或加仓引擎。成交仍是显示深度的模拟，不是实盘成交证明。

代码包不包含用户九月、十月的真实全量录制，也没有宣称本次完成完整七天、二十天或两个月的历史回测。引擎和入口验收，与历史数据覆盖完整性，是两项独立交付。
