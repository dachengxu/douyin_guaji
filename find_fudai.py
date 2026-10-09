#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
福袋位置校准辅助脚本（优化版）
============================
思路：
    1) 先按福袋通常出现的区域裁剪一块「子图」(ROI)。福袋是直播间顶部悬浮的
       “福袋”小图标，横排位置随布局变动（实测参考坐标 x≈88/214/419），但纵向
       固定在顶部一小条里。先裁剪这一条，既缩小搜索范围、提速，又避免画面其他
       紫色 UI 元素造成误匹配。
    2) 参考 dyfd 项目「小图找大图」的思路，在子图里用两种方式定位福袋：
         - 多尺度模板匹配（TM_CCOEFF_NORMED），主方法，对低纹理小图标稳健；
         - SIFT 特征匹配（zhaotu 同款），作为交叉验证/兜底；
       定位逻辑封装在 locate_fudai()，供 check_have_fudai 直接复用。
    3) 输出 1080x2400 参考坐标系下的 x,y 及主色/色域建议，方便回填 check_have_fudai。
       生成标注图 pic/fudai_marked.png（绿框=ROI，红框+红点=福袋）。

用法：
    .venv/bin/python find_fudai.py [截图路径] [模板路径(可选)] [--roi x0,y0,x1,y1]
    截图默认： pic/screenshot.png
    模板默认： target_pic/ 下所有 fudai*.png（提交入库；pic/ 被 gitignore）
    ROI 默认： 0,250,1080,520  (1080x2400 参考系下的 x0,y0,x1,y1，可用 --roi 覆盖)
