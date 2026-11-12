# -*- coding: utf-8 -*-
"""干什么：dsh-draw-meme 的推理脚本——本地跑 SDXL + 大肥鱼 LoRA 出一张表情包，
         自动压暗、自动贴中文、存进指定目录。不需要 ComfyUI。

用法：python draw.py --action "动作描述" --count 1 --size 832 --out 目录 [--text 台词] [--dim 0.96]
      底模：默认读环境变量 DSH_DRAW_BASE；没设就用 HF 上的 animagine-xl-4.0
      LoRA：默认用插件自带的 models/dafeiyu_v24.safetensors（可用 DSH_DRAW_LORA 覆盖）

产物：在 --out 目录下生成 <时间戳>_<序号>.png，并向 stdout 打印 `IMG:<绝对路径>`
依赖：首次运行自动 pip install（torch/Pillow/numpy/safetensors），comfy 运行时自带
日志：全部打到 stdout/stderr，调用方（host.js）收走
怎么撤：删生成的 png；依赖装在用户目录（~/.dsh/draw-meme/venv），删那个目录即可
"""
import argparse
import json
import os
import sys
import time

# 必须在 import comfy 之前定下来，否则镜像不生效
os.environ["HF_ENDPOINT"] = os.environ.get("DSH_HF_ENDPOINT") or "https://hf-mirror.com"

# 临时目录：在 DSH 沙箱里 %TEMP% 经常写不进去（Permission denied），
# comfy 一调 tempfile 就炸。自己试几个位置，选第一个真能写的。
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
# 2026-11-09 改：提示词写详细。CLIP 本身 77 token，但我们是分段编码再拼 embedding，
# 所以可以写长——每段各自不超过 77 token，拼起来总长度不受限。
# 每段职责单一，避免互相污染：外貌 / 服装 / 画风与构图 / （动作拼在第 4 段）。
段1 = ("dafeiyu, 1girl, solo, chibi proportions, oversized head, tiny body, "
       "(2.2 head tall:1.3), round face, fair warm pinkish skin, "
       "(solid royal blue hair:1.5), long thick wavy hair down to waist")
段2 = ("glossy hair strands with soft cel shading, uniform royal blue from root to tip, "
       "one single curved ahoge on top, big round deep royal blue eyes, "
       "gradient blue iris with a bright highlight, thick upper eyelashes, small nose, tiny mouth")
段3 = ("white frilled maid headdress with a small blue bow on one side, "
       "white and blue whale fin ear ornaments on both sides, one whale tail curling out from behind "
       "the body to the side, navy blue long sleeve maid dress, pure white apron with a tiny blue whale "
       "embroidered on it, gold trim only on cuffs and hem")
段4前 = ("pure white background, flat cel shading, clean dark lineart, crisp outline, "
         "centered composition, upper body portrait, both hands visible, ")
段4后 = ", dynamic lively pose, expressive face, high detail face"
负面前缀 = ()  # 占位，保留旧变量名不报错
负面 = (
    # 2026-11-12 补回实测那一串（原来只有多人/分屏，底板成灰、成蓝、带渐变都跟缺这些有关）
    "(worst quality:1.2), (low quality:1.2), blurry, "
    "(multiple girls:1.4), 2girls, 3girls, (split screen:1.3), (comic:1.3), (panel:1.2), "
    "(character sheet:1.4), (multiple views:1.4), (collage:1.2), "
    "(detailed background:1.4), scenery, outdoors, indoors, window, floor, "
    "(gradient background:1.3), (gray background:1.3), (vignette:1.2), shadow on ground, "
    "watermark, signature, username, text, logo"
    + os.environ.get("DSH_DRAW_NEG_EXTRA", "")
)

