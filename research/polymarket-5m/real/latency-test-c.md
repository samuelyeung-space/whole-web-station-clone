# 检验 C：coinbase 触发的过期报价（btc，GitHub 前向录制）

事先写死：2026-09-30 11:00 UTC 起开始的 5m 市场；coinbase 逐笔成交在剩 240–60 秒时第一次相对至少一秒前涨跌超过 3σ，0.3 秒后按交易所时间戳盘口的卖一买顺势一方，付 taker 费，持有到结算；按触发时间取前 1,500 笔判定一次，EV > 0 且精确 p < 0.025 才算通过。每段录制单独计算（σ 不跨越 10 秒以上的空档，参考价不早于 5 秒），下单时刻前 30 秒内和后 10 秒内盘口都要有更新。

| 币种 | 录制段 | 触发（按 L = 检验值） |
|---|---:|---:|
| btc | 42 | 1,662 |

录制段备注（跳过的和断线检查）：

- btc 36671472407: skipped (FileNotFoundError: no COINBASE_TRADE events under /home/runner/work/_temp/rec/36671472407/x/bundle-btc/latency)
- btc 36704245701: 1 candidates dropped because the CLOB socket closed between the quote and the order (25 disconnects in the recording; test C counts its judged lag only)
- btc 36740728504: 2 candidates dropped because the CLOB socket closed between the quote and the order (33 disconnects in the recording; test C counts its judged lag only)
- btc 37002779006: 3 candidates dropped because the CLOB socket closed between the quote and the order (107 disconnects in the recording; test C counts its judged lag only)
- btc 37041041140: 4 candidates dropped because the CLOB socket closed between the quote and the order (70 disconnects in the recording; test C counts its judged lag only)
- btc 37075141917: 5 candidates dropped because the CLOB socket closed between the quote and the order (80 disconnects in the recording; test C counts its judged lag only)
- btc 37095301195: 10 candidates dropped because the CLOB socket closed between the quote and the order (76 disconnects in the recording; test C counts its judged lag only)
- btc 37111395430: 8 candidates dropped because the CLOB socket closed between the quote and the order (77 disconnects in the recording; test C counts its judged lag only)
- btc 37128550183: 3 candidates dropped because the CLOB socket closed between the quote and the order (58 disconnects in the recording; test C counts its judged lag only)
- btc 37146417247: 4 candidates dropped because the CLOB socket closed between the quote and the order (102 disconnects in the recording; test C counts its judged lag only)
- btc 37164037946: 3 candidates dropped because the CLOB socket closed between the quote and the order (61 disconnects in the recording; test C counts its judged lag only)
- btc 37179397738: 3 candidates dropped because the CLOB socket closed between the quote and the order (70 disconnects in the recording; test C counts its judged lag only)
- btc 37194238626: 7 candidates dropped because the CLOB socket closed between the quote and the order (78 disconnects in the recording; test C counts its judged lag only)
- btc 37230483105: 8 candidates dropped because the CLOB socket closed between the quote and the order (179 disconnects in the recording; test C counts its judged lag only)
- btc 37250413580: 11 candidates dropped because the CLOB socket closed between the quote and the order (161 disconnects in the recording; test C counts its judged lag only)
- btc 37274274070: 9 candidates dropped because the CLOB socket closed between the quote and the order (214 disconnects in the recording; test C counts its judged lag only)
- btc 37307261464: 7 candidates dropped because the CLOB socket closed between the quote and the order (182 disconnects in the recording; test C counts its judged lag only)
- btc 37347643542: 18 candidates dropped because the CLOB socket closed between the quote and the order (249 disconnects in the recording; test C counts its judged lag only)
- btc 37384144976: 7 candidates dropped because the CLOB socket closed between the quote and the order (157 disconnects in the recording; test C counts its judged lag only)
- btc 37412793391: 12 candidates dropped because the CLOB socket closed between the quote and the order (219 disconnects in the recording; test C counts its judged lag only)
- btc 37444926603: 10 candidates dropped because the CLOB socket closed between the quote and the order (244 disconnects in the recording; test C counts its judged lag only)
- btc 37484528542: 9 candidates dropped because the CLOB socket closed between the quote and the order (232 disconnects in the recording; test C counts its judged lag only)
- btc 37527655881: 7 candidates dropped because the CLOB socket closed between the quote and the order (120 disconnects in the recording; test C counts its judged lag only)
- btc 37558084377: 16 candidates dropped because the CLOB socket closed between the quote and the order (184 disconnects in the recording; test C counts its judged lag only)
- btc 37586562398: 10 candidates dropped because the CLOB socket closed between the quote and the order (289 disconnects in the recording; test C counts its judged lag only)
- btc 37621711008: 13 candidates dropped because the CLOB socket closed between the quote and the order (356 disconnects in the recording; test C counts its judged lag only)
- btc 37663361030: 9 candidates dropped because the CLOB socket closed between the quote and the order (260 disconnects in the recording; test C counts its judged lag only)
- btc 37702189414: 10 candidates dropped because the CLOB socket closed between the quote and the order (230 disconnects in the recording; test C counts its judged lag only)
- btc 37729957174: 9 candidates dropped because the CLOB socket closed between the quote and the order (230 disconnects in the recording; test C counts its judged lag only)
- btc 37764001551: 10 candidates dropped because the CLOB socket closed between the quote and the order (361 disconnects in the recording; test C counts its judged lag only)
- btc 37806403126: 11 candidates dropped because the CLOB socket closed between the quote and the order (338 disconnects in the recording; test C counts its judged lag only)
- btc 37847134073: 11 candidates dropped because the CLOB socket closed between the quote and the order (240 disconnects in the recording; test C counts its judged lag only)
- btc 37875151310: 8 candidates dropped because the CLOB socket closed between the quote and the order (235 disconnects in the recording; test C counts its judged lag only)
- btc 37903011520: 13 candidates dropped because the CLOB socket closed between the quote and the order (236 disconnects in the recording; test C counts its judged lag only)
- btc 37938384834: 9 candidates dropped because the CLOB socket closed between the quote and the order (231 disconnects in the recording; test C counts its judged lag only)