"""

import os
import sys

import cv2
import numpy as np

# douyin_guaji 的参考分辨率
REF_W, REF_H = 1080, 2400

# 福袋候选色域：同时兼容旧蓝色款与新紫粉色款（与 check_have_fudai 对齐）
CANDIDATE_COLORS = [
    {'r': (183, 194), 'g': (165, 193), 'b': (237, 248)},  # 旧蓝色款
    {'r': (186, 252), 'g': (126, 192), 'b': (176, 242)},  # 新紫粉色款（实测主色≈RGB(219,159,209)）
]

# 默认 ROI（参考系）：直播间顶部福袋悬浮条
DEFAULT_ROI_REF = (0, 250, 1080, 520)

# 多尺度模板匹配参数
SCALE_MIN, SCALE_MAX, SCALE_STEP = 0.4, 1.6, 0.05
MATCH_THRESHOLD = 0.6

# SIFT 匹配阈值（与 dyfd 一致：good_matches > 30）
SIFT_THRESHOLD = 30


def list_templates(pic_dir):
    out = []
    if not os.path.isdir(pic_dir):
        return out
    # 这些不是「福袋小图标」模板，仅作内容/倒计时面板识别用，定位时排除
    skip_kw = ("content", "countdown", "detail", "info", "mark", "crop", "temp")
    for fn in sorted(os.listdir(pic_dir)):
        if fn.startswith("fudai") and fn.endswith(".png"):
            low = fn.lower()
            if any(k in low for k in skip_kw):
                continue
            out.append(os.path.join(pic_dir, fn))
    return out


def match_template_multiscale(img, tpl):
    """多尺度模板匹配，返回 (score, (x, y, w, h)) 于 img 坐标系；未匹配返回 None。"""
    best = None
    th, tw = tpl.shape[:2]
    ih, iw = img.shape[:2]
    for s in np.arange(SCALE_MIN, SCALE_MAX, SCALE_STEP):
        sw, sh = int(tw * s), int(th * s)
        if sw < 8 or sh < 8 or sw >= iw or sh >= ih:
            continue
        t = cv2.resize(tpl, (sw, sh))
        try:
            res = cv2.matchTemplate(img, t, cv2.TM_CCOEFF_NORMED)
        except cv2.error:
            continue
        _, mx, _, ml = cv2.minMaxLoc(res)
        if best is None or mx > best[0]:
            best = (mx, ml, sw, sh)
    if best is None:
        return None
    mx, (x, y), sw, sh = best
    return mx, (x, y, sw, sh)


def zhaotu_array(large_arr, small_arr):
    """参考 dyfd 的 zhaotu：SIFT 特征匹配在 large_arr 中找 small_arr。
    返回 ((cx, cy), (bw, bh)) 于 large_arr 像素坐标；未命中返回 None。"""
    lg = cv2.cvtColor(large_arr, cv2.COLOR_BGR2GRAY)
    sg = cv2.cvtColor(small_arr, cv2.COLOR_BGR2GRAY)
    sift = cv2.SIFT_create()
    kp1, des1 = sift.detectAndCompute(sg, None)
    kp2, des2 = sift.detectAndCompute(lg, None)
    if des1 is None or des2 is None or len(des1) < 4 or len(des2) < 4:
        return None
    bf = cv2.BFMatcher()
    try:
        matches = bf.knnMatch(des1, des2, k=2)
    except cv2.error:
        return None
    good = []
    for m_n in matches:
        if len(m_n) < 2:
            continue
        m, n = m_n
        if m.distance < 0.75 * n.distance:
            good.append(m)
    if len(good) <= SIFT_THRESHOLD:
        return None
    src = np.float32([kp1[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
    dst = np.float32([kp2[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)
    M, mask = cv2.findHomography(src, dst, cv2.RANSAC, 5.0)
    if M is None:
        return None
    h, w = sg.shape
    pts = np.float32([[0, 0], [0, h - 1], [w - 1, h - 1], [w - 1, 0]]).reshape(-1, 1, 2)
    d = cv2.perspectiveTransform(pts, M)
    rect = cv2.minAreaRect(d)
    (cx, cy), (bw, bh), _ = rect
    return (cx, cy), (bw, bh)


def in_color_range(b, g, r):
    """b,g,r 为 opencv 的 BGR 顺序；CANDIDATE_COLORS 按 R,G,B 定义。"""
    return any(
        cr['r'][0] <= r <= cr['r'][1] and
        cr['g'][0] <= g <= cr['g'][1] and
        cr['b'][0] <= b <= cr['b'][1]
        for cr in CANDIDATE_COLORS
    )


def locate_fudai(img, pic_dir, roi_ref=DEFAULT_ROI_REF, match_threshold=MATCH_THRESHOLD):
    """在截图中定位福袋图标，返回定位结果 dict 或 None。

    逻辑：先按参考坐标裁出顶部 ROI 子图，再对每张 fudai*.png 图标模板做多尺度模板匹配，
    取最高分且达阈值者；模板未达阈值时退回 SIFT 特征匹配（dyfd 思路）作为兜底。
    check_have_fudai 与本脚本共用此函数，避免重复实现。

    Args:
        img: BGR ndarray 或截图路径字符串
        pic_dir: 模板所在目录
        roi_ref: ROI 参考坐标 (x0,y0,x1,y1)，1080x2400 系
        match_threshold: 模板匹配置信阈值
    Returns:
        dict: {'x_ref','y_ref','w_ref','h_ref'(参考系中心/尺寸),
               'score','method','bbox'(全图像素 x,y,w,h),'roi'(全图 ROI 像素)}
        或 None（未定位）
    """
    if isinstance(img, str):
        img = cv2.imread(img)
    if img is None:
        return None
    ih, iw = img.shape[:2]
    ratio_x, ratio_y = iw / REF_W, ih / REF_H
    x0 = int(roi_ref[0] * ratio_x)
    y0 = int(roi_ref[1] * ratio_y)
    x1 = min(iw, int(roi_ref[2] * ratio_x))
    y1 = min(ih, int(roi_ref[3] * ratio_y))
    if x1 <= x0 or y1 <= y0:
        return None
    sub = img[y0:y1, x0:x1]

    templates = list_templates(pic_dir)
    if not templates:
        return None

    best_tpl = None   # (score, fx, fy, fw, fh, name)  全图像素坐标
    best_sift = None  # ((fcx, fcy), (bw, bh), name)
    for tpl_path in templates:
        tpl = cv2.imread(tpl_path)
        if tpl is None:
            continue
        r = match_template_multiscale(sub, tpl)
        if r is not None:
            score, (sx, sy, sw, sh) = r
            fx, fy, fw, fh = sx + x0, sy + y0, sw, sh
            if best_tpl is None or score > best_tpl[0]:
                best_tpl = (score, fx, fy, fw, fh, os.path.basename(tpl_path))
        rect = zhaotu_array(sub, tpl)
        if rect is not None:
            (cx, cy), (bw, bh) = rect
            if best_sift is None:
                best_sift = ((cx + x0, cy + y0), (bw, bh), os.path.basename(tpl_path))

    if best_tpl and best_tpl[0] >= match_threshold:
        _, x, y, w, h, name = best_tpl
        method = f"模板匹配({name})"
        score = best_tpl[0]
    elif best_sift is not None:
        (fcx, fcy), (bw, bh), name = best_sift
        x, y, w, h = int(fcx - bw / 2), int(fcy - bh / 2), int(bw), int(bh)
        method = f"SIFT({name})"
        score = 0.0
    else:
        return None

    cx, cy = x + w // 2, y + h // 2
    return {
        'x_ref': cx / ratio_x, 'y_ref': cy / ratio_y,
        'w_ref': w / ratio_x, 'h_ref': h / ratio_y,
        'score': score, 'method': method,
        'bbox': (x, y, w, h), 'roi': (x0, y0, x1, y1),
    }


def main():
    args = sys.argv[1:]
    img_path = None
    tpl_arg = None
    roi_ref = DEFAULT_ROI_REF
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--roi":
            try:
                roi_ref = tuple(int(v) for v in args[i + 1].split(","))
                assert len(roi_ref) == 4
            except Exception:
                print("[错误] --roi 需为 x0,y0,x1,y1 四个整数")
                sys.exit(1)
            i += 2
        elif img_path is None:
            img_path = a
            i += 1
        elif tpl_arg is None:
            tpl_arg = a
            i += 1
        else:
            i += 1

    base_dir = os.path.dirname(os.path.abspath(__file__))
    if img_path is None:
        img_path = os.path.join(base_dir, "pic", "screenshot.png")
    pic_dir = os.path.join(base_dir, "pic")          # 截图与标注图输出目录（运行时生成，gitignore）
    tpl_dir = os.path.join(base_dir, "target_pic")   # 福袋图标匹配模板目录（提交入库）

    if not os.path.exists(img_path):
        print(f"[错误] 截图不存在: {img_path}")
        sys.exit(1)

    img = cv2.imread(img_path)
    if img is None:
        print(f"[错误] 截图读取失败: {img_path}")
        sys.exit(1)
    ih, iw = img.shape[:2]
    ratio_x, ratio_y = iw / REF_W, ih / REF_H
    print(f"截图: {img_path}  ({iw} x {ih})  换算比例 x={ratio_x:.3f} y={ratio_y:.3f}")

    # 打印所用模板，便于核对
    templates = [tpl_arg] if tpl_arg else list_templates(tpl_dir)
    templates = [t for t in templates if t and os.path.exists(t)]
    print(f"模板: {[os.path.basename(t) for t in templates]}")

    # 定位福袋（ROI 裁剪 + 多尺度模板匹配 + SIFT 交叉验证），逻辑复用 locate_fudai()
    res = locate_fudai(img, tpl_dir, roi_ref=roi_ref, match_threshold=MATCH_THRESHOLD)
    if res is None:
        x0, y0, x1, y1 = (int(roi_ref[0] * ratio_x), int(roi_ref[1] * ratio_y),
                          min(iw, int(roi_ref[2] * ratio_x)), min(ih, int(roi_ref[3] * ratio_y)))
        out = img.copy()
        cv2.rectangle(out, (x0, y0), (x1, y1), (0, 255, 0), 2)
        cv2.imwrite(os.path.join(pic_dir, "fudai_marked.png"), out)
        print("[结果] 未定位到福袋：模板/SIFT 均未命中。可尝试放宽 --roi，或补充模板 fudai*.png")
        print(f"[标注图] 已生成: {os.path.join(pic_dir, 'fudai_marked.png')}（绿框=ROI）")
        sys.exit(2)

    x, y, w, h = res['bbox']
    x0, y0, x1, y1 = res['roi']
    cx, cy = x + w // 2, y + h // 2
    rx, ry, rw, rh = res['x_ref'], res['y_ref'], res['w_ref'], res['h_ref']
    print(f"[位置定位] 采用方法: {res['method']}  score={res['score']:.3f}")
    print(f"[参考坐标 {REF_W}x{REF_H}] 中心 x≈{rx:.1f} y≈{ry:.1f}  尺寸≈{rw:.0f}x{rh:.0f}")

    # ---- 5) 颜色分析：以福袋主色为准 ----
    cy0, cy1 = y + h // 4, y + 3 * h // 4
    cx0, cx1 = x + w // 4, x + 3 * w // 4
    patch = img[cy0:cy1, cx0:cx1]
    mb, mg, mr = [int(patch[:, :, i].mean()) for i in range(3)]
    med = np.array([mb, mg, mr], dtype=int)
    m = 25
    sx0, sy0 = max(0, x - m), max(0, y - m)
    sx1, sy1 = min(iw, x + w + m), min(ih, y + h + m)
    region = img[sy0:sy1, sx0:sx1]
    diff = np.abs(region.astype(int) - med)
    cluster = (diff <= 30).all(axis=2)
    if cluster.sum() > 20:
        lo = med - 30
        hi = med + 30
        sugg_r = (int(max(0, lo[2] - 3)), int(min(255, hi[2] + 3)))
        sugg_g = (int(max(0, lo[1] - 3)), int(min(255, hi[1] + 3)))
        sugg_b = (int(max(0, lo[0] - 3)), int(min(255, hi[0] + 3)))
    else:
        sugg_r = sugg_g = sugg_b = None
    in_orig = in_color_range(mb, mg, mr)
    print(f"[实际主色] BGR=({mb},{mg},{mr})  RGB=({mr},{mg},{mb})")
    print(f"[色域检查] 福袋主色{'落入' if in_orig else '未落入'}候选色域")
    if sugg_r:
        print(f"[建议色域] color_ranges={{'r':{sugg_r}, 'g':{sugg_g}, 'b':{sugg_b}}}")

    # ---- 6) 建议回填值 ----
    if cluster.sum() > 20:
        row_hits = cluster.sum(axis=1)
        best_row = int(np.argmax(row_hits)) + sy0
        ys_c, xs_c = np.where(cluster)
        x_suggest = int(round(((xs_c + sx0).mean()) / ratio_x))
        y_suggest = int(round(best_row / ratio_y))
    else:
        x_suggest, y_suggest = int(round(rx)), int(round(ry))
    base_suggest = int(round(x_suggest - 43))
    print("---- 建议回填到 check_have_fudai ----")
    print(f"  y  (固定行 403)        ≈ {y_suggest}")
    print(f"  base (x band 偏移)     ≈ {base_suggest}   "
          f"# 使 range(34+base,52+base) 覆盖 x≈{x_suggest}")
    print(f"  color_ranges 需更新?   {'否' if in_orig else '是（用上面[建议色域]）'}")

    # ---- 7) 标注图 ----
    out = img.copy()
    cv2.rectangle(out, (x0, y0), (x1, y1), (0, 255, 0), 2)      # 绿框=ROI
    cv2.rectangle(out, (x, y), (x + w, y + h), (0, 0, 255), 3)  # 红框=福袋
    cv2.circle(out, (cx, cy), max(5, int(min(w, h) * 0.15)), (0, 0, 255), -1)
    if cluster.sum() > 20:
        out[sy0 + ys_c, sx0 + xs_c] = (0, 255, 0)               # 绿点=福袋主色像素
    out_path = os.path.join(pic_dir, "fudai_marked.png")
    cv2.imwrite(out_path, out)
    print(f"[标注图] 已生成: {out_path}  （绿框=ROI，红框=福袋，绿点=福袋主色像素）")


if __name__ == "__main__":
    main()
