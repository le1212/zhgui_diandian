<div align="center">

<img src="assets/app_icon.png" alt="点点" width="120" height="120" style="border-radius: 24px;">

# 点点 · 桌面连点器

一款真正的 Windows 桌面自动化工具，基于 Win32 SendInput 控制真实鼠标键盘

<br>

![Python](https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white)
![Platform](https://img.shields.io/badge/Platform-Windows-0078D4?logo=windows&logoColor=white)
![License](https://img.shields.io/badge/License-MIT-green)
![Version](https://img.shields.io/badge/Version-1.0.0-orange)
![Build](https://img.shields.io/badge/Build-PyInstaller%20%2B%20InnoSetup-blueviolet)

</div>

---

## 功能特性

### 核心自动化
- **单点连点**：固定坐标循环点击，支持左键 / 右键 / 中键
- **多点任务**：按顺序执行多个位置，可混合各类步骤
- **桌面录制**：基于 Raw Input 录制真实鼠标点击、滚轮和键盘操作
- **图像定位点击**：框选屏幕区域作为模板，运行时模板匹配并点击
- **条件判断**：如果图像存在 / 不存在则跳过后续步骤，支持分支逻辑
- **文字输入**：通过剪贴板粘贴输入中英文内容
- **键盘操作**：下拉选择常用按键（按下 / 松开），支持 38 个常用键
- **鼠标滚轮**：向上 / 向下滚动，可设置滚动量

### 步骤编辑
- 步骤类型：点击、等待、滚轮、按键、文字输入、图像定位、条件判断
- 操作：添加、编辑、复制、上下移动、拖拽排序、启用 / 禁用、删除、清空
- 通用撤销：`Ctrl+Z` 支持所有操作（最多 50 步）
- 右键快捷菜单：快速添加各类步骤、批量操作
- 多选批量操作：同时复制、启用 / 停用、删除多个步骤

### 任务管理
- 多任务管理：新建、复制、重命名、切换
- 回收站：删除任务进入回收站，可恢复或永久删除
- 自动保存：任务实时保存到本地
- 导出 / 导入：任务以 JSON 格式导出 / 导入，便于分享和备份
- 数据持久化：任务、截图模板、步骤截图均持久化存储

### 运行控制
- 全局快捷键：`F2` 捕获坐标，`F6` 运行 / 停止，`F7` 暂停 / 继续，`Esc` 紧急停止
- 启动倒计时：可设置倒计时秒数，避免误触
- 循环次数：可设置固定次数或无限循环
- 随机间隔：可设置随机间隔百分比，模拟人工操作
- 运行悬浮条：执行期间置顶控制条，暂停 / 停止操作
- 运行状态强化：运行按钮呼吸动画，实时显示当前步骤进度

### 用户体验
- 莫兰迪灰绿配色，护眼舒适
- 首次启动新手引导，三步上手
- 空状态大按钮，引导首次捕获
- Toast 轻提示，操作反馈即时
- 步骤截图预览：多点 / 录制模式下每步捕获截图，点击查看大图
- Per-Monitor DPI 感知，兼容多显示器和不同缩放比例

## 快速开始

### 方式一：绿色版（免安装）
直接双击 `点点.exe` 即可运行，无需安装 Python。

### 方式二：安装版
运行安装向导，按提示完成安装，可选创建桌面快捷方式和开始菜单项。

### 方式三：源码运行
```powershell
# 环境要求：Python 3.11+
pip install pillow numpy pywin32
python desktop_app.py
```

## 使用说明

### 基础流程
1. 选择模式（单点 / 多点 / 录制）
2. 按 `F2` 捕获目标位置（或点击工具栏按钮手动添加步骤）
3. 配置点击方式、间隔、次数等参数
4. 按 `F6` 开始运行，`Esc` 紧急停止

### 图像定位
1. 点击工具栏「图像」按钮
2. 在屏幕截图上框选要识别的区域（图标、文字等）
3. 运行时自动在全屏查找模板并点击中心
4. 双击步骤可调整匹配阈值和点击偏移

### 条件判断
1. 点击工具栏「如果」按钮
2. 框选目标图像区域
3. 设置条件（存在 / 不存在时继续）、不满足时跳过步数、匹配阈值
4. 运行时自动判断，不满足条件则跳过后续指定步数

### 快捷键
| 快捷键 | 功能 |
|--------|------|
| `F2` | 捕获鼠标当前位置 |
| `F6` | 开始 / 停止运行 |
| `F7` | 暂停 / 继续 |
| `Esc` | 紧急停止 |
| `Ctrl+Z` | 撤销上一步操作 |
| `Ctrl+D` | 复制选中步骤 |
| `Del` | 删除选中步骤 |

## 技术栈

- **语言**：Python 3.11+
- **GUI**：Tkinter（自定义组件，PIL 预渲染图标）
- **输入模拟**：Win32 `SendInput`（ctypes 调用）
- **录制**：Raw Input API（不安装全局 Hook，不阻塞输入链路）
- **图像匹配**：Pillow + NumPy（归一化互相关模板匹配）
- **打包**：PyInstaller（单文件）+ Inno Setup（安装向导）
- **数据存储**：JSON（原子写入 + 自动备份）

## 目录结构

```
liandianqi/
├── assets/                  # 应用资源（图标等）
├── dist/                    # 构建输出（绿色版 exe）
├── tests/                   # 单元测试
├── desktop_app.py           # 主程序（UI + 业务逻辑）
├── models.py                # 数据模型（Step / Task / TaskSettings）
├── task_repository.py       # 任务存储（JSON 持久化 + 备份 + 迁移）
├── image_locator.py         # 图像模板匹配
├── raw_input.py             # Raw Input 录制器
├── Diandian.spec            # PyInstaller 配置
├── setup.iss                # Inno Setup 安装脚本
├── AGENTS.md                # 项目协作规范
└── README.md                # 项目说明
```

## 构建

### 绿色版
```powershell
pip install pyinstaller
python -m PyInstaller Diandian.spec --noconfirm
# 输出：dist/点点.exe
```

### 安装版
```powershell
# 需先安装 Inno Setup 6
& "C:\Users\<用户名>\AppData\Local\Programs\Inno Setup 6\ISCC.exe" setup.iss
# 输出：安装向导安装包
```

### 图标更换
替换 `assets/app_icon.png`（建议 256×256 以上 PNG），重新生成 ico 后打包即可。

## 数据存储

默认数据目录：`%APPDATA%\Diandian\`

```
Diandian/
├── tasks.json               # 任务数据（schema v2，原子写入）
├── tasks.json.bak.*         # 最近 5 份自动备份
├── templates/               # 图像定位模板
├── thumbs/                  # 步骤截图预览
├── trash.json               # 回收站
└── .onboarded               # 新手引导完成标记
```

设置环境变量 `DIANDIAN_DATA_DIR` 可自定义数据目录，适合便携使用。

## 性能设计

- 录制器基于 Raw Input 消息驱动，不安装全局低级鼠标 Hook，不阻塞 Windows 输入链路
- 独立录制线程只在点击 / 滚轮 / 按键事件到达时读取坐标和窗口信息
- 工作线程不直接访问 Tk 控件，所有界面更新通过线程安全队列回到主线程
- 运行前在主线程生成不可变执行计划，运行期间不读取界面状态
- 预览区 resize 防抖，点阵预渲染，保证窗口拖动流畅

## 注意事项

- 如果目标软件以管理员身份运行，需要右键以管理员身份启动点点，否则 Windows 会阻止低权限进程向高权限窗口注入输入
- 图像定位建议选择边缘清晰、对比度高的区域（图标、文字等），避免选择大面积纯色或重复图案
- 条件判断的跳过步数指当前步骤之后连续跳过的步骤数量，包含被禁用的步骤

## License

[MIT](LICENSE)

## 作者

By **Zhgui**
