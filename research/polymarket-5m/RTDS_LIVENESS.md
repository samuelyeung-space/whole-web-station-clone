# RTDS 活性监督修复：研究候选，尚未部署

本补丁只修采集层，未改变策略、下单、执行器、费用、生产配置或旧冻结协议。
源码基准是源策略分支 `f4e347f7a104c0cc0d18378fe60f230749e8a6b9`；
`paper_trader.py` 保持原字节，SHA256 为
`c7ddb4a29e4f8a62a4b0cba56c8ed4840e403db0c7ec5085b8d912768110d634`。
研究分支没有生产 wrapper / writer，因此只加入可独立测试的监督模块及 wrapper 补丁，
不把旧工作流或研究分支整体合入正在采集的分支。

## 定位与界限

旧 RTDS 接收循环没有行情无进展的超时；独立的 `_ping` 任务失败也不终止
接收循环。离线假连接可以复现“接收仍阻塞、ping 已失败、未重连”。
这解释了程序为何可能持续不恢复，不能确定外部订阅、对端或网络为何首次静默。
只使用已发布源码、录制元数据和人工构造帧，没有读取保护窗口的行情或结算来调策略。

## 修复契约

- 每个 RTDS 连接的接收、ping 和行情进展检查一起受监督；任一个失败即关闭并重订阅。
- 接收超时基于本地单调时钟，默认必需币种连续 30 秒无进展即失败；
  ping 每 5 秒发送，发送限时 10 秒；连接限时 10 秒、关闭限时 5 秒。
  重试等待为 2/4/8/16/30 秒并封顶，恢复必需币种后回到 2 秒。
- 默认 BTC 研究配置仅把 BTC 作为必需币种。实际必需集合是已录币种与交易币种交集，
  若交集为空则使用全部已录币种。其他币种分别报告可用性，不用安静币种打断 BTC。
- Chainlink 只接受匹配 topic 的 `coin/usd`，Binance RTDS 只接受 `coinusdt`；
  只计 `update` 且时间戳/价格有限、为正、时间戳严格推进的帧。
  pong、订阅确认、历史快照、重复及倒退时间戳不能刷新期限。
  价格相同但时间戳推进仍算进展。时间戳高水位跨重连保留。
- 原始处理函数仍接收每个原始帧；本模块不删原始帧、不重写接收时间，
  原函数对控制帧/格式异常的行为保留。停止时取消并等待所有子任务，再关闭录制器。
- 心跳与最终运行元数据记录分流、分币种的本地进展时间、epoch、可用性；
  同时保存监督模块源码哈希。运行结束后的状态明确 inactive。

这里的活性只表示“本地收到了源时间戳继续推进的更新”。它不验证绝对源时钟偏差，
不能识别持续送来的旧历史流，异常未来时间戳也可能抬高保留的高水位。
这些情况保留原始记录并需要另外的输入契约；不擅自用源时钟替换接收时钟。
30 秒不是撮合报价时效，不证明全日、逐场、逐token或队列完整性，也不能用作未来健康筛选。
新采集运行时不可自动继承旧冻结的样本外资格。

## 离线复现

研究分支中独立运行：

```sh
cd research/polymarket-5m
python -m unittest test_rtds_liveness -v
```

`rtds_wrapper.patch` 针对上述源策略提交的 wrapper；在隔离源码副本中先检查再应用，
并把本目录 `rtds_liveness.py` 与 `test_rtds_liveness.py` 复制到相同路径。
补丁同时加入 wrapper 的离线接入测试。
这些命令仅用于隔离验证，不是部署命令：

```sh
git apply --check /path/to/rtds_wrapper.patch
git apply /path/to/rtds_wrapper.patch
cd research/polymarket-5m
python -m unittest test_rtds_liveness test_rtds_wrapper_contract test_capture_reliability test_capture_workflow -v
```

假连接覆盖只有 pong 的静默、发送失败/阻塞、正常关闭与同时超时、取消清理、
重连后重订阅、跨 epoch 重复帧、两个流相互独立及原始回调保留。
工程测试通过不等于真实恢复验收、连续 7 日数据或策略盈利。
隔离副本已执行 57 项检查：20 项监督模块检查、1 项真实父类路径接入检查
（包含两个币种配置），以及原有 36 项写入、封存与工作流回归，全部通过。

## 官方参考（2026-10-18 核验）

- [2026-09-15 官方更新日志](https://docs.polymarket.com/changelog/predictions)：参考价 RTDS 已为 legacy。
- [RTDS 迁移文档](https://docs.polymarket.com/migrate/rtds-to-polybolt)：新通道需要 CLOB API 认证。

本次不迁移认证通道、不探查凭据。真实运行恢复、接入永久归档、逐场准入和统一七日榜仍须分别验收。
