# 我如何把项目交给 ChatGPT 理解

我为跨对话请教准备了七份 Markdown：先介绍产品、本地实现、Dify 映射与最新模型，再附完整节点、关键源码、决策与操作理由。模型默认设置变化说明优先于旧教学参数；源码与实际运行结果分别标注。

在项目根目录运行：

```powershell
.\.venv\Scripts\python.exe -X utf8 scripts/build_chatgpt_context.py
```

输出目录为 `runtime/share/chatgpt-project-context/`，同级生成按日期命名的 ZIP。里面只有 7 个 Markdown 和一个可核对内容指纹的 MANIFEST.json；不包含 .env、私人课程正文、数据库、日志、模型权重或缓存。生成器从明确文件清单读取，打包前检查已配置密钥及匹配的课程正文片段，生成后校验 ZIP CRC 与 SHA-256，不调用任何模型。

建议先将解压后的 00、01、02 上传到 ChatGPT，粘贴 00 里的开场提示词；按问题补充 03 至 06。也可一次上传全部 7 个 Markdown。ZIP 用于传输，是否能直接解析取决于当前 ChatGPT 页面；不要求模型猜测压缩包内容或平台上传限制。

精简入口：[00-先读我与开场提示词](00-先读我与开场提示词.md)。完整生成包留在本地 runtime，避免把源码和大段节点文档在仓库再重复一份；模板和生成脚本公开维护。
