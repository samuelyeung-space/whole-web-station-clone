# 双路原始消息保留入口（2026-10-23）

新增研究模块futures_route_frames.py，在现有“按成交ID保留首份”处理之前，把每一份输入的原始字节、SHA256、路由/端点映射、本地接收时刻、连接编号、连接epoch和该连接序号交给调用方的追加写入器。模块不连接网络，不修改录制器，不改变现有去重、特征、交易规则或冻结协议。

## 具体缺口

源分支f49c82fb138c7445423c0b8120b822091db53c94的paper_trader.py Git blob仍为7b8b746c5a040ab6a97c2b4a68ca03680be624fc。on_binance_futures_trade先按(symbol,trade_id)调用_first_binance_frame，再写入字段子集。后续副本及未选择字段无法从旧严格录制文件重建。

原生399701中已审计到的1,728条零价零量记录保持原判定，不重新筛选数据。它们究竟是源流语义、路由差异还是其他问题尚未确定。当前没有双路去重前原文，因此不能证明真实副本发生冲突，不能填补真实数量，也不能将零值当零主动量放行。

## 行为与边界

- 直接和combined两种原文均逐字节保留，包含未知字段、无效JSON/UTF-8、重复JSON键、控制帧、aggregate帧、缺m或非法p/q；不会把它们编造成原生有效成交。
- 同ID后续消息的比较只在后续本地接收时刻可知。原文不同分为元数据变化或未解决冲突；不会回写先前帧或先前决策。
- 不同长度的精确十进制用无舍入签名，避免Decimal默认精度把不同经济内容合并。
- 写入器失败时停止向旧回调转交并锁定入口；旧回调失败时已写入原文仍留存。返回给调用方的数据修改不影响内部首份记录。
- 路由、端点与时钟映射必须由实际录制配置绑定。调用方写入器的返回并不证明物理持久化完成；仍需现有缓冲录制、闭档哈希、归档与发布流程。
- 没有新增覆盖认证、健康区间、方向/流量特征或交易适配。旧RecorderFuturesFlowTape对坏消息的锁定不变；不能用后到副本修复过去信号。原文流尚未实际接入或部署，下一份采用新采集代码的数据必须单独登记输入及代码哈希，不继承旧冻结。

## 验证

8项合成测试通过：零值先到/有效后到；combined及未知字段；无效原文；写入失败锁定；旧回调失败后保留；路由/序号/时钟；超长小数；返回值不可改写内部首份证据。

另用check_futures_route_preservation.py从上述已核验Git blob中只提取三个纯处理方法，在离线合成反例中运行。两份同ID消息依次到达，首份p=q=0、第二份为正：旧处理器只写1行且保留零值，新入口写2份原文并在第二份到达时标为未解决冲突。未导入或运行录制器，无socket、账户或实际订单。此为工程反例，不是实际观察到的路由冲突或盈利回测。

复现（在research/polymarket-5m中运行，pinned-paper-trader.py必须是上述精确blob）：

```sh
python3 -m unittest test_futures_route_frames -v
python3 audits/check_futures_route_preservation.py pinned-paper-trader.py --output synthetic-route-check.json
```

## 哈希与后续

| 文件 | SHA256 |
|---|---|
| futures_route_frames.py | 7d8512344bb4e73d6e83957758c229c5697f2e4e3a577dffce024e1c97352ed1 |
| test_futures_route_frames.py | 84d3becdc5f4ece1c5339a639fe86cc493187dc849cc4169cc896f7dc2a22f15 |
| audits/check_futures_route_preservation.py | fb21aa593194b24f77fa9018b98a6c39626282c8d3bb67768a4e1247d559761c |

本轮源/研究起点f49c82f/decc5ec；399701转换分析作业仍在执行，严格转换工件尚未发布，未重复下载原生分片或旧网格。新候选0、新金融回放0、实际成交0、完整UTC录制日0，七日100份排名和正式OOS均pending/null，合格独立机制0。现有500ms历史描述排序未改。

后续需要真实去重前双路原文、明确路由及本地时钟、完整连接生命周期与可核验闭档覆盖；在此之前继续保持未知。该入口只准备未来可复现采集，不能制造已缺失的历史证据。未改生产录制器、未发起工作流、未使用AWS或钱包。
