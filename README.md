# moontvtransfercms

每天凌晨 3 点自动把 [MoonTV](https://pz.v88.qzz.io?format=2&source=jin18) 的 base58 订阅转换为 [LibreTV](https://github.com/LibreSpark/LibreTV) 支持的数据源订阅（CMS / LibreTV-SourceList JSON），生成 `cms.json` 并提交到本仓库。

## 订阅地址（在 LibreTV 中使用）

```
https://raw.githubusercontent.com/meishero/moontvtransfercms/main/cms.json
```

**LibreTV 使用方法**：设置 → 源管理 → 数据源订阅 → 粘贴上面的地址 → 订阅。导入的点播源会自动勾选，之后按需手动同步或设置自动刷新周期。

> 注意：仓库需为 **公开** 才能通过 raw.githubusercontent.com 直接访问。

## 工作原理

```
MoonTV 订阅（base58 编码的 JSON 配置）
  → convert.py 解码（自动识别 base58 / base64 / 纯 JSON）
  → 清洗（去尾部斜杠、按 url 去重、仅保留公网 http(s)、上限 100 个）
  → cms.json（LibreTV-SourceList 格式，version 2）
  → GitHub Action 提交推送
```

输出格式示例：

```json
{
  "name": "MoonTV 聚合点播源",
  "version": 2,
  "sources": [
    { "name": "🎬-爱奇艺-", "url": "https://iqiyizyapi.com/api.php/provide/vod", "isAdult": false, "detail": "https://iqiyizyapi.com" }
  ],
  "liveSources": []
}
```

## 文件说明

| 文件 | 说明 |
| --- | --- |
| `convert.py` | 转换脚本，纯标准库实现，无需安装依赖 |
| `cms.json` | 转换产物，每日更新 |
| `.github/workflows/update-cms.yml` | 定时任务：每天北京时间 03:00（UTC 19:00）执行，支持手动触发 |

## 本地运行

```bash
python convert.py
```

环境变量：

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `SUB_URL` | `https://pz.v88.qzz.io?format=2&source=jin18` | 上游 MoonTV 订阅地址 |
| `OUTPUT_FILE` | `cms.json` | 输出文件名 |
| `LIST_NAME` | `MoonTV 聚合点播源` | 订阅列表显示名称 |

## 常见修改

- **换订阅源**：改 `.github/workflows/update-cms.yml` 里的 `SUB_URL`（本地跑就设环境变量）。
- **改执行时间**：改 workflow 里的 `cron` 表达式（UTC 时间；北京时间 = UTC + 8，如北京时间 6 点 = `0 22 * * *`）。
- **手动立即更新**：仓库页面 → Actions → Update cms.json → Run workflow。

## 说明

- 源站失效是常态，LibreTV 会标记不健康源并自动停用，不影响其他源。
- 拉取失败时 Action 会报错退出，不会提交空的或损坏的 `cms.json`，仓库保留上一次的正常数据。
