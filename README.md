# dsh-draw-meme · 表情包绘制

装完就能让大肥鱼自己画表情包：一句话出图、自动压暗、自动加中文、直接进表情库。

**不需要 ComfyUI。** 只要有 DSH 和 Python 3.10+（带显卡最好，不带也能跑）。

---

## 装

### 方式一：用 dsh 命令（DSH 自带 CLI 的机器）

```bash
dsh plugin --profile <你的 profile 名> add <本目录的绝对路径>
```

它会自己把依赖项和 bundle 写进 profile，重启后生效。

### 方式二：手动装（没有 CLI 也能装）

1. 把**整个目录**（含 `models/`，里面有 170M 的 LoRA，别漏）复制到
   `~/.dsh/profiles/<profile 名>/node_modules/dsh-draw-meme/`
2. 打开同级的 `package.json`，加两处：

```json
{
  "dependencies": {
    "dsh-draw-meme": "file:<本目录的绝对路径>"
  },
  "dsh": {
    "profile": {
      "bundles": [ "...别动...", "dsh-draw-meme" ]
    }
  }
}
```

> 改之前先把 `package.json` 备份成 `package.json.bak-before-draw-meme`。

3. 重启 DSH

### 装完怎么确认

- 工具表里出现 `draw_meme`
- 设置页最下面出现「**表情包绘制**」（client 面板要**重启 + 刷新页面**才出来，只重启不刷新看不到）
- 直接对它说「画一张笑的」，能出图就是成了

### 首次使用会自动准备

1. `pip install` 缺的依赖（torch / diffusers / peft / Pillow / numpy / safetensors）
   —— 装进**运行它的那个 Python**，不是插件自带 venv
2. 从 `hf-mirror.com` 下 SDXL 底模 animagine-xl-4.0（约 7G，缓存到 `~/.dsh/draw-meme/hf/`）

第一次要等（下模型最慢），**别以为卡死了**——`~/.dsh/draw-meme/hf/` 里的文件在变大就说明在下载。
之后每次出图 8~10 秒（显卡）或 1~2 分钟（CPU）。

装到哪、下到哪、每种报错怎么修 → **`docs/首次使用-依赖与模型.md`**（接手前必读）

---

## 把它传到 GitHub（给维护者看的）

包里有个 163M 的 LoRA（`models/dafeiyu_v17.safetensors`），**超过 GitHub 单文件 100M 限制**，
直接 `git add` 会被拒。两种办法：

**1. Git LFS（推荐，别人 clone 就能用）**

```bash
git lfs install
git lfs track "models/*.safetensors"
git add .gitattributes
git add models/dafeiyu_v17.safetensors
```

**2. 放 Release 附件（仓库里不带权重）**

`.gitignore` 默认已经忽略 `models/*.safetensors`。把 LoRA 传成 Release 资产，
装机的人下载后放进 `models/` 就行 —— 少了它插件照样能跑，只是出来的不是大肥鱼。

`.gitignore` 已经排掉：`out/`、`tmp/`、`torchinductor_*/`、`__pycache__/`、`*.bak*`。

---

## 为什么提示词要拆三段

CLIP 的文本编码只有 **77 个位置**，超了不报错、直接从尾部砍掉。

v17 那套提示词实测 **137 个 token**，被砍掉的正好是最后那截——动作词和白底词，
所以以前的图「动作出不来、背景不白」。这不是模型不行，是提示词压根没进去。

现在的做法：拆成三段分别编码，再把 embedding 拼起来（UNet 的 cross-attention 不限制长度）。

- 发色只写发根，不写发梢（写「lighter tips」发梢会漂成亮蓝）
- 负面 embedding 复制三份补齐长度，不然 CFG 会报 shape 不匹配
- 纯 txt2img，不做 img2img 重绘（重绘会磨掉清晰度，还慢 6 倍）

单张约 4 秒（首张含加载模型约 28 秒），显存约 6.5G。