# 表情词表（2026-11-09 定）：一个中文情绪换成四到六个具体词——嘴型、眼睛、眉毛、手放哪、脸红、汗滴。
# 为什么不用「exaggerated expression」这种空话：实测权重加到 1.55 反而把脸画平，具体词才管用。
表情词表 = {
    "大笑":  "laughing out loud, (open mouth showing teeth:1.4), (eyes squeezed shut:1.3), head thrown back, blush",
    "笑死":  "laughing out loud, (open mouth showing teeth:1.4), (eyes squeezed shut:1.3), head thrown back, blush",
    "笑":    "smiling, (open mouth:1.2), happy eyes, blush",
    "鼓腮":  "pouting, (puffed cheeks:1.5), (angry eyebrows:1.35), crossed arms, looking away",
    "哼":    "pouting, (puffed cheeks:1.5), (angry eyebrows:1.35), crossed arms, looking away",
    "生气":  "angry, (furrowed brows:1.4), (puffed cheeks:1.3), clenched fists",
    "在吗":  "curious, head tilt, (raised eyebrows:1.3), (cat-like mouth:1.2), looking at viewer",
    "疑惑":  "confused, head tilt, (raised eyebrows:1.3), question mark, (cat-like mouth:1.2)",
    "困了":  "yawning, (:d mouth wide open:1.5), eyes closed tightly, head tilted back, hand covering mouth",
    "犯困":  "yawning, (:d mouth wide open:1.5), eyes closed tightly, head tilted back, hand covering mouth",
    "好耶":  "cheering, (open mouth:1.3), (arms up:1.3), sparkling eyes, happy",
    "开心":  "cheering, (open mouth:1.3), (arms up:1.3), sparkling eyes, happy",
    # 2026-11-12：原来写 hands on own cheeks（双手贴脸），SDXL 会画成四只手——
    # 脸颊两侧各一个拳头 + 身前又各一个。改成《拱手》后同种子实测只剩两只手。
    "害羞":  "shy, (heavy blush:1.5), hands clasped in front, (looking away:1.3), nervous smile",
    # hands up 也会多画手；改成单手举高，数量写死。
    "震惊":  "shocked, (eyes wide open:1.5), (mouth wide open:1.35), (sweat drop:1.2), one hand raised",
    "惊讶":  "surprised, (eyes wide open:1.4), (mouth wide open:1.3), one hand raised",
    "得意":  "smug, (smirk:1.4), (half-closed eyes:1.3), hand on hip, confident",
    "委屈":  "sad, (teary eyes:1.4), (trembling lips:1.2), (drooping eyebrows:1.3), holding back tears",
    "难过":  "sad, (teary eyes:1.4), (drooping eyebrows:1.3), looking down",
    "撒娇":  "shy, (heavy blush:1.4), (hands clasped together:1.3), (looking up at viewer:1.3), (pout:1.2), pleading eyes",
    "无语":  "speechless, (half-closed eyes:1.3), (flat mouth:1.2), (sweat drop:1.2), deadpan stare",
    "哈欠":  "yawning, (:d mouth wide open:1.4), (eyes closed tightly:1.3), hand covering mouth, tears in eyes",
    # 2026-11-12 补：插件面板用的 14 个情绪里有 7 个这边原先没有（会变成中文原文拼进提示词，
    # 模型听不懂）。补齐，都只用「一只」手，避免多手。
    "哭":    "crying, (tears streaming down:1.3), (open mouth wailing:1.2), eyes squeezed shut, trembling",
    "冒汗":  "nervous, (sweat drops on face:1.3), forced smile, (awkward eyes:1.2)",
    "指人":  "pointing at viewer, (one hand raised:1.3), confident, smirk",
    "比心":  "one hand making a finger heart gesture, happy, blush",
    "鼓掌":  "clapping hands happily, (open mouth smile:1.2), sparkling eyes",
    "捂脸":  "one hand covering face, embarrassed, (deep blush:1.3)",
    "思考":  "thinking, (one hand on chin:1.3), looking up, serious",
}

