# -*- coding: utf-8 -*-
"""干什么：dsh-draw-meme 的推理脚本——本地跑 SDXL + 大肥鱼 LoRA 出一张表情包，
         自动压暗、自动贴中文、存进指定目录。不需要 ComfyUI。

用法：python draw.py --action "动作描述" --count 1 --size 832 --out 目录 [--text 台词] [--dim 0.96]
      底模：默认读环境变量 DSH_DRAW_BASE；没设就用 HF 上的 animagine-xl-4.0
      LoRA：默认用插件自带的 models/dafeiyu_v17.safetensors（可用 DSH_DRAW_LORA 覆盖）

产物：在 --out 目录下生成 <时间戳>_<序号>.png，并向 stdout 打印 `IMG:<绝对路径>`
依赖：首次运行自动 pip install（torch/diffusers/accelerate/Pillow/numpy/safetensors）
日志：全部打到 stdout/stderr，调用方（host.js）收走
怎么撤：删生成的 png；依赖装在用户目录（~/.dsh/draw-meme/venv），删那个目录即可
"""
import argparse
import os
import sys
import time

# 必须在 import huggingface_hub/diffusers 之前定下来，否则镜像不生效
os.environ["HF_ENDPOINT"] = os.environ.get("DSH_HF_ENDPOINT") or "https://hf-mirror.com"

# 临时目录：在 DSH 沙箱里 %TEMP% 经常写不进去（Permission denied），
# diffusers 一调 tempfile 就炸。自己试几个位置，选第一个真能写的。
import tempfile as _tempfile

for _d in [
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tmp"),
    os.path.join(os.path.expanduser("~"), ".dsh", "draw-meme", "tmp"),
    os.path.join(os.path.expanduser("~"), ".dsh", "tmp"),
]:
    try:
        os.makedirs(_d, exist_ok=True)
        _probe = os.path.join(_d, "_probe_write")
        with open(_probe, "wb") as _f:
            _f.write(b"ok")
        os.remove(_probe)
        _tempfile.tempdir = _d
        break
    except Exception:
        continue

# ── 提示词拆成三段 ──────────────────────────────────────────────
# CLIP 的文本编码只有 77 个位置，超了不报错、直接从尾部砍。
# v17 那套原文实测 137 token，被砍掉的正好是「鲸鳍耳、白底、动作」——所以动作永远出不来。
# 解法：拆成几段分别编码，再把 embedding 拼起来（UNet 的 cross-attention 不限制长度）。
# 发色只写发根、不写发梢，实测能让发梢自然贴近发根（写「lighter tips」发梢会漂成亮蓝）。
段1 = ("dafeiyu, 1girl, solo, chibi, (deep royal blue hair:1.45), long wavy hair, "
      "one curved ahoge, (2.2 head tall:1.3)")
段2 = ("deep royal blue eyes, big round eyes, pale pinkish skin, white frilled maid headdress, "
      "very dark navy dress, pure white apron, whale tail, white and blue fin ear ornaments")
段3前 = ("pure white background, soft watercolor anime style, sticker, "
        "(upper body portrait, hands visible:1.3), (exaggerated expression:1.35), ")
段3后 = ", dynamic pose"
# 负面必须保持 v17 原版这一串，一个字都别加 —— 实测加任何权重词都会把模型推向「表情包合集」
负面 = (
    "3d, render, realistic, photo, glossy, harsh contrast, multiple girls, text, letters, watermark, "
    "lowres, yellow skin, tanned skin, warm skin tone, bad anatomy, extra limbs, nsfw, cropped head, "
    "(white hair tips:1.3), (very light blue hair tips:1.3)"
)
构图 = "(upper body portrait, hands visible:1.3)"
夸张 = "(exaggerated expression:1.35)"
步数 = 28
CFG = 5.5
LORA强度 = 0.8
改图幅度 = 0.62  # img2img 的 strength：0.58~0.65 是最稳的区间，低了改不动表情，高了发色会飘
默认底模 = "cagliostrolab/animagine-xl-4.0"


