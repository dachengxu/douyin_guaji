#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
douyin_guaji 福袋倒计时 / 裁剪区域回归测试。

用途：把历次踩过的“时间格式异常”坑固化成自动断言，方便后续改动后回归。
  - 纯字符串解析用例：覆盖 check_countdown 的各种输入（3位、乱码、空、拆框…），无需图片。
  - 图片 fixture 用例：对 test/ 下的真实截图，端到端验证
      “裁剪区域 + OCR + check_countdown” 能稳定解析出正确剩余秒数。

运行：
    cd /home/dacheng/project/douyin_guaji
    ./.venv/bin/python test/regression.py

依赖：项目 .venv（Python 3.9 + paddleocr + Pillow + numpy）。
新增 fixture：把截图放进 test/ 目录，并在下方 FIXTURES 列表追加一项
（file / expected_seconds / content_terms）。
"""
import os
import sys

# 让测试能直接 import 项目根目录的模块
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PIL import Image
import numpy as np
from paddleocr import PaddleOCR

from Underlying_Operations import underlying_operations
import douyin_fudai as df

# 说明：check_countdown 是纯函数（不依赖 self 状态），以未绑定方式调用
# underlying_operations.check_countdown(None, text)，避免实例化时加载 PaddleOCR 的开销。

# 与 douyin_fudai 默认 y_pianyi 保持一致（get_fudai_contain 内会叠加到 y 坐标）
Y_PIANYI = -50

# ---------------------------------------------------------------------------
# 纯字符串解析用例（不依赖图片）
# 历次修复记录：
#   - 分钟<10 只有3位（'4 : 30'）→ 需兼容
#   - OCR 乱码夹字符（'7[08] : 07'）→ 需以冒号为锚容错
#   - 倒计时区域偶发读空，改从 content（'已参与／07：58后开'）兜底
#   - 无冒号拆框（'0807'）→ 退化纯数字 4 位
# (输入文本, 期望剩余秒数；None 表示应解析失败)
# ---------------------------------------------------------------------------
PARSE_CASES = [
    ('4 : 30', 270),                       # 分钟<10，3位
    ('14 : 02', 842),
    ('12 : 32', 752),
    ('04 : 01', 241),
    ('07 : 58', 478),
    ('7[08] : 07', 487),                   # OCR 乱码（夹 ']'）
    ('0807', 487),                         # 无冒号拆框
    ('120人已参与／07：58后开', 478),        # content 兜底含冒号（07:58=478）
    ('购）139人已参与04:01后开奖孟将钢刀鲢鳙碳素支架一个', 241),
    ('完成任务抽取奖品', None),              # 无数字 → 应失败
    ('', None),                            # 空 → 应失败
]

# ---------------------------------------------------------------------------
# 图片 fixture（test/ 下的真实截图）。expected_seconds 来自人工核对 OCR 结果。
# content_terms：content 裁剪区域 OCR 文本中应包含的字段（用于验证没裁错位置）。
# ---------------------------------------------------------------------------
FIXTURES = [
    {
        'file': '超级福袋_1.png',
        'expected_seconds': 478,           # 07:58
        'content_terms': ['天鼎速干加固鱼护一个-A', '120人已参与'],
    },
    {
        'file': '超级福袋_2.png',
        'expected_seconds': 241,           # 04:01
        'content_terms': ['孟将钢刀鲢鳙碳素支架一个', '139人已参与'],
    },
]


def test_parse():
    print("\n=== 1) check_countdown 字符串解析回归 ===")
    passed = failed = 0
    for text, expected in PARSE_CASES:
        result = underlying_operations.check_countdown(None, text)
        if expected is None:
            # 期望解析失败：check_countdown 返回 False
            ok = (result is False)
            got = result
        else:
            ok = (isinstance(result, tuple) and result[0] == expected)
            got = result[0] if isinstance(result, tuple) else result
        status = "PASS" if ok else "FAIL"
        if ok:
            passed += 1
        else:
            failed += 1
        print(f"  [{status}] {text!r:50s} -> {got} (期望 {expected})")
    return passed, failed


def _ocr_text(image_np):
    ocr = PaddleOCR(use_angle_cls=True, lang='ch', show_log=False)
    res = ocr.ocr(image_np)
    if not res or not res[0]:
        return ''
    return ' '.join(line[1][0] for line in res[0])


def test_crop_regions():
    print("\n=== 2) 图片 fixture 端到端回归（裁剪区域 + OCR + 解析）===")
    passed = failed = 0
    for fx in FIXTURES:
        path = os.path.join(ROOT, 'test', fx['file'])
        img = Image.open(path).convert('RGB')

        # 倒计时区域：直接复用模块级常量（与 get_fudai_contain 同一条码路径）
        cr = df.FUDAI_COUNTDOWN_REGION
        cd_crop = img.crop((cr[0], cr[1] + Y_PIANYI, cr[2], cr[3] + Y_PIANYI))
        cd_text = _ocr_text(np.array(cd_crop))
        cd_result = underlying_operations.check_countdown(None, cd_text)
        cd_secs = cd_result[0] if isinstance(cd_result, tuple) else None

        # 内容区域：验证没裁错位置
        nr = df.FUDAI_CONTENT_REGION
        ct_crop = img.crop((nr[0], nr[1] + Y_PIANYI, nr[2], nr[3] + Y_PIANYI))
        ct_text = _ocr_text(np.array(ct_crop))
        missing = [t for t in fx['content_terms'] if t not in ct_text]

        cd_ok = (cd_secs == fx['expected_seconds'])
        ct_ok = (not missing)
        ok = cd_ok and ct_ok
        status = "PASS" if ok else "FAIL"
        if ok:
            passed += 1
        else:
            failed += 1
        print(f"  [{status}] {fx['file']}")
        print(f"        倒计时区域 OCR={cd_text!r} -> {cd_secs}s (期望 {fx['expected_seconds']}s) {'OK' if cd_ok else 'X'}")
        if missing:
            print(f"        内容区域缺失字段 {missing} | OCR={ct_text!r}")
        else:
            print(f"        内容区域字段齐全 | OCR={ct_text!r}")
    return passed, failed


def main():
    p1, f1 = test_parse()
    p2, f2 = test_crop_regions()
    total_passed = p1 + p2
    total_failed = f1 + f2
    print(f"\n=== 汇总：通过 {total_passed} / 失败 {total_failed} ===")
    sys.exit(1 if total_failed else 0)


if __name__ == '__main__':
    main()
