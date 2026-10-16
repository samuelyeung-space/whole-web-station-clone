# 研究录制工件分片传输（离线工具，未部署）

当前已连接下载接口拒绝大于 512 MiB 的单个 Actions artifact。本工具在**有原文件的已授权发布环境**拆分一个已关闭归档；没有下载旁路、AWS 凭据查找、录制重启或订单操作。每片默认 400 MiB，加 ZIP_STORED 封装，保留外层上传余量。源文件、各片 payload、各片 ZIP、独立清单均有 SHA256。

```sh
python artifact_parts.py split --source /private/closed/raw.tgz --out /private/parts
python artifact_parts.py assemble --manifest /private/parts/manifest.json \
  --parts-dir /private/parts --out /private/restored/raw.tgz \
  --expected-manifest-sha256 <从可信发布记录保留的清单哈希>
python -m unittest test_artifact_parts -v
```

## 发布与恢复约定

1. 仅处理已经关闭、不可变的归档；拆分期间变更会失败。失败不覆盖旧文件；重组只在所有校验通过后发布输出。
2. **每片 ZIP 必须上传成单独一个 Actions artifact**，例如 `recording-RUN-part-00000`。不能将所有片作为一次 upload-artifact 的 path，否则仍是一个超大工件。小清单独立发布；保留原来源提交、运行 ID、下载 artifact ID 和 manifest SHA256 的映射。
3. 在原来的私有/已授权存储通道发布；不将私有原始数据、账本或含内容的测试文件提交公共 Git。代码提交不等于上传工作流已启用。
4. 下载后将各 part ZIP 放在同一目录（不同 run 用不同目录）。重组只接受清单中的规范有序文件名、一个 STORED payload；拒绝缺片、篡改、重排、重复键、路径穿越和已有输出。不提取归档内路径。
5. 本工具只建立字节相等性。其后仍须验证归档 CRC、BTC5m 覆盖、双侧盘口、本地接收时钟、费用和结算来源，才能进入回放。不得把源时间补成本地接收时间。
6. Oct12-19 正式确认数据保持隔离；分片操作不读取行情或标签、不构成新回测。默认研究基准仍为 $1,000 / 目标 100 shares / 部分 FAK / 500、600、1000ms。

## 此版本边界

此版本没有修改线上上传工作流，也没有触发采集、导出或通知。GitHub 接力采集与 AWS 常驻录制是不同来源；下载受限、尚未归一化、缺字段、真实断流必须分别登记。发布环境仍需将工具接入逐片上传，旧超大包需要在拥有原文件的授权环境重新发布。仅有本工具不能宣称这一步已经完成。
