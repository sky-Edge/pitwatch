# -*- coding: utf-8 -*-
"""静态资源：该缓存的一年不动，该压缩的必须压。

背景
----
服务器在悉尼，现场链路实测下行 17 KB/s。前端主包 1.4 MB 原样下发要传一分多钟，
用户感受到的就是"打开很慢 / 一片白"。两件事能救：

1. `assets/*` 带内容 hash → `immutable` 缓存一年，浏览器连请求都不发；
2. 传输压缩：1.4 MB → 442 KB。

⚠ 最容易踩的坑是**把首页也缓存了**：那样用户永远拿不到新版本的前端，
   再去请求已被删掉的旧 JS，整页白屏。所以这里专门钉一条"首页不许缓存"。
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MAIN = ROOT / "backend" / "main.py"
DIST = ROOT / "frontend" / "dist"


def test_static_serving_compresses_and_caches():
    """源码层面钉住三件事：压缩、immutable、首页不缓存。"""
    src = MAIN.read_text(encoding="utf-8")
    block = src[src.index("class CachedStaticFiles") : src.index('app.mount("/", CachedStaticFiles')]

    assert "_gzip_for" in block, "静态资源没有做传输压缩 —— 1.4MB 主包会原样传完"
    assert "Content-Encoding" in block and "gzip" in block
    assert "Vary" in block, "压缩响应必须带 Vary: Accept-Encoding，否则中间层会发错缓存"
    assert "immutable" in block, "assets 必须 immutable 长缓存"
    assert "no-cache" in block, "index.html 必须 no-cache"


def test_gzip_only_for_compressible_types():
    """图片/字体不重复压：压了也不小，白烧 CPU。"""
    src = MAIN.read_text(encoding="utf-8")
    block = src[src.index("_GZ_EXT") : src.index("_GZ_EXT") + 200]
    for ext in (".js", ".css", ".svg"):
        assert ext in block, f"压缩白名单缺 {ext}"
    for ext in (".png", ".jpg", ".woff2"):
        assert ext not in block, f"{ext} 已经压过，不该再压"


def test_dist_assets_are_hashed():
    """构建产物必须带内容 hash —— 否则 immutable 缓存会让用户永远拿旧文件。"""
    if not DIST.is_dir():
        return  # 没构建过就跳过（CI 里可能只跑后端测试）
    js = list((DIST / "assets").glob("index-*.js"))
    assert js, "没找到带 hash 的入口文件"
    name = js[0].stem  # index-CWHrQ0eT
    suffix = name.split("-", 1)[1]
    assert len(suffix) >= 6, f"文件名不像内容 hash：{name}"