构图 = "(upper body portrait, hands visible:1.3)"
夸张 = "(exaggerated expression:1.35)"
步数 = int(os.environ.get("DSH_DRAW_STEPS", "20"))   # 2026-11-12 她定：默认 20 步（底图已跑 2 步，实际采样 18）
CFG = float(os.environ.get("DSH_DRAW_CFG", "5.5"))
LORA强度 = float(os.environ.get("DSH_DRAW_LORA_W", "1.0"))
改图幅度 = 0.62  # img2img 的 strength：0.58~0.65 是最稳的区间，低了改不动表情，高了发色会飘
# 2026-11-12 起固定走 comfy 后端，comfy 只认本地 safetensors 文件，
# 不能再给 HF 仓库名（"cagliostrolab/animagine-xl-4.0" 会 FileNotFoundError）。
默认底模名 = "animagine-xl-4.0.safetensors"
COMFY底模目录 = r"D:/ComfyUI_windows_portable_nvidia/ComfyUI_windows_portable/ComfyUI/models/checkpoints"


def 找底模(插件根, 模型根):
    """按优先级找一个能用的本地底模：环境变量 > 插件 models > 用户目录 > ComfyUI 安装目录。"""
    候选 = [os.environ.get("DSH_DRAW_BASE", "").strip(),
            os.path.join(插件根, "models", 默认底模名),
            os.path.join(模型根, "models", 默认底模名),
            os.path.join(COMFY底模目录, 默认底模名)]
    for 路径 in 候选:
        if 路径 and os.path.isfile(路径):
            return 路径
    return 候选[0] or 默认底模名




def 确保依赖(额外=None):
    """首次运行时自动装依赖。返回 None 表示 OK，否则返回错误说明。"""
    缺 = []
    要查 = [
        ("torch", "torch"),
        ("PIL", "Pillow"), ("numpy", "numpy"), ("safetensors", "safetensors"), ("peft", "peft"),
    ] + list(额外 or [])
    for 模块, 包 in 要查:
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





def 收表情权重(文本, 上限=None):
    """把表情词里的权重压下来（2026-11-12 她要求：部分表情太夸张）。

    默认上限 1.2；顺带清掉表情词表里混进去的 ":d" 之类脏字符（实测会拼成
    "(:d mouth wide open:1.5)" 这种无效标签）。上限可用 DSH_DRAW_EXPR_CAP 改。
    """
    import re
    try:
        上限 = float(os.environ.get("DSH_DRAW_EXPR_CAP", 上限 if 上限 is not None else 1.2))
    except ValueError:
        上限 = 1.2

    def 换(m):
        try:
            值 = float(m.group(2))
        except ValueError:
            return m.group(0)
        return "(%s:%s)" % (m.group(1), ("%.2f" % min(值, 上限)).rstrip("0").rstrip("."))

    # 先清脏字符再收权：像 "(:d mouth wide open:1.5)" 带冒号，先收权会匹配不上
    文本 = 文本.replace("(:d ", "(").replace("(:d", "(").replace(":d ", "")
    文本 = re.sub(r"\(([^()]+?):(-?[0-9.]+)\)", 换, 文本)
    return 文本




def _底图步数(底图):
    """这份底图潜变量是跑了几步的半成品。

    优先读底图旁边的 sidecar（同名 .json 里的 steps），
    其次读环境变量 DSH_DRAW_BASE_START，都没有就按 2 步算。
    """
    if 底图 and 底图.lower().endswith(".pt"):
        sidecar = os.path.splitext(底图)[0] + ".json"
        try:
            with open(sidecar, "r", encoding="utf-8") as f:
                值 = int(json.load(f).get("steps", 0))
            if 值 >= 1:
                return 值
        except Exception:
            pass
    try:
        return max(1, int(os.environ.get("DSH_DRAW_BASE_START", "2") or 2))
    except ValueError:
        return 2


