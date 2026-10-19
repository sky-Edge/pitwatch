# -*- coding: utf-8 -*-
"""版本号 —— **唯一出处**。

改版本号只改这里（`__version__`），然后跑：

    python tools/version.py save "这次改了啥"

它会同步根目录的 `VERSION`、写 `CHANGELOG.md`、提交并打 git tag —— 一整套动作，
省得"代码里写一个、标签打一个、文件里又是另一个"（记忆里那条铁律：配对的东西最容易只改一头）。

**为什么不用读根目录的 VERSION 文件**：打包成 exe 之后，根目录的 `VERSION` 不一定被带进包，
而 `.py` 一定在包里。所以代码里留常量，`VERSION` 只是给人和脚本看的镜像。
两边是否一致由 `tests/test_version_single_source.py` 守着。
"""
from __future__ import annotations

__version__ = "1.1.4"

#: 给人看的产品名（界面顶栏、日志抬头共用）
APP_TITLE = "基坑监测"