def 确保依赖():
    """首次运行时自动装依赖。返回 None 表示 OK，否则返回错误说明。"""
    缺 = []
    for 模块, 包 in [
        ("torch", "torch"), ("diffusers", "diffusers"), ("accelerate", "accelerate"),
        ("PIL", "Pillow"), ("numpy", "numpy"), ("safetensors", "safetensors"), ("peft", "peft"),
    ]:
        try:
            __import__(模块)
        except ImportError:
            缺.append(包)
    if not 缺:
        return None
    print("缺依赖：" + ", ".join(缺), flush=True)
    print("正在装（第一次会慢，后面就不用装了）……", flush=True)
    import subprocess
    命令 = [sys.executable, "-m", "pip", "install", "--quiet"] + 缺
    # pip 每次装包前都会去拉 https://pypi.org/simple/pip/ 自检新版本，
    # 那页有好几 MB，网络一般的时候能卡好几分钟，看起来像死掉（本机实测）。
    # 关掉自检 + 禁交互，装包就是几秒钟的事。
    env = os.environ.copy()
    env["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
    env["PIP_NO_INPUT"] = "1"
    try:
        __import__("pip")
    except ImportError:
        subprocess.check_call([sys.executable, "-m", "ensurepip", "--upgrade"])
    try:
        subprocess.check_call(命令, env=env)
    except Exception as e:
        return "装依赖失败：%s\n请手动跑：%s" % (e, " ".join(命令))
    return None


def _是SDXL(路径):
    """读 safetensors header，看 UNet 的 cross_attention_dim 是不是 2048。"""
    try:
        from safetensors import safe_open
        with safe_open(路径, framework="pt") as f:
            for k in f.keys():
                if "attn2.to_k.weight" in k:
                    return f.get_slice(k).get_shape()[1] == 2048
    except Exception:
        pass
    return False


def 建管道(底模, 设备, 类型, 模型根, lora):
    import torch
    from diffusers import StableDiffusionXLPipeline, StableDiffusionPipeline
    本地 = os.path.isfile(底模)
    缓存 = os.path.join(模型根, "hf")
    os.makedirs(缓存, exist_ok=True)
    if 本地 and _是SDXL(底模):
        print("loading local SDXL checkpoint ...", flush=True)
        管道 = StableDiffusionXLPipeline.from_single_file(底模, torch_dtype=类型)
    elif 本地:
        print("loading local SD1.5 checkpoint ...", flush=True)
        管道 = StableDiffusionPipeline.from_single_file(
            底模, torch_dtype=类型, safety_checker=None, requires_safety_checker=False
        )
    else:
        print("loading %s from HF (first run downloads) ..." % 底模, flush=True)
        try:
            管道 = StableDiffusionXLPipeline.from_pretrained(底模, torch_dtype=类型, cache_dir=缓存)
        except Exception:
            管道 = StableDiffusionPipeline.from_pretrained(
                底模, torch_dtype=类型, cache_dir=缓存, safety_checker=None, requires_safety_checker=False
            )
    管道 = 管道.to(设备)
    # 采样器必须和 ComfyUI 那套对齐（euler_ancestral + normal），不然底色和画风都跑偏
    try:
        from diffusers import EulerAncestralDiscreteScheduler
        管道.scheduler = EulerAncestralDiscreteScheduler.from_config(管道.scheduler.config)
    except Exception as e:
        print("scheduler 切换失败，用默认的：%s" % e, flush=True)

    # SDXL 的 VAE 在 fp16 下解码会溢出（整图发灰发紫），让它解码时自动 upcast 到 fp32
    try:
        管道.vae.config.force_upcast = True
    except Exception:
        pass
    if lora and os.path.exists(lora):
        print("loading LoRA %s ..." % os.path.basename(lora), flush=True)
        try:
            管道.load_lora_weights(lora)
            管道.fuse_lora(lora_scale=LORA强度)
            管道.unload_lora_weights()
        except Exception as e:
            print("LoRA 加载失败，出不带 LoRA 的图：%s" % e, flush=True)
    else:
        print("没找到 LoRA，出的是底模原图（会不像大肥鱼）", flush=True)
    管道.set_progress_bar_config(disable=True)
    return 管道


def _能写目录(d):
    try:
        os.makedirs(d, exist_ok=True)
        p = os.path.join(d, "_write_probe")
        with open(p, "wb") as f:
            f.write(b"ok")
        os.remove(p)
        return True
    except Exception:
        return False


def _挑输出目录(首选, 插件根):
    """返回第一个真能写的目录；都不行就返回首选（让它把错误报出来）"""
    候选 = [
        首选,
        os.path.join(插件根, "out"),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "out"),
        # 兜底必须落到 ~/.dsh 以外：插件装在 ~/.dsh/profiles/... 下，
        # 子进程对 ~/.dsh 常常是只读的，上面几个候选会全军覆没。
        os.path.join(_tempfile.gettempdir(), "dsh-draw-meme-out"),
        os.getcwd(),
    ]
    for d in 候选:
        if d and _能写目录(d):
            if os.path.abspath(d) != os.path.abspath(首选):
                print("提示：%s 写不了，改存到 %s" % (首选, d), flush=True)
            return d
    return 首选