def 出图(动作, 数量, 边长, 输出目录, 压暗, 台词, 种子=None, 基底图=None,
       字体路径="", 字号比=0.078, 描边比=0.10, 不贴字=False):
    # 2026-11-12：步数改成每次调用都读环境变量（以前写在模块级，进程内改不生效，
    # 插件设置页改了步数必须重启才吃到）。放在函数第一行，覆盖同名的模块级默认值。
    步数 = int(os.environ.get("DSH_DRAW_STEPS") or 20)
    动作 = 表情词表.get(str(动作).strip(), 动作)   # 认得的中文情绪换成具体表情词
    动作 = 收表情权重(动作)                      # 权重压到 1.2 以内，压掉过夸张的写法
    错误 = 确保依赖()
    if 错误:
        print(错误, file=sys.stderr, flush=True)
        return 1
    import torch
    import numpy as np
    from PIL import Image

    模型根 = os.path.join(os.path.expanduser("~"), ".dsh", "draw-meme")
    插件根 = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    底模 = 找底模(插件根, 模型根)
    if not os.path.isfile(底模):
        print("找不到底模文件：" + str(底模), file=sys.stderr, flush=True)
        print("用 DSH_DRAW_BASE 指一个本地 animagine-xl-4.0.safetensors", file=sys.stderr, flush=True)
        return 1
    lora = os.environ.get("DSH_DRAW_LORA") or os.path.join(插件根, "models", "dafeiyu_v24.safetensors")
    设备 = "cuda" if torch.cuda.is_available() else "cpu"
    类型 = torch.float16 if 设备 == "cuda" else torch.float32

    if not (lora and os.path.exists(lora)):
        print("警告：没找到大肥鱼 LoRA（%s）" % (lora or "未配置"), flush=True)
        print("      插件能出图，但画出来的不是大肥鱼。去 Releases 下载 "
              "dafeiyu_v24.safetensors 放进 models/ 就好。", flush=True)
    # 不加字开关（2026-11-11 她定的）：DSH_DRAW_NO_TEXT=1 时强制不贴字，方便只出图、字后期自己加
    if 不贴字 or os.environ.get("DSH_DRAW_NO_TEXT", "").strip().lower() in ("1", "true", "yes"):
        台词 = ""
    print("台词：%r" % (台词 or ""), flush=True)
    if not (台词 or "").strip():
        print("注意：没传台词，这张图上不会有字", flush=True)
    基种子 = int(种子) if 种子 is not None and str(种子) != "" else int(time.time()) % 1000000

    # 底图（2026-11-11 她定的）：从插件自带的「底图库」里随机抽一张 1 步底图当构图骨架。
    # 底图库里是同一种子只跑 1 步的半成品 png；用它接力出图，构图更稳，也省一次采样。
    #   优先级：函数参数 基底图 > DSH_DRAW_BASE_IMG > DSH_DRAW_BASE_SEED > 底图库随机 > 纯 txt2img
    #   关掉：DSH_DRAW_NO_BASE=1
    底图 = None
    if os.environ.get("DSH_DRAW_NO_BASE", "").strip() in ("1", "true", "yes"):
        底图 = None
    else:
        指定 = (基底图 or os.environ.get("DSH_DRAW_BASE_IMG", "") or "").strip()
        指种子 = (os.environ.get("DSH_DRAW_BASE_SEED", "") or "").strip()
        底图目录 = os.path.join(插件根, "底图库")
        候选 = []
        if os.path.isdir(底图目录):
            候选 = sorted(f for f in os.listdir(底图目录) if f.lower().endswith(".pt"))
        if 指定 and os.path.exists(指定):
            底图 = 指定
            print("底图：指定 %s" % 指定, flush=True)
        elif 指种子:
            目标 = os.path.join(底图目录, "潜图-%s.pt" % 指种子)
            if os.path.exists(目标):
                候选 = [os.path.basename(目标)]
                # 2026-11-12 修 bug：这里原来只设了 候选、没设 底图，导致 --base-seed /
                # DSH_DRAW_BASE_SEED 一直「假生效」——日志说指定成功，实际走成纯 txt2img。
                底图 = 目标
                print("底图：指定种子 %s -> %s" % (指种子, 目标), flush=True)
            else:
                print("底图：库里没有种子 %s 的底图，这次按纯 txt2img 画" % 指种子, flush=True)
        elif 候选:
            import random as _random
            底图 = os.path.join(底图目录, _random.choice(候选))
            print("底图：库内 %d 份潜图，这次随机抽到 %s" % (len(候选), os.path.basename(底图)), flush=True)
        else:
            print("底图：底图库是空的（%s），这次按纯 txt2img 画" % 底图目录, flush=True)

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
        数量名 = "1 " + os.environ.get("DSH_DRAW_NAME", "dafeiyu")   # 数量+角色名绑在一起
        变体 = os.environ.get("DSH_DRAW_PROMPT_VARIANT", "M").upper()
        发色词 = os.environ.get("DSH_DRAW_HAIR", "(solid royal blue hair:1.5)")
        短1 = "dafeiyu, 1girl, solo, chibi, " + 发色词 + ", long wavy hair, one curved ahoge, (2.2 head tall:1.3)"
        短2 = "deep royal blue eyes, big round eyes, (soft warm pinkish skin:1.35), white frilled maid headdress, very dark navy dress, pure white apron, whale tail, white and blue fin ear ornaments"
        短3后 = "pure white background, soft watercolor anime style, (upper body portrait, hands visible:1.3), (exaggerated expression:1.35), "
        if 变体 == "B":      # 只把颜色写详细
            正段们 = ["dafeiyu, 1girl, solo, chibi, (solid royal blue hair:1.5), long wavy hair, one curved ahoge, (2.2 head tall:1.3)",
                     "(deep royal blue eyes:1.3), big round eyes, (fair warm pinkish skin, rosy cheeks:1.35), white frilled maid headdress, very dark navy dress, pure white apron, whale tail, white and blue fin ear ornaments",
                     短3后 + 动作 + ", dynamic pose"]
        elif 变体 == "C":    # 只把服装配件写详细
            正段们 = [短1,
                     "deep royal blue eyes, big round eyes, (soft warm pinkish skin:1.35)",
                     "white frilled maid headdress with a small blue bow on one side, white and blue whale fin ear ornaments, one whale tail curling out from behind the body to the side, navy blue long sleeve maid dress, pure white apron with a tiny blue whale on it, gold trim on cuffs and hem",
                     短3后 + 动作 + ", dynamic pose"]
        elif 变体 == "D":    # 只把动作表情写详细
            正段们 = [短1, 短2,
                     "pure white background, soft watercolor anime style, (upper body portrait, hands visible:1.3), (facial expression:1.4), both eyes open with expressive eyebrows, mouth shaped by the emotion, "
                     + 动作 + ", body language matching the mood, dynamic pose"]
        elif 变体 == "F":    # 表情词放最前面 + 服装详细（C 那套），配 CFG 6.5 用
            正段们 = ["(facial expression:1.4), (exaggerated expression:1.35), " + 动作,
                     短1,
                     "deep royal blue eyes, big round eyes, (soft warm pinkish skin:1.35)",
                     "white frilled maid headdress with a small blue bow on one side, white and blue whale fin ear ornaments, one whale tail curling out from behind the body to the side, navy blue long sleeve maid dress, pure white apron with a tiny blue whale on it, gold trim on cuffs and hem",
                     "pure white background, soft watercolor anime style, (upper body portrait, hands visible:1.3), dynamic pose"]
        elif 变体 == "M":    # 极简版（2026-11-09 她定）：只留身份特征 + 表情动作，不要画风/构图/感觉词
            身份 = os.environ.get("DSH_DRAW_IDENTITY",
                                "blue whale fin ear ornaments, navy maid dress, white apron, dark blue whale tail, "
                                "white maid headdress, (dark navy blue hair :0.9), long hair, ahoge, "
                                "pale pinkish skin, "
                                "blue eyes, 1girl, solo")
            # 顺序（2026-11-10 她定）：solo 紧跟角色名，表情随后，尾巴跟表情，其余身份描述放最后；不要 1girl
            描 = 身份.replace("1girl, ", "").replace(", 1girl", "").replace("solo", "").strip(", ")
            描 = 描.replace("dark blue whale tail, ", "").replace(", dark blue whale tail", "").strip(", ")
            前 = os.environ.get("DSH_DRAW_PREFIX", "")
            尾 = os.environ.get("DSH_DRAW_SUFFIX", "")
            头 = os.environ.get("DSH_DRAW_HEAD", "")
            # 2026-11-12 她定：加回 1girl（数量限定靠它，solo 权重太弱），并把正面提示词收简洁。
            # 顺序：数量词 → 角色名 → 头身比 → 发色 → 表情 → 服装/尾巴 → 白底。
            正段们 = [前 + "(" + 数量名 + ":1.2), solo, " + 头
                     + "chibi, big head, short body, (solid royal blue hair:1.5), long wavy hair, ahoge, "
                     + 动作 + ", "
                     + "blue eyes, pale pinkish skin, blue whale fin ear ornaments, navy maid dress, "
                     + "(white apron:1.2), white maid headdress, large dark blue whale tail, blue tail fin, "
                     + "pure white background"
                     + 尾]
        elif 变体 == "G":    # 夸张表情版：情绪词打头并加重，脸占更大画面
            正段们 = ["(exaggerated facial expression:1.55), (very strong emotion:1.45), " + 动作
                     + ", (mouth clearly showing the emotion:1.4), (eyebrows matching the emotion:1.3), "
                       "(eyes shaped by the emotion:1.3)",
                     短1,
                     "deep royal blue eyes, big round eyes, (soft warm pinkish skin:1.35)",
                     "white frilled maid headdress with a small blue bow on one side, white and blue whale fin ear ornaments, navy blue long sleeve maid dress, pure white apron with a tiny blue whale on it, gold trim on cuffs and hem",
                     "pure white background, soft watercolor anime style, (close-up of the face:1.3), (upper body portrait:1.2), dynamic pose"]
        elif 变体 == "E":    # 全部详细（上一版试过，掉色）
            正段们 = [段1, 段2, 段3, 段4前 + 动作 + 段4后]
        else:                # A 基线
            正段们 = [短1, 短2, 短3后 + 动作 + ", dynamic pose"]
        # 白底（2026-11-12，她定的 B 方案）：以前靠出图后的 floodfill 刷白，可 comfy 后端把刷白
        # 默认关了，底色就露出来（实测插件图背景 158,181,206 / 216,213,216，实验脚本是 247~252）。
        # 现在把白底词直接写进提示词，跟验证过的那套一致。想去掉：DSH_DRAW_WHITE_BG=0
        # 2026-11-12 实测：这段白底词权重 1.5/1.4 会把画风拉成水彩、线稿消失，
        # 所以默认关掉，改回「出图后刷白」。真要用提示词压白底：DSH_DRAW_WHITE_BG=1
        if os.environ.get("DSH_DRAW_WHITE_BG", "0").strip() == "1":
            正段们 = 正段们 + ["(pure white background:1.5), (solid white background:1.4), "
                              "white void, simple background, no background detail"]
        # 2026-11-12 她定：只有一个后端了（comfy）。diffusers 那套管道、采样、
        # 两段式、低显存补充包都删掉了，这里不再有 if/else。
        if True:  # 固定走 comfy
            from comfy_backend import 出图_comfy, 出图_comfy_图生图
            负文 = 负面 if isinstance(负面, str) else " ".join(负面)
            采样器名 = os.environ.get("DSH_DRAW_SAMPLER", "dpmpp_2m")
            调度器名 = os.environ.get("DSH_DRAW_SCHED", "karras")
            if 底图:
                # 底图是「只跑 N 步」的半成品潜变量，接着从第 N 步跑：去噪 = 1 - N/总步数。
                # N 从底图旁的 sidecar（潜图-<种子>.json 里的 steps）读；没有就退到环境变量，
                # 再没有就用 2（2026-11-12 她定的：库换成 2 步底图）。1 步库要显式给 DSH_DRAW_BASE_START=1。
                # 用潜图（.pt）而不是 png：编码 png 会额外吃 10G 显存（2026-11-12 实测）。
                接手步 = _底图步数(底图)
                去噪 = max(0.05, 1.0 - float(接手步) / float(步数))
                print("底图：接手第 %d 步（去噪 %.4f）" % (接手步, 去噪), flush=True)
                if 底图.lower().endswith(".pt"):
                    底潜 = torch.load(底图, map_location="cpu")
                    # 底图按请求的边长缩放（2026-11-12）：底图库是 832 的 104×104 潜变量，
                    # 请求别的尺寸时形状对不上。不缩的话画布会被底图定死（请求 512 还是出 832）；
                    # 缩了就能真出目标尺寸，代价是缩得越远构图越飘（实测 4 档都能用）。
                    格 = max(8, int(边长) // 8)
                    if tuple(底潜.shape[-2:]) != (格, 格):
                        原格 = 底潜.shape[-2]
                        底潜 = torch.nn.functional.interpolate(
                            底潜.float(), size=(格, 格), mode="bilinear", align_corners=False)
                        print("底图：为 %d×%d 出图，把 %d×%d 的潜变量缩到 %d×%d"
                              % (边长, 边长, 原格 * 8, 原格 * 8, 格 * 8, 格 * 8), flush=True)
                    图 = 出图_comfy_图生图(
                        "".join(正段们), 负文,
                        种子=基种子 + i, 步数=步数, 配置=CFG, 去噪=去噪,
                        lora=lora, 底模=底模, 采样器=采样器名, 调度器=调度器名, 底潜=底潜,
                    )
                else:
                    from PIL import Image as _Image
                    图 = 出图_comfy_图生图(
                        "".join(正段们), 负文, _Image.open(底图),
                        种子=基种子 + i, 步数=步数, 配置=CFG, 去噪=去噪,
                        lora=lora, 底模=底模, 采样器=采样器名, 调度器=调度器名,
                    )
            else:
                图 = 出图_comfy(
                    "".join(正段们), 负文,
                    种子=基种子 + i, 边长=边长, 步数=步数, 配置=CFG,
                    lora=lora, 底模=底模,
                    采样器=采样器名, 调度器=调度器名,
                )
            # 2026-11-12 她定：刷白从流程里去掉。
            # 原因：它只吃「跟四角同色、连成一片的浅色区」，粉底/蓝底/灰底那种成块彩色背景
            # 全刷不掉（50 张实测至少 8 张留着彩底），反而把白围裙边缘吃掉。
            # 想临时开回来：DSH_DRAW_NO_WHITEN=0（默认已是 1，即不刷）。
            if os.environ.get("DSH_DRAW_NO_WHITEN", "1").strip() == "0":
                图 = 刷白背景(图)
            if os.environ.get("DSH_DRAW_DUMP_PROMPT") == "1":
                print("PROMPT=[" + "".join(正段们) + "]", flush=True)
                print("NEG=[" + (负面 if isinstance(负面, str) else " ".join(负面)) + "]", flush=True)
            # comfy 分支原来直接存图就 continue 了，贴字那步被跳过（2026-11-11 修）
            if 台词 and os.environ.get("DSH_DRAW_NO_TEXT", "").strip().lower() not in ("1", "true", "yes"):
                图 = 贴字(图, 台词, 字体路径, 字号比, 描边比)
            路径 = os.path.join(输出目录, f"{戳}_{i+1}.png")
            图.save(路径)
            print("IMG:" + os.path.abspath(路径), flush=True)
            continue
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
    # 2026-11-12 改：以前见了偏蓝的底色就整张跳过，结果蓝灰底板的图全留着彩色底。
    # 现在改成「按亮度判断」：底色够亮（不是深色画底、也不是深藏青裙子那种暗色）就刷，
    # 颜色靠泛洪阈值兜住——实测蓝灰底(140,160,200)和藏青裙(30,40,80)、蓝发(60,90,170)
    # 的通道差都大于阈值，刷不到人物身上。
    亮度 = float(0.299 * 中位[0] + 0.587 * 中位[1] + 0.114 * 中位[2])
    if 亮度 < 95:
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


def 贴字(图, 文本, 字体路径="", 字号比=0.078, 描边比=0.10):
    """深藏青字 + 白描边，写在下方，不扩画布、不垫白块。

    字体路径/字号比/描边比是从插件设置页传进来的（2026-11-11 她加的可调项），
    不传就用默认值，老调用方式（贴字(图, 文本)）行为不变。
    """
    from PIL import ImageDraw, ImageFont
    图 = 图.convert("RGBA")
    宽, 高 = 图.size
    字号 = max(16, int(宽 * float(字号比 or 0.078)))
    字体 = _找字体(字体路径)
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
           stroke_width=max(2, int(字号 * float(描边比 or 0.10))), stroke_fill=(255, 255, 255, 255))
    return 图.convert("RGB")


