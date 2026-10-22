# BTC5m：连续ID仍不能证明主动量完整

2026-10-22。对已消耗397622原始期货录制做新增的负面完整性检查，没有重跑既有12情景、筛选策略或修改冻结。新增候选/经济回放均0，合格机制0/10，七天排名/OOS仍null。

## 新发现

原始699,234行包含699,232条BTC期货trade、1条connection和1条disconnect。录制sequence逐行连续，trade ID从8195465807到8196165038全部逐一连续，本地接收时钟无倒退。但2,117条trade的p、q均为字面量0；其中m=true 1,288条，m=false 829条。连续ID没有补回这些行真实经济含义或数量，不能据此证明完整主动买卖量。

首尾录制接收区间为14,399,505.855毫秒（约4小时，非完整UTC日）。按预先固定的UTC整秒网格，共14,399个诊断时点，分别检查严格尾随(receive−horizon, receive]。只使用当时已接收的异常，后来的异常不能排除过去。

| 尾随窗口 | 含已观察异常的时点 | 未被本检查排除、仍未知 | 异常影响时间并集比例 |
| --- | ---: | ---: | ---: |
| 15秒 | 12,793 | 1,606 | 88.8543% |
| 60秒 | 14,390 | 9 | 99.9371% |

时点数与时间并集是两种摘要，不相加。异常仅约占trade行0.303%，但不能据行比例小就称流量基本完整；在当前拒绝非正经济字段的合同下，短窗影响很广。

这些是保守的拒绝依据，不是完整健康覆盖。表中未排除时点仍缺独立因果覆盖，feature_available=false、coverage_certified=false。7个新反例测试覆盖未来事件不改写过去、左开右闭边界、相同接收时点、区间交并/截断、非法时钟，以及“无异常不代表健康”。

## 可执行检查

新增futures_recording_integrity.py及test_futures_recording_integrity.py；复用精确整数校验，先核对输入SHA256，再按原始行序检查，禁止按源时间重排。保留异常接收时钟及少量原始见证，输出可恢复的负面时点索引。没有生成任何complete覆盖声明，没有把生命周期或递增ID当完整性的外部证据。

原始gzip SHA256保持004467a2762eb062784f0b0523ea42b628a78de8d3ec71d70eef9a018ec893c1，未改文件。源异常明细仅保存在私有状态工件，代码/报告保留Git身份。

```sh
python3 -m unittest test_futures_recording_integrity -v
python3 futures_recording_integrity.py /path/to/binance-futures-trade-strict.jsonl.gz --sha256 004467a2762eb062784f0b0523ea42b628a78de8d3ec71d70eef9a018ec893c1 --output /path/to/real_integrity_audit.json
```

本次注册只定义15/60秒诊断及1秒网格，不是策略规则或新OOS协议。先前recorder_futures_flow遇异常后的永久锁定不变，不能用本索引选择性恢复后续“干净”窗口，更不能依此筛出盈利市场。现货ID缺失也未被期货ID替代。

## 未解决的语义和数据入口

2026-10-22搜索并读取[官方USD-M更新日志](https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/change-log)及市场流说明，尚未找到将本raw trade的p=q=0解释为可忽略、零真实成交量的官方依据。没有把它推断为RPI、ADL、STP或心跳；具体源语义仍待证。即使将来取得解释，也须另行绑定源版本、规则身份与因果覆盖，不能改旧冻结后沿用原样本外标签。

本轮source e5addf104a53254e25814c6bb53c80ddaa8bb098和研究cc3e1692ddabdd5115b39e03bbe3d6563f33ce02无外部提交增量。已授权Actions接口再次确认forward39794955858和shadow39807135341仍in_progress、均无已发布artifact；未分配收益或零PnL。此前已完成原始解析不计为新策略尝试；探索计数和注册后完整性审计各自登记。

下一步优先取得零字段的源端解释或能独立核对的完整成交量源，同时接收真正新增闭日数据。完整七天2016个BTC5m时段、每策略/情景独立1000美元、目标100shares、全部压力成本与资金台账的准入要求不变。没有下单、部署、改生产参数、派发工作流或向他人发消息。