_管道缓存 = {}


def 取管道(底模, 设备, 类型, 模型根, lora):
    """同一个进程里只加载一次模型。常驻 worker 靠这个把每张图压到 8~30 秒。"""
    key = (底模, 设备, lora)
    e = _管道缓存.get(key)
    if e is None:
        e = {"txt2img": 建管道(底模, 设备, 类型, 模型根, lora), "i2i": None}
        _管道缓存[key] = e
    return e



def 拼提示(管道, 段们, 负面文本, 设备):
    """把几段提示词分别编码，再把 embedding 拼起来，绕过 CLIP 的 77 token 上限。"""
    import torch
    正嵌入们, 池嵌入们 = [], []
    for 段 in 段们:
        e, _, pe, _ = 管道.encode_prompt(
            prompt=段, prompt_2=None, device=设备, num_images_per_prompt=1,
            do_classifier_free_guidance=False, negative_prompt=None,
        )
        正嵌入们.append(e)
        池嵌入们.append(pe)
    正 = torch.cat(正嵌入们, dim=1)
    池 = sum(池嵌入们) / len(池嵌入们)
    负e, _, 负池, _ = 管道.encode_prompt(
        prompt=负面文本, prompt_2=None, device=设备, num_images_per_prompt=1,
        do_classifier_free_guidance=False, negative_prompt=None,
    )
    return 正, 池, 负e.repeat(1, len(段们), 1), 负池


def 出图(动作, 数量, 边长, 输出目录, 压暗, 台词, 种子=None, 基底图=None):
    错误 = 确保依赖()
    if 错误:
        print(错误, file=sys.stderr, flush=True)
        return 1
    import torch
    import numpy as np
    from PIL import Image

    模型根 = os.path.join(os.path.expanduser("~"), ".dsh", "draw-meme")
    插件根 = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    底模 = os.environ.get("DSH_DRAW_BASE") or 默认底模
    lora = os.environ.get("DSH_DRAW_LORA") or os.path.join(插件根, "models", "dafeiyu_v17.safetensors")
    设备 = "cuda" if torch.cuda.is_available() else "cpu"
    类型 = torch.float16 if 设备 == "cuda" else torch.float32

    print("台词：%r" % (台词 or ""), flush=True)
    if not (台词 or "").strip():
        print("注意：没传台词，这张图上不会有字", flush=True)
    缓存 = 取管道(底模, 设备, 类型, 模型根, lora)
    管道 = 缓存["txt2img"]
    基种子 = int(种子) if 种子 is not None and str(种子) != "" else int(time.time()) % 1000000

    底图 = None  # 不再走 img2img：重绘会磨掉清晰度，改表情靠提示词就够

    # 输出目录：给的那个不一定能写（这台机的 ~/.dsh 是只读 ACL），
    # 写不了就往插件边上退，最后退到当前目录，保证图一定落得下来。
    输出目录 = _挑输出目录(输出目录, 插件根)
    戳 = time.strftime("%Y%m%d-%H%M%S")
    for i in range(数量):
        # 三段顺序是踩出来的：
        #   1) 动作/表情放最前 —— 保证一定生效，不然「笑」会出成面瘫
        #   2) 肤色紧跟 —— 开 1.3 权重，且不能被白底词污染（白底词权重一高皮肤就惨白）
        #   3) 角色段垫后 —— 全部特征（发色/瞳色/头箍/裙子/围裙/鲸尾/鳍耳）挤进 77 token
        # 背景交给出图后的 floodfill 刷白，不在提示词里加权重。
        正段们 = [段1, 段2, 段3前 + 动作 + 段3后]
        正嵌入, 池嵌入, 负嵌入, 负池嵌入 = 拼提示(管道, 正段们, 负面, 设备)
        生成器 = torch.Generator(device="cpu").manual_seed(基种子 + i)
        图 = 管道(
                prompt_embeds=正嵌入, negative_prompt_embeds=负嵌入,
                pooled_prompt_embeds=池嵌入, negative_pooled_prompt_embeds=负池嵌入,
                width=边长, height=边长,
                num_inference_steps=步数, guidance_scale=CFG, generator=生成器,
            ).images[0]
        # 压暗之前先把背景刷白（只用 Pillow 自带的 floodfill，不加任何依赖）
        图 = 刷白背景(图)
        # 压暗 0.96（纯白背景跳过，别把白的压成灰的）
        数组 = np.array(图.convert("RGB")).astype(np.float32)
        纯白 = (数组[..., 0] >= 250) & (数组[..., 1] >= 250) & (数组[..., 2] >= 250)
        数组[~纯白] *= 压暗
        图 = Image.fromarray(np.clip(数组, 0, 255).astype(np.uint8))
        if 台词:
            图 = 贴字(图, 台词)
        路径 = os.path.join(输出目录, f"{戳}_{i+1}.png")
        图.save(路径)
        print("IMG:" + os.path.abspath(路径), flush=True)
    return 0


