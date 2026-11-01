# -*- coding: utf-8 -*-
"""pptx-kit：契约驱动的 PPT 生成器。

渲染链路：页面契约 JSON → 版式函数 → pptx → 几何自检 → 目视复查
"""
from .builder import ContractError, build, build_from_file
from .check import check, report
from .contract import ok, validate
from .theme import FS, Theme

__all__ = [
    "build", "build_from_file", "ContractError",
    "validate", "ok", "check", "report", "Theme", "FS",
]
__version__ = "0.1.0"
