# -*- coding: utf-8 -*-
"""
ComfyUI 采样后端（搬进插件的那一份）。

这是什么
  把 ComfyUI 的源码（comfy_kit/）直接在插件进程里跑，不启动 ComfyUI 服务。
  用途是让插件的出图曲线和 ComfyUI 完全一致——同一套采样器、同一套模型加载。

怎么用
  from 出图_comfy import 出图_comfy
  图 = 出图_comfy(正向提示词, 负向提示词, 种子=104300, 边长=832, 步数=35, 配置=7.0,
                 lora=..., 底模=..., 采样器="dpmpp_2m", 调度器="karras")
  返回 PIL.Image（RGB）。

约定
  - VAE 解码前强制 fp32（等价 ComfyUI 的 --fp32-vae），否则 SDXL 出图发灰。
  - VAE 解码输出已经是 0~1，不要再做 (x+1)/2。
  - 模型和 LoRA 只加载一次，缓存在模块里；换底模或换 LoRA 才重载。

出问题怎么撤
  这个文件是新增的，删掉即可。draw.py 里只有 DSH_DRAW_BACKEND=comfy 时才会调用它。
"""
import os
import sys
import torch

_已加载 = {}


def _拿环境(底模, lora):
    """加载或复用（model, clip, vae）。"""
    key = (os.path.abspath(底模), os.path.abspath(lora) if lora else "")
    if key in _已加载:
        return _已加载[key]

    # comfy_kit 在不同布局里层级不一样（开发版多一层「开发版/」，装进 node_modules 又少一层），
    # 所以从本文件往上找带 comfy_kit 的那一级，不再写死层级（2026-11-12 装插件时改）。
    工具包 = None
    _起 = os.path.dirname(os.path.abspath(__file__))
    for _  in range(4):
        _起 = os.path.dirname(_起)
        _候选 = os.path.join(_起, "comfy_kit")
        if os.path.isdir(_候选):
            工具包 = _候选
            break
    if 工具包 is None:
        raise RuntimeError("找不到 comfy_kit（从 %s 往上找了 4 层）" % os.path.abspath(__file__))
    if 工具包 not in sys.path:
        sys.path.insert(0, 工具包)

    import comfy.cli_args
    # VAE 解码精度（2026-11-12 实测）：fp16 解码只要 5.3 秒、但图是**空白的**——就是 SDXL VAE
    # 在 fp16 下溢出的老毛病（uniform 灰图）。所以默认必须 fp32，不能图快。
    # 显存靠下面的「分块解码」压，不靠降精度。真要用 fp16 试：DSH_COMFY_FP16_VAE=1
    comfy.cli_args.args.fp32_vae = not (str(os.environ.get("DSH_COMFY_FP16_VAE", "")).strip().lower() in ("1", "true", "yes", "on"))
    # comfy 自己的低显存模式（2026-11-12 加，默认关）：开了之后文本编码器只在 CPU 上跑、
    # 采样时权重按需搬上显卡，峰值实测 0.95G。代价是每层来回搬，慢 2.3 倍。
    # 默认不设 = 和以前完全一样，不影响正常档。
    _低显 = str(os.environ.get("DSH_COMFY_LOWVRAM", "")).strip().lower()
    if _低显 in ("2", "novram", "no"):
        comfy.cli_args.args.novram = True
        print("低显存档：开（显存峰值约 0.95G，每层权重单独搬）", flush=True)
    elif _低显 in ("1", "true", "yes", "on", "lowvram"):
        comfy.cli_args.args.lowvram = True
        print("低显存档（轻量档）：开（文本编码器走 CPU、权重按需搬）", flush=True)
    import comfy.sd
    import comfy.sample
    import comfy.samplers
    import comfy.utils

    model, clip, vae, _ = comfy.sd.load_checkpoint_guess_config(
        底模, output_vae=True, output_clip=True, embedding_directory=None
    )
    if lora and os.path.exists(lora):
        lora数据 = comfy.utils.load_torch_file(lora, safe_load=True)
        model, clip = comfy.sd.load_lora_for_models(model, clip, lora数据, 1.0, 1.0)

    已备 = (model, clip, vae)
    _已加载[key] = 已备
    return 已备


