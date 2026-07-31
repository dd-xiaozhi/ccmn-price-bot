# 长江现货行情抓取 · 企微推送

抓取[长江有色网](https://www.ccmn.cn/)首页「长江现货」板块行情，导出 Excel，定时推送到企业微信群。

```
长江有色网 API  →  AES 解密  →  Excel  →  企微群（摘要 + 附件）
                                    ↑
                             config.yaml（cron / webhook，热加载）
```

---

## 快速开始

```bash
pip install -r requirements.txt

cp config.example.yaml config.yaml     # 填入真实 webhook
python3 run.py --dry-run               # 只抓取导出，不推送
python3 run.py                         # 抓取 + 推送企微
python3 scheduler.py                   # 常驻定时执行
```

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
| `ccmn_cjxh.py` | 抓取 + 解密 + Excel 导出（可独立 CLI 使用） |
| `wecom.py` | 企微机器人：markdown 摘要 + 文件上传推送 |
| `config.py` | YAML 加载、`${ENV}` 插值、校验、热更新检测 |
| `run.py` | 一次性任务编排：抓取 → 导出 → 推送 |
| `scheduler.py` | 常驻调度器，按 cron 执行，配置热加载 |
| `scripts/sync_workflow_cron.py` | 把 config 的 cron 同步进 GitHub Actions workflow |
| `config.example.yaml` | 配置模板（入库） |
| `config.yaml` | 实际配置，**含凭据，已 gitignore** |

---

## 配置

全部配置项见 `config.example.yaml`。常用：

```yaml
market: 长江现货          # 或 长江综合
markets_all: false        # true = 全部市场，每市场一个 sheet

schedule:
  cron: "*/30 * * * *"    # 标准 5 段 cron
  timezone: Asia/Shanghai
  run_on_start: false

wecom:
  enabled: true
  webhook: "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=${WECOM_WEBHOOK_KEY}"
  send_summary: true      # markdown 行情摘要
  send_file: true         # Excel 附件
  summary_top_n: 10
  notify_on_error: true   # 失败时群内告警

push:
  skip_if_unchanged: false   # true = 数据没变就不推（见下）
```

**`${ENV}` 插值**：任意字符串支持 `${VAR}` 与 `${VAR:-默认值}`，CI 里用 GitHub Secret 注入凭据。

**热加载**：`scheduler.py` 每 2 秒检查 `config.yaml` 的 mtime，改动后立即重载并按新 cron 重排，**不用重启**。配置写坏时保留上一份继续跑，只打错误日志。

### 关于 `skip_if_unchanged`

数据每天只在 10 点左右更新一次，而 cron 是每 30 分钟。默认 `false` = 每次都推（一天 48 条）。想只在数据真变化时推，设 `true` —— 按品名/价格/涨跌/发布时间做 SHA256 指纹比对，存在 `.push_state.json`。

---

## 定时执行

### 方式一：本地常驻（推荐，配置热加载）

```bash
python3 scheduler.py                    # 前台
nohup python3 scheduler.py > /dev/null 2>&1 &   # 后台
```

macOS 开机自启可用 launchd，Linux 用 systemd。

### 方式二：GitHub Actions

`.github/workflows/ccmn-push.yml` 已配好，支持定时 + 手动触发（`workflow_dispatch`，可选 dry-run / force）。

**部署三步**：

1. 仓库 Settings → Secrets and variables → Actions → New secret
   - Name：`WECOM_WEBHOOK_KEY`
   - Value：webhook URL 里 `key=` 后面那串
2. Actions 页面确认 workflow 已启用
3. 手动 Run workflow 验证一次

**改 cron 的正确姿势**（GitHub Actions 的 `schedule` 只能写死在 workflow 里，运行时读不到 yaml）：

```bash
# 1. 改 config.example.yaml 的 schedule.cron
# 2. 同步进 workflow（自动做时区换算，GH 只认 UTC）
python3 scripts/sync_workflow_cron.py
# 3. 提交这两个文件
```

`--check` 模式可用于 CI 校验两边是否一致。换算规则：小时位是 `*` 或 `*/n` 时与时区无关原样使用；是具体值时按配置时区偏移前移（如 `30 10 * * *` @ Asia/Shanghai → `30 2 * * *` UTC）。

#### GitHub Actions 的三个已知限制

| 限制 | 说明 |
|---|---|
| **不准时** | `schedule` 是尽力而为，高峰期常延迟 5–20 分钟，偶尔跳过。对准点要求高就用本地常驻。 |
| **会自动停** | 仓库 60 天无提交活动，定时 workflow 会被 GitHub 自动禁用，需手动重新启用。 |
| **耗额度** | 私有仓库定时任务算 Actions 分钟数。每 30 分钟 ≈ 1440 次/月，按每次 ~1 分钟算接近免费额度（2000 分钟/月）上限。跑久了建议放宽到每小时或每天。 |

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

- **`config.yaml` 不入库**（`.gitignore` 已排除）—— 里面有 webhook key，拿到的人可以往群里发任意消息
- CI 中凭据走 GitHub Secret，由 `${WECOM_WEBHOOK_KEY}` 注入，`config.example.yaml` 只留占位
- 日志里的 webhook 自动脱敏（`key=c43a****`）
- 直接依赖全部 exact-pin（`requirements.txt`），升级走 PR review

---

## 故障排查

| 现象 | 排查 |
|---|---|
| `配置引用了环境变量 ${WECOM_WEBHOOK_KEY}，但它未设置` | 本地 `export WECOM_WEBHOOK_KEY=...`，CI 检查 Secret 是否配好 |
| `企微返回 errcode=93000` | webhook key 失效或群机器人被移除 |
| `文件超过企微 20MB 上限` | 企微 webhook 文件上限 20MB，本场景数据量远小于此，出现即说明数据异常 |
| `抓取到 0 行数据` | 站点接口或加密方式变更，重新查 `ccmn-index.js` 里的 key 和接口路径 |
| Actions 定时不触发 | 见上文三个已知限制；先用 workflow_dispatch 手动跑确认脚本本身没问题 |

企微 webhook 机器人限流 20 条/分钟；`media_id` 有效期 3 天（本脚本每次上传都是新的，不受影响）。