| L | 笔数 | 胜率 | 平均价 | EV | p | 卖一数量中位 |
|---:|---|---|---|---|---|---|
| 0 秒 | 1,752 | 59.6% | 0.570 | +1.31¢ | 0.1051 | 105 |
| 0.1 秒 | 1,709 | 59.5% | 0.580 | +0.24¢ | 0.4217 | 70 |
| 0.2 秒 | 1,679 | 59.4% | 0.593 | -1.12¢ | 1.0000 | 61 |
| 0.3 秒 | 1,662 | 59.4% | 0.607 | -2.51¢ | 1.0000 | 84 |
| 0.4 秒 | 1,656 | 59.4% | 0.614 | -3.23¢ | 1.0000 | 105 |
| 0.5 秒 | 1,648 | 59.3% | 0.617 | -3.60¢ | 1.0000 | 142 |
| 1 秒 | 1,625 | 59.4% | 0.621 | -3.88¢ | 1.0000 | 283 |
| 2 秒 | 1,596 | 59.3% | 0.622 | -4.01¢ | 1.0000 | 351 |
| 5 秒 | 1,523 | 59.6% | 0.621 | -3.75¢ | 1.0000 | 361 |

检验 C（前 1,500 笔）：过期报价（z=3，0.3 秒后按卖一买）：1500 笔，EV -2.08¢，p = 1.0000 → 没通过（录制段 37 个：36704245701, 36740728504, 36781277830, 36807987110, 36835222993, 36869869857, 36912544596, 36946930986, 36972067946, 37002779006, 37041041140, 37075141917, 37095301195, 37111395430, 37128550183, 37146417247, 37164037946, 37179397738, 37194238626, 37230483105, 37250413580, 37274274070, 37307261464, 37347643542, 37384144976, 37412793391, 37444926603, 37484528542, 37527655881, 37558084377, 37586562398, 37621711008, 37663361030, 37702189414, 37729957174, 37764001551, 37806403126；最后一笔触发于 2026-10-08 18:36 UTC）
