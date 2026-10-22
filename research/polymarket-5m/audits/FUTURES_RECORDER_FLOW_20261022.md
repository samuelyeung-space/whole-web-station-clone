# BTC5m：严格期货主动量录制格式接入

2026-10-22。仅开发解析与失效验证；没有新增收益回放、策略或排名。合格机制0/10，完整UTC录制日0，七天排名与正式OOS均null。

## 数据接入结果

新增recorder_futures_flow.py，复用historical_flow_receipts.ReceiptFlowTape及原精确整数/Decimal校验。未改旧现货桥接、mine500规则、执行器、任何Oct10/11或Oct12–19冻结。

已完整审计既有397622期货原始gzip（14,261,891字节；SHA256 004467a2762eb062784f0b0523ea42b628a78de8d3ec71d70eef9a018ec893c1）：

| 项目 | 行数 |
| --- | ---: |
| 总记录 | 699,234 |
| BTC期货trade | 699,232 |
| 格式及价格/数量可解析 | 697,115 |
| 非正价格/数量拒绝 | 2,117 |
| 其余生命周期记录，原档保留 | 2 |

全部trade行的分类对账；可解析数量与旧转换器697,115一致，仅为输入诊断，不能把删除异常后的行数当完整健康流。

首个拒绝在原档第306行，前面只有304条可进入特征引擎的trade。拒绝发生后新tape永久阻断，后续可解析行只作格式计数，不参与特征。失败前因缺独立因果覆盖，15/60秒特征也均null；失败后明确返回blocked_recorder_ingestion_error。不从成交密度、递增ID或连接数量制造覆盖证明，不把无特征填零。

## 录制身份与安全边界

读取录制时checkout 7bf9cf2a028beeb5af789fc36b6bdbbc069b7d31的paper_trader.py，blob 7b8b746c5a040ab6a97c2b4a68ca03680be624fc。on_binance_futures_trade在原生e不等于trade时返回，只在通过后写kind=trade；t/p/q/m/T/E直接保留，原e未写入。因此新桥接显式绑定此录制格式，不把缺e伪装为保留的原生包，也不接受aggTrade或现货路径。

精确ID、价格/数量Decimal、两个源时钟及本地接收时钟、原录制行均保留。m=true映射卖方主动、false映射买方主动；缺失保持未知。未知单位、浮点ID、非法m、身份冲突、接收时钟倒退、连接epoch改变均拒绝并锁定。期货独立使用binance_usdm命名空间；不是NativeFlowTape子类型，因此不能悄悄进入现有现货catalog join。未来期货规则采用必须单独登记，不把既有180流量变体算新策略。

[官方期货字段页](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/ws-streams/market)于2026-10-22读取，说明m为buyer-is-maker，聚合流为100ms；该页列出的aggTrade不等于本录制raw trade路由，本次没有找到raw路由的完整现行官方文档。raw路由的类型依据是固定录制代码；不借聚合文档声称逐笔源覆盖已获证实。

## 工程验证与可复现执行

10项新适配测试与19项既有receipt-flow测试共29项通过。测试覆盖精确大ID、Decimal冲突、重复、未知方向、未来输入/未来覆盖不改变过去、错误venue、epoch及失败锁定。第一次扩展测试因本地缺mine500_catalog报1项导入错误，恢复相同固定版本后29项通过，未改测试或规则以迎合结果。

解析审计先核对整个gzip SHA256，再按原接收顺序处理；输出包含全部分类、规范化可解析行摘要hash、首个原始异常、失败前后特征与null经济字段。原始数据不写入公共仓库。执行：

```sh
python3 -m unittest test_recorder_futures_flow test_historical_flow_receipts -v
python3 recorder_futures_flow.py /path/to/binance-futures-trade-strict.jsonl.gz --sha256 004467a2762eb062784f0b0523ea42b628a78de8d3ec71d70eef9a018ec893c1 --capture-run-id run39762268955:epoch1 --output /path/to/parser_audit.json
```

初版及epoch校验后的真实扫描均登记为工程尝试，非两轮策略回测。正常行规范化摘要仅是字节身份，不是可执行输入准入。既有7天/100份/独立1000美元账户标准不变；未产生新信号、发单、成交或PnL。

## 增量与接续

截至本次检查，source e5addf104a53254e25814c6bb53c80ddaa8bb098及研究3471ab51a3fa834542e20920a78e356485af7945无外部新增提交。forward39794955858与shadow39807135341仍in_progress，各无已发布artifact；已完成shadow39774266374也无artifact。全库artifact集合端点被工具拒绝，改用各已识别run的官方artifact接口，未把接口拒绝解释为数据不存在。

下一步需确认非正记录的源语义、提供可在决策时已知的独立覆盖，再决定新增期货规则身份及更晚未触碰留出。现货成交ID仍缺，不能从价格/时间制造。现有397622十二情景不重复跑；旧负结果与完整筛选分母保留。没有下单、部署、调生产、派发工作流或向他人发消息。
