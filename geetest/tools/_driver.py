# -*- coding: utf-8 -*-
"""评测共用驱动：直接运行 py/geetest_solve.py 的生产定位路径。"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def locate_group(main: str, targets: list, model: str = None, verify: bool = False,
                 cross_check: bool = True, candidate_verify: bool = True,
                 main_scale: float = 2.0, geometric_rescue: bool = True) -> list:
    # 延迟导入：离线重计分不加载模型配置，更不会发网络请求。
    sys.path.insert(0, os.path.join(ROOT, "py"))
    import geetest_solve as gs

    saved_model = gs.MODEL
    try:
        if model:
            gs.MODEL = model
        orig = gs.load_rgb(main)
        sent = gs.prepare_main(main, scale=main_scale)
        icons = [gs.prep_icon(path) for path in targets]
        results, _, _, _ = gs.locate_all(
            orig, gs.data_url(sent), sent.size, icons,
            verify=verify, cross_check=cross_check,
            candidate_verify=candidate_verify, geometric_rescue=geometric_rescue,
        )
        for position, result in enumerate(results):
            if position < len(targets):
                result["target"] = targets[position]
        return results
    finally:
        gs.MODEL = saved_model