def 出图_comfy(正提示, 负提示, 种子=0, 边长=832, 步数=35, 配置=7.0,
              lora=None, 底模=None, 采样器="dpmpp_2m", 调度器="karras"):
    from PIL import Image
    import numpy as np

    model, clip, vae = _拿环境(底模, lora)   # 先把 comfy_kit 加进 sys.path
    import comfy.sample
    import comfy.samplers
    import comfy.model_management as mm

    正 = clip.encode_from_tokens_scheduled(clip.tokenize(正提示))
    负 = clip.encode_from_tokens_scheduled(clip.tokenize(负提示))

    空潜 = {"samples": torch.zeros([1, 4, 边长 // 8, 边长 // 8], device=mm.get_torch_device())}
    噪声 = comfy.sample.prepare_noise(空潜["samples"], int(种子))

    采样器对 = comfy.samplers.KSampler(
        model, steps=int(步数), device=mm.get_torch_device(),
        sampler=采样器, scheduler=调度器,
    )
    潜 = 采样器对.sample(噪声, 正, 负, cfg=float(配置),
                         latent_image=空潜["samples"], seed=int(种子))

    像素 = 解码_分块(vae, 潜)
    数组 = 像素.detach().float()
    if float(数组.min()) < -0.05:      # 老式 VAE 输出 [-1,1]
        数组 = (数组 + 1.0) / 2.0
    数组 = torch.clamp(数组, 0.0, 1.0).cpu().numpy()
    if 数组.ndim == 4:
        数组 = 数组[0]
    数组 = (数组 * 255).astype(np.uint8)   # 已经是 HWC
    return Image.fromarray(数组).convert("RGB")


def 出图_comfy_图生图(正提示, 负提示, 底图=None, 种子=0, 步数=35, 配置=7.0, 去噪=0.5,
                      lora=None, 底模=None, 采样器="dpmpp_2m", 调度器="karras", 底潜=None):
    """图生图：以 底图 为构图骨架，按 去噪（0~1）重画。

    去噪 0.5 左右能保住构图、换掉细节；0.75 以上基本等于重画。
    """
    from PIL import Image
    import numpy as np

    model, clip, vae = _拿环境(底模, lora)
    import comfy.sample
    import comfy.samplers
    import comfy.model_management as mm

    正 = clip.encode_from_tokens_scheduled(clip.tokenize(正提示))
    负 = clip.encode_from_tokens_scheduled(clip.tokenize(负提示))

    if 底潜 is None:
        if 底图 is None:
            raise ValueError("图生图要么给 底图，要么给 底潜")
        数组 = np.asarray(底图.convert("RGB")).astype(np.float32) / 255.0
        张 = torch.from_numpy(数组).unsqueeze(0).to(mm.get_torch_device())
        底潜 = 编码_分块(vae, 张)
    else:
        底潜 = 底潜.to(mm.get_torch_device(), dtype=torch.float16)

    噪声 = comfy.sample.prepare_noise(底潜, int(种子))
    采样器对 = comfy.samplers.KSampler(
        model, steps=int(步数), device=mm.get_torch_device(),
        sampler=采样器, scheduler=调度器,
    )
    起始 = max(0, min(int(步数) - 1, int(round(int(步数) * (1.0 - float(去噪))))))
    潜 = 采样器对.sample(噪声, 正, 负, cfg=float(配置),
                         latent_image=底潜, seed=int(种子),
                         start_step=起始, force_full_denoise=True)

    像素 = 解码_分块(vae, 潜)
    输出 = 像素.detach().float()
    if float(输出.min()) < -0.05:
        输出 = (输出 + 1.0) / 2.0
    输出 = torch.clamp(输出, 0.0, 1.0).cpu().numpy()
    if 输出.ndim == 4:
        输出 = 输出[0]
    return Image.fromarray((输出 * 255).astype(np.uint8)).convert("RGB")


def _块权重(高, 宽, 重叠, 设备, 类型):
    """拼块用的柔性权重：块内部是 1，靠边的重叠区线性降到 0。

    因为最后按权重和归一化，图像最外圈的衰减会被自身除回去，不会变暗。
    """
    import torch

    def 一维(n):
        v = torch.ones(n, dtype=类型, device=设备)
        if 重叠 > 0:
            k = min(int(重叠), n // 2)
            if k > 0:
                r = torch.linspace(0, 1, k + 2, dtype=类型, device=设备)[1:-1]
                v[:k] = r
                v[-k:] = torch.flip(r, dims=[0])
        return v

    return (一维(高)[:, None] * 一维(宽)[None, :])[None, None]


def 解码_分块(vae, 潜, 块=24, 重叠=4):
    """分块解码，压住 fp32 VAE 的显存峰值（2026-11-12 加）。

    背景：一次解码 832 的图时显存从 6.4G 冲到 21.8G（权重才 6.4G），16G 卡溢出到系统内存，
          单张要十几二十秒。fp16 解码虽快但会出空白图，所以只能分块。
    参数：块 = 每块的潜边长（64 => 512 像素）；重叠 = 相邻块重叠的潜像素（8 => 64 像素）。
          环境变量 DSH_COMFY_TILE 可覆盖块大小，设 0 关闭分块退回一次解码。
    """
    import torch

    try:
        块 = int(os.environ.get("DSH_COMFY_TILE", str(块)) or 块)
    except ValueError:
        pass
    高, 宽 = int(潜.shape[2]), int(潜.shape[3])
    if 块 <= 0 or (块 >= 高 and 块 >= 宽):
        return vae.decode(潜)
    重叠 = max(0, min(int(重叠), 块 // 2))
    步长 = max(1, 块 - 重叠)

    def 起点(总, 尺寸, 步):
        out = []
        p = 0
        while True:
            if p + 尺寸 >= 总:
                out.append(总 - 尺寸)
                break
            out.append(p)
            p += 步
        return out

    结果 = None
    权重总 = None
    for y in 起点(高, 块, 步长):
        for x in 起点(宽, 块, 步长):
            片 = 潜[:, :, y:y + 块, x:x + 块]
            解 = vae.decode(片).detach().float()
            if 解.shape[-1] != 3 and 解.shape[1] == 3:      # 万一是 NCHW，转成 NHWC
                解 = 解.permute(0, 2, 3, 1)
            高像, 宽像 = int(解.shape[1]), int(解.shape[2])
            倍 = max(1, 高像 // 块)          # latent -> 像素的放大倍数（通常 8）
            if 结果 is None:
                结果 = torch.zeros((1, 高 * 倍, 宽 * 倍, 3), dtype=解.dtype, device=解.device)
                权重总 = torch.zeros((1, 高 * 倍, 宽 * 倍, 1), dtype=解.dtype, device=解.device)
            y像, x像 = y * 倍, x * 倍
            权重 = _块权重(高像, 宽像, 重叠 * 倍, 解.device, 解.dtype).permute(0, 2, 3, 1)
            结果[:, y像:y像 + 高像, x像:x像 + 宽像, :] += 解 * 权重
            权重总[:, y像:y像 + 高像, x像:x像 + 宽像, :] += 权重
    return 结果 / 权重总.clamp_min(1e-6)


def 编码_分块(vae, 张, 块=192, 重叠=32):
    """分块编码（图生图用），和 解码_分块 对称：把整张图切成带重叠的小块分别编码，
    再把潜变量拼回去，避免一次编码 832 图把显存撑爆（实测一次编码要多用 5G+）。

    张 是 [1,H,W,3] 的 0~1 张量。块/重叠 是像素单位；DSH_COMFY_ETILE 可覆盖块大小，0 关闭。
    """
    import torch

    try:
        块 = int(os.environ.get("DSH_COMFY_ETILE", str(块)) or 块)
    except ValueError:
        pass
    高, 宽 = int(张.shape[1]), int(张.shape[2])
    if 块 <= 0 or (块 >= 高 and 块 >= 宽):
        编码 = vae.encode(张)
        return 编码["samples"] if isinstance(编码, dict) else 编码
    重叠 = max(0, min(int(重叠), 块 // 2))
    步长 = max(1, 块 - 重叠)

    def 起点(总, 尺寸, 步):
        out = []
        p = 0
        while True:
            if p + 尺寸 >= 总:
                out.append(总 - 尺寸)
                break
            out.append(p)
            p += 步
        return out

    结果 = None
    权重总 = None
    for y in 起点(高, 块, 步长):
        for x in 起点(宽, 块, 步长):
            片 = 张[:, y:y + 块, x:x + 块, :]
            编码 = vae.encode(片)
            潜 = 编码["samples"] if isinstance(编码, dict) else 编码
            if 潜.shape[1] != 4 and 潜.shape[-1] == 4:      # 万一是 NHWC
                潜 = 潜.permute(0, 3, 1, 2)
            倍 = max(1, 块 // int(潜.shape[2]))             # 像素 / 潜像素（通常 8）
            if 结果 is None:
                结果 = torch.zeros((1, 4, 高 // 倍, 宽 // 倍), dtype=潜.dtype, device=潜.device)
                权重总 = torch.zeros((1, 1, 高 // 倍, 宽 // 倍), dtype=潜.dtype, device=潜.device)
            y潜, x潜 = y // 倍, x // 倍
            权 = _块权重(int(潜.shape[2]), int(潜.shape[3]), 重叠 // 倍, 潜.device, 潜.dtype)
            结果[..., y潜:y潜 + 潜.shape[2], x潜:x潜 + 潜.shape[3]] += 潜 * 权
            权重总[..., y潜:y潜 + 潜.shape[2], x潜:x潜 + 潜.shape[3]] += 权
    return (结果 / 权重总.clamp_min(1e-6)).to(torch.float16)