def _找字体(指定=""):
    候选 = []
    if 指定:
        候选.append(指定)
    候选 += [
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
    ap.add_argument("--action", default="")
    ap.add_argument("--count", type=int, default=1)
    ap.add_argument("--size", type=int, default=832)
    ap.add_argument("--out", required=True)
    ap.add_argument("--text", default="")
    ap.add_argument("--dim", type=float, default=0.96)
    ap.add_argument("--seed", type=int, default=None)
    # 一次加载模型、连着画多个动作：每个进程都要重新加载 7G 底模，单独跑一次就是一次加载
    ap.add_argument("--actions", default="", help="用 | 分隔的多个动作，一次加载里连续出图")
    ap.add_argument("--seed-step", type=int, default=7, help="多张时每张种子的步进，配合 --seed 做同提示词对照")
    ap.add_argument("--base", default="", help="指定底图 png 的路径；不填 = 从插件自带的底图库随机抽一张")
    ap.add_argument("--base-seed", default="", help="从底图库里挑指定种子那张（如 --base-seed 8123）；不填 = 随机")
    ap.add_argument("--no-base", action="store_true", help="这次不用底图，纯 txt2img 从零画")
    ap.add_argument("--steps", type=int, default=0, help="总步数，默认 20（底图已跑 2 步，实际采样 = 总步数-2）")
    ap.add_argument("--no-text", action="store_true", help="只出图，不贴中文（插件设置页「要字」关掉时用）")
    ap.add_argument("--font", default="", help="字体文件路径；留空用默认（雅黑粗体）")
    ap.add_argument("--font-scale", type=float, default=0.078, help="字号占画面宽度的比例，默认 0.078")
    ap.add_argument("--stroke", type=float, default=0.10, help="白描边粗细占字号的比例，默认 0.10")
    a = ap.parse_args()
    # 底图选择（2026-11-11）：默认从插件「底图库」随机抽一张 1 步底图接力出图；
    # 测试时可以 --base 指定图片、--base-seed 指定种子、--no-base 关掉。
    if a.no_base:
        os.environ["DSH_DRAW_NO_BASE"] = "1"
    if a.base_seed:
        os.environ["DSH_DRAW_BASE_SEED"] = str(a.base_seed)
    if a.steps:
        os.environ["DSH_DRAW_STEPS"] = str(a.steps)
    if a.actions:
        码 = 0
        # 动作里本身有逗号，所以优先用 | 分隔；没写 | 才退回逗号
        分隔符 = "|" if "|" in a.actions else ","
        for 序, 一个 in enumerate([x.strip() for x in a.actions.split(分隔符) if x.strip()]):
            这种子 = None if a.seed is None else a.seed + 序 * a.seed_step
            码 = 出图(一个, max(1, min(5, a.count)), a.size, a.out, a.dim, a.text, 这种子, a.base or None,
                     a.font or "", a.font_scale, a.stroke, a.no_text) or 码
        sys.exit(码)
    sys.exit(出图(a.action, max(1, min(5, a.count)), a.size, a.out, a.dim, a.text, a.seed, a.base or None,
                 a.font or "", a.font_scale, a.stroke, a.no_text))