---

## 出图不稳定是正常的

为了保证速度（单张约 4 秒），模型跑在很紧的配置上，所以**废片是常态**：

| 现象 | 大约比例 | 说明 |
|---|---|---|
| 偶尔出现两只大肥鱼 | 6% 左右 | 训练集里拼图表情包太多 |
| 图上冒出假汉字乱码 | 10% 左右 | 训练集带字的图太多，负面词压不住 |
| 画得明显不对（脸崩、手崩） | 4% 左右 | 采样器差异 |

**建议一次出 2~3 张，挑一张用。** 不要只出 1 张就下结论。

真在意稳定性的话，就把 `数量` 给到 3，废片率会降到能接受的范围。

---

## 用

- 「画一张笑的」
- 「出三张：笑、震惊、鼓腮」
- 「画一张指人的，配文『说的就是你』」

参数就四个：`情绪`（中文情绪名或英文动作）、`台词`（可选）、`数量`（1~5）、`尺寸`（512/768/832/1024）。

---

## 它做什么

1. 拼提示词（角色段写死，表情动作段按你说的补）
2. 跑 SDXL + 内置的大肥鱼 LoRA（`models/dafeiyu_v17.safetensors`）出图
3. 整图压暗 0.96（纯白背景不动）
4. 贴上中文字（深藏青字 + 白描边，不垫白块、不扩画布）
5. 存进 `~/.dsh/meme-packs/dafeiyu-auto/memes/auto/`
   （那个目录写不进去时自动退到插件自己的 `out/`，宿主再搬回库，图不会丢）

---

## 可以改的配置

设置页「表情包绘制」里直接改，或者写 `~/.dsh/draw-meme.json`（改完立刻生效，不用重启）：

```json
{
  "python": "C:/Python312/python.exe",
  "base": "D:/models/animagine-xl-4.0.safetensors",
  "lora": "",
  "size": 832,
  "dim": 0.96,
  "seed": null
}
```

| 键 | 说明 |
|---|---|
| `python` | 指定解释器。留空 = 自动找（会跳过 Windows 商店的占位符） |
| `base` | 本地底模 `.safetensors`，或 HF 仓库名。留空 = 下 animagine-xl-4.0 |
| `lora` | 换 LoRA 时用。留空 = 用内置的大肥鱼 v17 |
| `size` | 出图边长，默认 832 |
| `dim` | 压暗系数，0.96 是定稿值；1 = 不压 |
| `seed` | 固定种子，想复现某张就填；留空 = 每次随机 |

也可以用环境变量 `DSH_PYTHON` / `DSH_DRAW_BASE` / `DSH_DRAW_LORA`。

---

## 提示词怎么写

见 **`docs/提示词指南.md`**：内置 14 个中文情绪名 + 20 条现成英文动作 + 六个必踩的坑。

想在 ComfyUI 里自己搭同样的流程，见 `docs/技能包.md`（底模、LoRA、步数、色号都在里面）。

---

## 做不到的地方

- 底模 7G 必须下，省不掉
- CPU 出一张 1~2 分钟
- **中文只能后期贴**，不能让模型写。SDXL 写中文必乱码，别试
- 训练集里带字的图不少，出图偶尔会冒假字/乱码 —— 已知限制，没根治（详见提示词指南坑 6）
- 要不要重启才生效：改 `python/draw.py` **不用**重启（每次出图新起进程）；改 `lib/host.js` 或 `client.js` **要**重启

---

## 卸载

1. 从 profile 的 `package.json` 里去掉 `dsh-draw-meme` 两处（dependencies 和 bundles）
2. 删掉 `node_modules/dsh-draw-meme/` 整个目录
3. 想连缓存一起清：删 `~/.dsh/draw-meme/`（模型 7G）和 `~/.dsh/draw-meme.json`
4. 出过的图在 `~/.dsh/meme-packs/dafeiyu-auto/`，想留就留着
