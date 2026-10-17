# 部署到 Render（当前确实有免费档）

> 2026-10-17 在 render.com/pricing 上核过：**Free $0/月，512 MB 内存、不到 1 个 CPU**。
> 有休眠、不能挂持久磁盘，但**能跑 Docker**，所以这个项目直接就能上。

---

## 先说清楚免费档的三个限制

| 限制 | 影响 | 我们的应对 |
|---|---|---|
| **15 分钟没访问就休眠** | 下次打开要等约 1 分钟冷启动 | 演示站可接受；在意就升级 $7/月 |
| **不能挂持久磁盘** | 重启后 `database/` 丢失 | `MONITOR_DEMO_SEED=1` → 空库自动重建示例项目 |
| **512 MB 内存** | 批量导出 20 多期日报可能吃紧 | 演示站不会批量导 24 期；真要用建议 1GB 档（$7/月）|

---

## 部署步骤（约 5 分钟）

### 先看一个前提

Render 的一键部署要读仓库里的 **`render.yaml`**。如果这个文件还没推到 GitHub，
就跳过下面「方式 A」，直接走「方式 B 手动建」（用仓库里已有的 `Dockerfile`，一样能跑）。

---

### 方式 A：Blueprint 一键部署（仓库里有 `render.yaml` 时）

1. 控制台右上 **New** → **Blueprint**
2. 选仓库 **`sky-Edge/pitwatch`** → **Connect**
3. Render 会读到 `render.yaml`，显示要创建的服务 `pitwatch`
4. 点 **Apply**

它自动做的事（都在 `render.yaml` 里写好了）：

- 用仓库根目录的 `Dockerfile` 构建
- 计划选 `free`，区域 `singapore`（亚太，国内访问好一些）
- 健康检查打 `/api/health`
- 环境变量：`MONITOR_DEMO_SEED=1`、`TZ=Asia/Shanghai`

---

### 方式 B：手动建 Web Service（仓库里没有 `render.yaml` 时）

1. 控制台右上 **New** → **Web Service**（不是 Blueprint）
2. **Connect a repository** → 选 **`sky-Edge/pitwatch`** → Connect
3. 按下面的表填（其余保持默认）：

| 字段 | 填什么 | 说明 |
|---|---|---|
| **Name** | `pitwatch` | 会变成域名的一部分 |
| **Region** | `Singapore` | 亚太，国内快；选错会慢很多 |
| **Branch** | `master` | |
| **Runtime / Language** | **`Docker`** | ⚠️ 别选 Python，会去猜怎么装 |
| **Dockerfile Path** | `./Dockerfile` | 默认就是 |
| **Instance Type** | **`Free`** | 512 MB / 0.1 CPU |
| **Health Check Path** | `/api/health` | 没配的话冷启动容易被判失败 |

4. 展开 **Environment Variables**，手动加两条（**别漏，尤其是 TZ**）：

| Key | Value |
|---|---|
| `MONITOR_DEMO_SEED` | `1` |
| `TZ` | `Asia/Shanghai` |

5. 点 **Create Web Service**，等构建

> ⚠️ 漏了 `TZ` 不会报错，只会让"今天"偏 8 小时 —— 观测日期、日报期次、周报统计全歪，而且**从现象上看不出来**。

---

### 等构建

第一次约 5–10 分钟（要装 Node 依赖、跑 `tsc && vite build`、装 Python 依赖）。
日志里能看到 `npm run build` 和 `pip install` 的进度，属正常。

### 拿地址

构建完，服务页顶部会显示：

```
https://pitwatch-xxxx.onrender.com
```

打开它，应该直接看到 **「示例项目（8 个测点 · 6 期演示数据）」**。

---

## 上线自检

| 检查 | 期望 |
|---|---|
| 打开首页 | 项目列表里有 1 个示例项目 |
| 点进去 → 曲线 | 有折线 |
| 成果导出 → 选一期 → 导出 Excel | 能下到 .xlsx |
| 等 15 分钟不访问，再打开 | 慢约 1 分钟（冷启动），之后正常 |

---

## 常见问题

**Q：打开很慢，要等一分钟？**
免费实例休眠了，正在唤醒。之后正常。想一直在线：服务页 → Settings → 改成 `Starter`（$7/月）。

**Q：页面空白 / 502？**
1. 看 **Logs** 里有没有 `[演示模式] 空库，已自动创建示例项目`
2. 打开 `/api/health`，返回 JSON 说明后端正常 → 那就是前端构建的问题，回看 Build 日志
3. 内存超了会直接 502 —— Logs 里会写 `Ran out of memory`，这种情况只能升级到 1GB

**Q：数据能保住吗？**
免费档不能。挂了持久磁盘要付费（$0.25/GB/月）。演示站不需要 —— 重启后示例数据自动重建。
真要让同事长期用，建议还是买台轻量服务器（¥60/月，本地磁盘天然持久，也不用绑卡）。

**Q：能改代码后自动更新吗？**
能。`render.yaml` 里 `autoDeploy: true`，推 `master` 就自动重新部署。

---

## 不想绑卡的话

Render 免费档要验证一张卡。完全不想绑卡，可选：

| 方案 | 要卡吗 | 说明 |
|---|---|---|
| **Oracle Cloud 永久免费** | 要（但可用虚拟卡）| 4 核 24G ARM，**真服务器**、有磁盘、数据能存住。注册成功率一般 |
| **Hugging Face Spaces** | 不要 | 免费 Docker，但**空间公开**、48 小时休眠、磁盘临时 |
| **阿里云 / 腾讯云试用** | 要（实名）| 1–3 个月免费，国内最快，到期 ¥60/月 |

要我换成其中哪一个，说一声，配置我来改。