def 刷白背景(图, 阈值=42):
    """从边缘泛洪，把连通的浅色背景刷成纯白。

    - 只用 Pillow 自带的 ImageDraw.floodfill，不引入 opencv / scipy
    - 背景太暗（夜景、星空）就不动，免得刷出一块一块白边
    - 种子点先做「纯色区」检查，落在角色身上就不刷（防止把脸刷没）
    - 刷完白色占比超过 92% 说明吃太多了，退回原图
    """
    from PIL import ImageDraw
    import numpy as np

    图 = 图.convert("RGB")
    数组 = np.array(图)
    h, w = 数组.shape[:2]
    四角 = np.array([数组[0, 0], 数组[0, w - 1], 数组[h - 1, 0], 数组[h - 1, w - 1]], dtype=np.float32)
    中位 = np.median(四角, axis=0)
    # 只有「蓝背景板」不刷：它跟深藏青裙子颜色太近，泛洪会连衣服一起吃掉。
    # 灰色、米色、浅色背景一律刷。
    if 中位[2] > 中位[0] + 40 and 中位[2] > 100:
        return 图

    def 纯色(x, y, r=4, tol=12):
        x0, y0 = max(0, x - r), max(0, y - r)
        块 = 数组[y0:y + r + 1, x0:x + r + 1].reshape(-1, 3).astype(np.float32)
        return 块.std(axis=0).max() < tol

    点们 = [(1, 1), (w - 2, 1), (1, h - 2), (w - 2, h - 2)]
    for t in (0.25, 0.5, 0.75):
        x, y = int(w * t), int(h * t)
        点们 += [(x, 2), (x, h - 3), (2, y), (w - 3, y)]

    刷 = 图.copy()
    for 点 in 点们:
        if not 纯色(*点):
            continue
        try:
            ImageDraw.floodfill(刷, 点, (255, 255, 255), thresh=int(阈值))
        except Exception:
            pass
    后 = np.array(刷)
    if float((后.min(axis=2) >= 250).mean()) > 0.92:
        return 图  # 吃太多，撤回
    return 刷


def 贴字(图, 文本):
    """深藏青字 + 白描边，写在下方，不扩画布、不垫白块"""
    from PIL import ImageDraw, ImageFont
    图 = 图.convert("RGBA")
    宽, 高 = 图.size
    字号 = max(16, int(宽 * 0.078))
    字体 = _找字体()
    if 字体 is None:
        return 图.convert("RGB")
    f = ImageFont.truetype(字体, 字号)
    d = ImageDraw.Draw(图)
    bb = d.textbbox((0, 0), 文本, font=f)
    for _ in range(6):
        if bb[2] - bb[0] <= 宽 * 0.86:
            break
        字号 = int(字号 * 0.9)
        f = ImageFont.truetype(字体, 字号)
        bb = d.textbbox((0, 0), 文本, font=f)
    x = (宽 - (bb[2] - bb[0])) // 2 - bb[0]
    y = int(高 * 0.845) - bb[1]
    d.text((x, y), 文本, font=f, fill=(30, 42, 96, 255),
           stroke_width=max(2, int(字号 * 0.1)), stroke_fill=(255, 255, 255, 255))
    return 图.convert("RGB")


def _找字体():
    候选 = [
        "C:/Windows/Fonts/msyhbd.ttc", "C:/Windows/Fonts/msyh.ttc",
        "/System/Library/Fonts/PingFang.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
    ]
    for p in 候选:
        if os.path.exists(p):
            return p
    return None


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--action", required=True)
    ap.add_argument("--count", type=int, default=1)
    ap.add_argument("--size", type=int, default=832)
    ap.add_argument("--out", required=True)
    ap.add_argument("--text", default="")
    ap.add_argument("--dim", type=float, default=0.96)
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--base", default="", help="底图路径；默认空 = 纯 txt2img 从零画")
    a = ap.parse_args()
    # 默认不传底图：v17 的路子就是 txt2img，img2img 会把清晰度磨掉一层。
    sys.exit(出图(a.action, max(1, min(5, a.count)), a.size, a.out, a.dim, a.text, a.seed, a.base or None))
