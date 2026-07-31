# 长江现货行情抓取 · 企微推送

抓取[长江有色网](https://www.ccmn.cn/)首页「长江现货」板块行情，导出 Excel，定时推送到企业微信群。

```
长江有色网 API  →  AES 解密  →  Excel  →  企微群（摘要 + 附件）
                                    ↑
                             config.yaml（热加载）
```

---

## 快速开始

```bash
pip install -r requirements.txt

python3 run.py --dry-run     # 只抓取导出，不推送
python3 run.py               # 抓取 + 推送企微
python3 scheduler.py         # 常驻定时执行
```

配置开箱即用，`config.yaml` 已含 webhook。

---

## 数据来源

首页价格面板是 JS 异步渲染，静态 HTML 里只有 `<div class="loading">`，直接爬 DOM 拿不到数据。实际链路：

| 项 | 值 |
|---|---|
| 接口 | `GET https://www.ccmn.cn/shop/historyData/getCjysAndCjysw` |
| 加密 | `showMarketList` / `showPriceList` 为 **AES-128-ECB + PKCS7** |
| 密钥 | 硬编码于 `https://static.ccmn.cn/js/ccmn-index.js`（`var keys = "..."`） |
| 市场 | `长江现货`（36 品名）、`长江综合`（135 品名） |
| 更新 | 每日约 10:00–10:20 |

拿的是渲染前的原始 JSON，字段比页面表格多（牌号 / 产地 / 备注 / 走势链接等）。

**Excel 列**：品名、最低价、最高价、价格区间、均价、涨跌、单位、牌号、产地、备注、日期、发布时间、市场、数据来源、走势链接。表头冻结 + 自动筛选，涨红跌绿。

> 升贴水行（如 `铜升贴水` = `b340-b380`）带 `b`/`c` 前缀，原样保留为文本，不强转数字 —— 保真优先。

---

## 文件说明

| 文件 | 作用 |
|---|---|
| `config.yaml` | 唯一配置源，热加载 |
| `ccmn_cjxh.py` | 抓取 + 解密 + Excel 导出（可独立 CLI 使用） |
| `wecom.py` | 企微机器人：markdown 摘要 + 文件上传推送 |
| `config.py` | YAML 加载、`${ENV}` 插值、校验、热更新检测 |
| `run.py` | 一次性任务编排：抓取 → 导出 → 推送 |
| `scheduler.py` | 常驻调度器，按 cron 执行，配置热加载 |
| `.github/workflows/ccmn-push.yml` | GitHub Actions 定时部署 |

---

## 配置

全部配置项见 `config.yaml` 内注释。常用：

```yaml
market: 长江现货          # 或 长江综合
markets_all: false        # true = 全部市场，每市场一个 sheet

schedule:
  cron: "*/30 * * * *"    # 仅本地 scheduler.py 生效
  timezone: Asia/Shanghai
  run_on_start: false

wecom:
  enabled: true
  webhook: "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=..."
  send_summary: true      # markdown 行情摘要
  send_file: true         # Excel 附件
  summary_top_n: 10
  notify_on_error: true   # 失败时群内告警

push:
  skip_if_unchanged: false   # true = 数据没变就不推
```

**热加载**：`scheduler.py` 每 2 秒检查 `config.yaml` 的 mtime，改动后立即重载并按新 cron 重排，**不用重启**。配置写坏时保留上一份继续跑，只打错误日志。

**`${ENV}` 插值**：任意字符串支持 `${VAR}` 与 `${VAR:-默认值}`。临时换群不用改文件：

```bash
WECOM_WEBHOOK_KEY=<另一个key> python3 run.py
# 前提是把 config.yaml 的 webhook 改成 ${WECOM_WEBHOOK_KEY:-<默认key>} 形式
```

**`skip_if_unchanged`**：数据每天只更新一次。高频 cron 下设 `true` 可避免重复刷屏 —— 按品名/价格/涨跌/发布时间做 SHA256 指纹比对，状态存 `.push_state.json`。固定每天一次后无所谓。

---

## 定时执行

### 方式一：本地常驻（配置热加载，准点）

```bash
python3 scheduler.py                              # 前台
nohup python3 scheduler.py > /dev/null 2>&1 &     # 后台
```

改 `config.yaml` 的 cron 即时生效。macOS 开机自启用 launchd，Linux 用 systemd。

### 方式二：GitHub Actions

`.github/workflows/ccmn-push.yml` 已配好，推上去就跑。支持手动触发（Actions → Run workflow，可选 dry-run / force）。

**改定时**：直接改 workflow 里的 `- cron:` 那行。**读不到 `config.yaml`** —— GitHub 的 `schedule` 必须写死在 workflow 文件里，这是平台限制。

时间是 **UTC**，北京时间 = UTC + 8：

| 需求 | workflow cron |
|---|---|
| 每 30 分钟（当前，调试用） | `*/30 * * * *` |
| 每天北京 10:30 | `30 2 * * *` |
| 工作日北京 10:30 | `30 2 * * 1-5` |

#### 三个已知限制

| 限制 | 应对 |
|---|---|
| **不准时** | `schedule` 尽力而为，高峰期常延迟 5–20 分钟，偶尔跳过。要准点用本地常驻 |
| **60 天无活动自动禁用** | 已内置保活：workflow 检测到距上次提交 ≥45 天时自动推一个空提交，计时器归零。需要 `permissions: contents: write`（已配） |
| **耗额度** | 私有仓定时任务算 Actions 分钟。每 30 分钟 ≈ 1440 次/月 × ~1 分钟，接近免费额度（2000 分钟/月）上限。调完切每天一次后 ≈ 30 分钟/月，可忽略 |

---

## CLI

```bash
# 抓取导出（不涉及配置和推送）
python3 ccmn_cjxh.py                    # 长江现货
python3 ccmn_cjxh.py --all              # 全部市场
python3 ccmn_cjxh.py --market 长江综合
python3 ccmn_cjxh.py -o out.xlsx

# 完整任务
python3 run.py --dry-run                # 不推送
python3 run.py --force                  # 忽略 skip_if_unchanged
python3 run.py -c other.yaml            # 指定配置

# 调度器
python3 scheduler.py -c other.yaml
```

---

## 安全

⚠️ **`config.yaml` 里的 webhook 是明文凭据，且已入库。**

- 拿到 key 的人可以往群里发任意消息
- **仓库必须保持私有**；转公开或加协作者前先换 key
- 明文一旦提交就永久留在 git 历史里，删文件删不掉
- 撤销方式：企微后台移除该群机器人重建，拿新 key 替换

日志里的 webhook 会自动脱敏（`key=a5f1****`）。直接依赖全部 exact-pin（`requirements.txt`），升级走 PR review。

---

## 故障排查

| 现象 | 排查 |
|---|---|
| `企微返回 errcode=93000` | webhook key 失效或群机器人被移除 |
| `抓取到 0 行数据` | 站点接口或加密方式变更，重新查 `ccmn-index.js` 里的 key 和接口路径 |
| `文件超过企微 20MB 上限` | 企微 webhook 文件上限 20MB，本场景远小于此，出现即说明数据异常 |
| Actions 定时不触发 | 见上文三个限制；先用 Run workflow 手动跑确认脚本本身没问题 |
| Actions 被禁用了 | Actions 页面点 "Enable workflow"；保活步骤会防止再次发生 |

企微 webhook 机器人限流 20 条/分钟；`media_id` 有效期 3 天（每次上传都是新的，不受影响）。
