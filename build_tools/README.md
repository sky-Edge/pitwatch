# 打包与发布（Windows 免安装测试包）

给同事/朋友试用的版本：**对方不需要装 Python、不需要装 Node、不需要联网**，
解压后双击即可。

```bash
# 一条命令完成：构建前端 → PyInstaller 打包 → 组装发布目录 → 打 zip
python build_tools/build_release.py
```

产物：

| 路径 | 内容 |
|---|---|
| `release/基坑监测系统-测试版/` | 发布目录（`MonitorSystem.exe` + `_internal/` + `启动系统.bat` + `使用说明.txt`） |
| `release/基坑监测系统-测试版.zip` | 发给朋友的就是这个（约 28 MB） |

> ⚠ 本目录**不能叫 `packaging/`**：那与 PyInstaller 依赖的第三方库 `packaging` 重名，
> 构建时 cwd 会进 `sys.path`，可能让 PyInstaller 导入到我们自己的目录而崩。

## 运行形态

- **单端口**：前端构建产物（`frontend/dist`）随包一起打进去，由 FastAPI 挂在 `/`，
  所以只有一个地址 `http://127.0.0.1:8000`，不需要 Node。
- **数据在 exe 同级**：`database/`、`templates/`、`logs/` 都相对 exe 定位。
  换电脑把整个文件夹拷走即可；卸载 = 删文件夹。
- **首启自动建库**：空库 + 内置导出模板，不含任何生产数据（构建脚本会显式清一遍）。
- **关窗即停 + 自动备份**：关掉控制台窗口时快照一次数据库到 `database/backups/`（保留 30 份）。

`app_entry.py` 另有两个只有自动化测试才用的参数（正常双击不会传）：
`--no-browser`（不弹浏览器）、`--port N`（指定端口）、`--stop-after N`（N 秒后走一遍停机流程）。

## 踩过的坑（改这块之前先读）

1. **数据目录不能用 `__file__`**。打包后它指向 `_internal/`（onedir）或
   `%TEMP%/_MEIxxxx/`（onefile，退出即删）——后者会让用户的数据库每次重启都没了。
   见 `backend/core/config.py::_resolve_base_dir`。
2. **停机收尾必须"串行 + 等它做完"**。只设一个标志就返回，主线程会退出进程、
   把还在备份的 daemon 线程一起带走 —— 现象是"关窗了、没报错、也没有备份"。
   见 `app_entry.py::_graceful_shutdown`（锁 + done 事件）。
3. **清理构建产物不能用 `shutil.rmtree`**。受限环境里的"安全删除"垫片会在单轮删除
   > 50 个文件时直接中止进程（脚本没报错就退出，极难排查）。构建产物上千个文件必然踩中，
   统一走 `clean_dir()`（系统 `rmdir`）。
4. **静态挂载必须在 API 路由之后**。`app.mount("/")` 会吃掉所有未匹配路径。
5. **pandas / numpy 要排除**：后端一个都没用到（可用脚本扫 `backend/**` 的 import 验证），
   打进包里白白多 150 MB。

## 验收清单

```bash
# 1) 常规回归（含打包守卫：路径语义、收尾幂等、构建脚本不删数据目录）
python -m pytest tests/test_packaging.py -q

# 2) 打包后按"朋友视角"实测（复制到新目录、从别处启动、跑业务、关窗看备份）
#    脚本在临时目录里，按需重跑：
#    test_release.py —— 页面/静态资源/空库/示例项目/导出 Excel+Word/持久化
#    test_stop.py    —— 停机收尾是否真的备份
```

实测覆盖到的关键点：`GET /` 200、`/assets/*.js` 200、`/api/v1/projects` 200、
示例项目 201、Excel（openpyxl）与 Word（python-docx）导出文件可被解析、
重启后数据仍在、生产库指纹跑前跑后一致。
