# CODEBUDDY.md

This file provides guidance to CodeBuddy Code when working with code in this repository.

## 项目简介

这是一个用 Python + ADB 控制安卓手机、在抖音直播间**自动挂机参与福袋/超级福袋抽奖**的脚本。
手机通过 USB 或 WiFi 连接电脑，脚本循环：发现福袋 → 打开详情 → 校验内容是否想要 →
读倒计时 → 到点参与 → 等待开奖 → 处理中奖/未中奖弹窗 → 切换直播间。

## 环境要求（重要）

- **必须用 Python 3.9**。仓库已用 `.python-version` 锁定 3.9，本地已建好 `.venv`（3.9.24）。
  不要用 3.10+，paddlepaddle 2.6.1 / numpy 1.26.4 在 3.9 上最稳。
- paddlepaddle 必须从官方 CPU 源安装（见 `requirements.txt` 顶部 `--extra-index-url`），
  PyPI 镜像常缺 2.6.1，装到 3.x 在缺 AVX2 的 CPU 上会 SIGILL 崩溃。
- 运行前手机需 `adb devices` 能识别（USB 调试开启，或 WiFi 直连）。

## 常用命令

```bash
# 激活环境并运行主程序（连上设备后）
source .venv/bin/activate
python douyin_guaji.py

# 不激活，直接用 venv 解释器
.venv/bin/python douyin_guaji.py

# 福袋图标位置校准辅助脚本（独立工具，不参与主流程）：
# 默认读取 pic/screenshot.png，在 pic/ 下所有 fudai*.png 模板中定位福袋
.venv/bin/python find_fudai.py
.venv/bin/python find_fudai.py pic/screenshot.png --roi 0,250,1080,520
```

没有测试框架、没有 lint 配置、没有构建步骤。验证方式就是连真机跑并用日志观察。

## 高层架构

三个核心文件，职责清晰分层：

### 1. `Underlying_Operations.py` — 底层设备/工具层（`underlying_operations` 类）
只做"与抖音业务无关的原子操作"，被上层调用：
- ADB 操作：`get_screenshot`（截图存 `pic/screenshot.png`）、`click`、`swipe`、`input_text`、`click_back`、
  `start_douyin_app` / `stop_douyin_app`、`select_device`（多设备选择）、`get_device_resolution`。
- 图像处理/OCR：`cut_pic`（按坐标裁剪，坐标在**参考坐标系**下，调用方自行加 `y_pianyi`）、
  `analyse_pic_word`（PaddleOCR 识别，可选二值化 `change_color=1/2`）、`extract_ocr_content`。
- 业务无关逻辑：`check_countdown`（把倒计时文本解析成秒数，见下方坑点）、`load_config` /
  `update_config_with_offsets`（读写 `config.json`）。

### 2. `douyin_fudai.py` — 业务编排/状态机（`fudai_analyse` 类）
整个挂机逻辑都在这里，入口是 `fudai_choujiang(device_id, needswitch, wait_minutes)`。
主循环（约 1187 行起）用一组**嵌套的 `handle_*` 函数**实现状态机：
`handle_night_time` / `handle_fudai_click` / `handle_fudai_content` / `handle_countdown` /
`handle_countdown_check` / `handle_after_draw` 等，每个返回 bool 表示"已处理并 continue"。
关键子流程：
- `check_have_fudai`：模板匹配在顶部 ROI 找福袋小图标，返回图标 x 坐标（或 None）。
- `get_fudai_contain(renwu)`：根据任务数 `renwu`(0/1/2/3) 裁剪福袋**内容区**和**倒计时区**并 OCR。
  区域坐标在 `task_params` 字典里，是参考坐标系、需叠加 `y_pianyi`。
- `check_contain`：拿内容文本和 `config.json` 的 `contains_want`/`contains_not_want` 比对，决定是否参与。
- `attend_choujiang` / `check_lucky_draw_result` / `get_reward`：参与、开奖结果判定、领奖。
- `find_y_offset`：自动测定状态栏高度偏移 `y_pianyi`（也支持从 config 的 `device_offsets` 直接读）。
- `get_device_resolution`：按实际分辨率算出 `resolution_ratio_x/y` 做坐标缩放。
- `Tee` 类：把 stdout 同时写控制台和按时间戳命名的 `.log` 文件（排查问题的依据）。

### 3. `find_fudai.py` — 独立校准工具（**不被主流程 import**）
用多尺度模板匹配 + SIFT 在截图的顶部 ROI 里定位福袋图标，输出参考坐标并生成 `pic/fudai_marked.png`。
仅在需要调整/验证 `check_have_fudai` 的模板和坐标时使用。

### 入口
`douyin_guaji.py` 极薄：构造 `fudai_guaji` → `guaji()` → 选设备 → 调 `fudai_analyse.fudai_choujiang(...)`。

## 坐标系与分辨率适配（改动坐标相关代码必读）

- 代码中大量**写死的像素坐标是基于参考分辨率 1080×2400**（`find_fudai.py` 里 `REF_W/REF_H`）。
- 适配不同手机靠两点：
  1. `get_device_resolution` 得到实际分辨率，算 `resolution_ratio_x/y` 缩放横纵。
  2. **状态栏高度偏移 `y_pianyi`**：顶部 UI 因状态栏高度不同而下移，`y_pianyi` 默认 `-50`，
     可由 `config.json` 的 `device_offsets`（设备序列号 → 偏移值）按设备覆盖。
- `cut_pic` / 各 `task_params` 的区域 y 值都需要在裁剪时手动 `+ self.y_pianyi`（见 `get_fudai_contain`）。

## 关键坑点 / 易错处

- **`check_countdown` 解析倒计时**：优先正则匹配 `分:秒`（兼容中英文冒号与空格），
  无冒号才退化按纯数字（4 位 `MMSS` 或 3 位 `MSS`）。分钟 <10 时（如 `4:30`）纯数字只有 3 位，
  老逻辑只认 4 位会误报"时间格式异常"。
- **倒计时区域偶尔截空/半截**：`handle_countdown` 已加兜底——倒计时区域 OCR 失败时，
  从 `fudai_content`（一定含 `已参与/MM:SS`）解析时间。改这块逻辑时务必保留兜底。
- **福袋类型差异**：普通福袋、超级福袋（`super_fudai` 模板）、钻石福袋判定逻辑不同；
  倒计时数字在"福袋奖品（免费购） 已参与/MM:SS 后开奖"标题行右上角，不是"完成任务抽取奖品"小标题。
- 福袋图标匹配模板在 `target_pic/`（fudai_01/02.png，提交入库）；`pic/` 被 gitignore（仅运行时截图/裁剪产物）。超级福袋模板也在 `target_pic/`。
- 改完坐标/解析相关代码，建议用现有 `pic/screenshot.png` + 离线 OCR 验证，再连真机跑一轮看日志。

## 配置（`config.json`）

- `contains_want` / `contains_not_want`：福袋内容关键词，决定要不要参与该福袋。
- `video_comment`：刷活跃时随机发的评论文案。
- `device_offsets`：设备序列号 → `y_pianyi` 偏移值（新设备首次识别成功后会写回这里）。
