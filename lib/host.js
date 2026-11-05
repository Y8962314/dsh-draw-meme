// DSH「表情包绘制」· 宿主半边
//
// 干一件事：**让大肥鱼自己画表情包，装完就能用。**
//
//   draw_meme(情绪, 台词?)
//      → 拼提示词（角色段写死 + 表情动作段）
//      → 调 python/draw.py（diffusers + LoRA，本地推理）
//      → 整图压暗 0.96（纯白跳过）
//      → 贴上中文（深藏青字+白描边，不垫白块）
//      → 存进 ~/.dsh/meme-packs/dafeiyu-auto/memes/auto/
//      → 返回图片路径
//
// 不需要 ComfyUI。只需要 DSH + Python 3.10+；依赖（torch/diffusers）
// 首次运行时自动装，模型首次运行时自动下载。
//
// 配置文件：~/.dsh/draw-meme.json（设置页也能改，改完立刻生效）
//   { "python": "...", "base": "本地底模或 HF 仓库名", "lora": "本地 LoRA 路径",
//     "size": 832, "dim": 0.96, "seed": null }
//
// 已知硬限制：
//   * CPU 出一张 1-2 分钟，有显卡 5-10 秒（首帧要等模型加载）
//   * SDXL 底模 6.9G 要下载，省不掉
//   * 中文只能后期贴，不能让模型写（SDXL 写中文必乱码，实测）

import { defineTool } from '@deepseek-ai/dsh-tools'
import { spawn } from 'node:child_process'
import { existsSync, mkdirSync, readFileSync, writeFileSync, copyFileSync } from 'node:fs'
import { join, dirname, basename } from 'node:path'
import { fileURLToPath } from 'node:url'
import { homedir } from 'node:os'

const 这里 = dirname(fileURLToPath(import.meta.url))
const 插件根 = join(这里, '..')
const Python脚本 = join(插件根, 'python', 'draw.py')
const Worker脚本 = join(插件根, 'python', 'worker.py')
const 内置底图 = join(插件根, 'models', 'base.png')
const 打包LoRA = join(插件根, 'models', 'dafeiyu_v17.safetensors')
const 配置文件 = join(homedir(), '.dsh', 'draw-meme.json')

export const name = 'draw-meme'
export const inject = ['tools', 'webServer']

/** 读用户配置（读不到就是空对象） */
function 读配置文件() {
  try {
    return JSON.parse(readFileSync(配置文件, 'utf8'))
  } catch {
    return {}
  }
}

/** 当前生效的配置：用户配置 > 环境变量 > 默认值 */
function 合并配置() {
  const 用户 = 读配置文件()
  return {
    python: 用户.python || process.env.DSH_PYTHON || '',
    base: 用户.base || process.env.DSH_DRAW_BASE || '',
    lora: 用户.lora || process.env.DSH_DRAW_LORA || (existsSync(打包LoRA) ? 打包LoRA : ''),
    size: Number(用户.size) || 832,
    dim: Number(用户.dim) || 0.96,
    seed: 用户.seed === null || 用户.seed === undefined ? null : Number(用户.seed),
    // 默认不走底图（纯 txt2img）：img2img 会把清晰度磨掉一层，实测 4 秒 vs 24 秒。
    // 想用底图接力就自己在 ~/.dsh/draw-meme.json 里写 "base图": "路径"。
    base图: 用户.base图 || '',
  }
}

/** 表情名 → 提示词片段（和 docs/提示词指南.md 里的表保持一致） */
const 表情表 = {
  笑: 'laughing hysterically, mouth wide open, tears of joy',
  震惊: 'jaw dropped, eyes bulging, arms flailing',
  鼓腮: 'puffed cheeks bulging, steam from head, crossing arms hard',
  哭: 'teary eyes crying, trembling lips',
  得意: 'smug grin, chin up high, hand on hip confidently',
  害羞: 'shy blushing looking away, hands behind back',
  无语: 'flat deadpan face, half-lidded eyes',
  犯困: 'sleepy half-closed eyes, yawning',
  冒汗: 'sweating nervously, forced smile',
  指人: 'one hand pointing at viewer, confident smirk',
  比心: 'both hands making a small heart, happy blush',
  鼓掌: 'clapping hands happily, bright smile',
  捂脸: 'both hands covering face, embarrassed',
  思考: 'hand on chin, thinking serious',
}

/** 找一个真能跑的 Python：Windows 商店的占位符不算 */
let 选定的Python = null
function 试一个(命令) {
  return new Promise((resolve) => {
    let 出 = ''
    let 子
    try {
      子 = spawn(命令, ['-c', 'import sys;print(sys.executable)'], { windowsHide: true })
    } catch {
      return resolve(false)
    }
    子.stdout.on('data', (d) => { 出 += d })
    子.on('error', () => resolve(false))
    子.on('close', (码) => {
      if (码 !== 0) return resolve(false)
      if (/WindowsApps/i.test(出)) return resolve(false)
      resolve(true)
    })
  })
}

async function 探Python() {
  if (选定的Python) return 选定的Python
  const 指定 = 合并配置().python
  const 候选 = []
  if (指定) 候选.push(指定)
  if (process.env.DSH_PYTHON) 候选.push(process.env.DSH_PYTHON)
  const 家 = homedir()
  for (const v of ['313', '312', '311', '310', '39']) {
    候选.push(join(家, 'AppData', 'Local', 'Programs', 'Python', 'Python' + v, 'python.exe'))
    候选.push(join(家, 'AppData', 'Roaming', 'uv', 'python', 'cpython-' + v.slice(0, 2) + '.' + v.slice(2) + '-windows-x86_64-none', 'python.exe'))
  }
  候选.push('C:/Python312/python.exe', 'C:/Python311/python.exe', 'C:/Python310/python.exe')
  候选.push('python', 'python3', 'py')
  for (const 命令 of 候选) {
    const 是命令名 = 命令 === 'python' || 命令 === 'python3' || 命令 === 'py'
    if (!是命令名 && !existsSync(命令)) continue
    if (await 试一个(命令)) {
      选定的Python = 命令
      return 命令
    }
  }
  return 指定 || 'python'
}

/** 跑 Python 脚本，收 stdout/stderr，超时杀掉 */
async function 跑Python(参数, 超时) {
  const 配置 = 合并配置()
  const python = await 探Python()
  return new Promise((resolve) => {
    const env = { ...process.env }
    // DSH 进程里的 TEMP 是 POSIX 路径（/tmp），Python 不认，会给
    // "No usable temporary directory found"。这里硬给一个真 Windows 目录。
    const 临时 = join(homedir(), '.dsh', 'draw-meme', 'tmp')
    try { mkdirSync(临时, { recursive: true }) } catch { /* 建不了就靠下面兜底 */ }
    env.TEMP = 临时
    env.TMP = 临时
    env.TMPDIR = 临时
    if (配置.base) env.DSH_DRAW_BASE = 配置.base
    if (配置.lora) env.DSH_DRAW_LORA = 配置.lora
    let 子
    try {
      子 = spawn(python, [Python脚本, ...参数], { cwd: 插件根, env, windowsHide: true })
    } catch (e) {
      return resolve({ 码: -1, 出: '', 错: '起不了 Python：' + String((e && e.message) || e) })
    }
    let 出 = '', 错 = ''
    子.stdout.on('data', (d) => { 出 += d })
    子.stderr.on('data', (d) => { 错 += d })
    const 定时 = setTimeout(() => { try { 子.kill() } catch { /* 已经没了 */ } }, 超时)
    子.on('close', (码) => {
      clearTimeout(定时)
      resolve({ 码, 出, 错 })
    })
    子.on('error', (e) => {
      clearTimeout(定时)
      resolve({ 码: -1, 出, 错: String((e && e.message) || e) })
    })
  })
}

// ─── 常驻出图进程 ─────────────────────────────────────────────
// 模型只加载一次（约 50 秒），之后每张只要 8~30 秒。
// 空闲 12 分钟自动退出，把 6.6G 显存还回去；下次调用再起。
let 工作进程 = null
const 空闲毫秒 = 12 * 60 * 1000

function 清掉工作进程(原因) {
  const w = 工作进程
  工作进程 = null
  if (!w) return
  try { clearTimeout(w.计时) } catch { /* 无所谓 */ }
  for (const p of w.待办) p.reject(new Error(原因 || '出图进程已退出'))
  w.待办 = []
  try { w.子.kill() } catch { /* 已经没了 */ }
}

async function 确保工作进程() {
  if (工作进程 && 工作进程.子 && !工作进程.子.killed) return 工作进程
  const 配置 = 合并配置()
  const python = await 探Python()
  const env = { ...process.env }
  const 临时 = join(homedir(), '.dsh', 'draw-meme', 'tmp')
  try { mkdirSync(临时, { recursive: true }) } catch { /* 建不了就靠兜底 */ }
  env.TEMP = 临时
  env.TMP = 临时
  env.TMPDIR = 临时
  if (配置.base) env.DSH_DRAW_BASE = 配置.base
  if (配置.lora) env.DSH_DRAW_LORA = 配置.lora
  const 子 = spawn(python, [Worker脚本], { cwd: 插件根, env, windowsHide: true })
  const w = { 子, 待办: [], 缓冲: '', 就绪: false, 计时: null }
  工作进程 = w

  子.stdout.on('data', (d) => {
    w.缓冲 += String(d)
    let i
    while ((i = w.缓冲.indexOf('\n')) >= 0) {
      const 行 = w.缓冲.slice(0, i).trim()
      w.缓冲 = w.缓冲.slice(i + 1)
      if (!行) continue
      let 消息
      try { 消息 = JSON.parse(行) } catch { continue }
      if (消息.ready) { w.就绪 = true; continue }
      const 等待 = w.待办.shift()
      if (等待) 等待.resolve(消息)
    }
  })
  子.stderr.on('data', () => { /* worker 的杂音不往日志里灌 */ })
  子.on('error', () => 清掉工作进程('出图进程起不来'))
  子.on('close', () => 清掉工作进程('出图进程退出了'))
  return w
}

function 重置空闲计时() {
  const w = 工作进程
  if (!w) return
  try { clearTimeout(w.计时) } catch { /* 无所谓 */ }
  w.计时 = setTimeout(() => {
    try { w.子.stdin.write(JSON.stringify({ cmd: 'quit' }) + '\n') } catch { /* 已经没了 */ }
  }, 空闲毫秒)
}

function 跑worker(载荷, 超时) {
  return new Promise((resolve, reject) => {
    确保工作进程().then((w) => {
      const 定时 = setTimeout(() => { 清掉工作进程('出图超时') }, 超时)
      w.待办.push({
        resolve: (v) => { clearTimeout(定时); 重置空闲计时(); resolve(v) },
        reject: (e) => { clearTimeout(定时); reject(e) },
      })
      try {
        w.子.stdin.write(JSON.stringify(载荷) + '\n')
      } catch (e) {
        清掉工作进程('写不进出图进程')
        reject(e)
      }
    }).catch(reject)
  })
}

/** 注册工具：等 tools 服务就绪再挂 */
function 等工具就绪(ctx, 名, 注册) {
  const 试 = () => {
    try { 注册(); return true } catch (e) {
      if (/duplicate/i.test(String(e && e.message))) return true
      try { ctx.logger && ctx.logger.error('draw-meme: ' + 名 + ' 注册失败 ' + (e && e.message)) } catch { /* 没 logger 就算了 */ }
      return false
    }
  }
  if (typeof ctx.inject === 'function') {
    try { ctx.inject(['tools'], () => 试()); return } catch { /* 退回同步 */ }
  }
  试()
}

/** 读 HTTP 请求体 */
function 读体(req) {
  return new Promise((resolve, reject) => {
    const 块 = []
    req.on('data', (c) => 块.push(c))
    req.on('end', () => resolve(Buffer.concat(块).toString('utf8')))
    req.on('error', reject)
  })
}

export function apply(ctx) {
  // —— 设置页用的配置 API ——
  const webServer = ctx.webServer ?? (typeof ctx.get === 'function' ? ctx.get('webServer') : null)
  if (webServer) {
    const json = (res, obj, 状态 = 200) => {
      res.writeHead(状态, { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store' })
      res.end(JSON.stringify(obj))
    }
    ctx.effect(() => webServer.register({
      kind: 'exact',
      path: '/dsh-draw-meme-api',
      async handler(req, res) {
        try {
          if (req.method === 'GET') {
            return json(res, {
              ok: true,
              config: 合并配置(),
              defaults: { size: 832, dim: 0.96 },
              file: 配置文件,
              loraPacked: existsSync(打包LoRA),
            })
          }
          if (req.method === 'POST') {
            const 体 = await 读体(req)
            const 输入 = JSON.parse(体 || '{}')
            const 当前 = 读配置文件()
            for (const 键 of ['python', 'base', 'lora', 'size', 'dim', 'seed']) {
              if (!(键 in 输入)) continue
              const 值 = 输入[键]
              if (值 === null || 值 === '') delete 当前[键]
              else 当前[键] = 值
            }
            const 目录 = dirname(配置文件)
            try { mkdirSync(目录, { recursive: true }) } catch { /* 有就别管 */ }
            writeFileSync(配置文件, JSON.stringify(当前, null, 2), 'utf8')
            选定的Python = null // 换了 python 就重新探
            return json(res, { ok: true, config: 合并配置(), file: 配置文件 })
          }
          return json(res, { ok: false, error: '只认 GET / POST' }, 405)
        } catch (e) {
          return json(res, { ok: false, error: String((e && e.message) || e) }, 400)
        }
      },
    }), 'dsh-draw-meme: 设置 API')
  }

  // —— draw_meme 工具 ——
  等工具就绪(ctx, 'draw_meme', () => ctx.tools.register(defineTool({
    name: 'draw_meme',
    description:
      '画一张大肥鱼表情包（本地出图，不需要 ComfyUI）。传情绪名（笑/震惊/鼓腮/哭/得意/害羞/无语/犯困/冒汗/指人/比心/鼓掌/捂脸/思考）' +
      '或直接传一段英文动作描述；**建议配一句中文台词**（会用中文字体贴在图下方；不传就只出底图、图上没有字）。返回图片绝对路径。' +
      '第一次用会装依赖、下模型（3~10 分钟），之后就快了。',
    parameters: {
      情绪: { type: 'string', description: '情绪名（中文，见描述里的列表）或英文动作描述' },
      台词: { type: 'string', description: '要贴在图上的中文（不填就只出底图）' },
      数量: { type: 'number', description: '出几张（默认 1，最大 5）' },
      尺寸: { type: 'number', description: '边长（默认 832，可传 512/768/832/1024；越大越慢）' },
    },
    output: {
      schema: { type: 'json' },
      render: (_a, v) => [{ type: 'text', text: v.message }],
    },
    async execute(参数) {
      const 配置 = 合并配置()
      const 情绪键 = String(参数.情绪 || '').trim()
      const 动作 = 表情表[情绪键] || 情绪键 || 'gentle smile'
      const 台词 = String(参数.台词 || '').trim()
      const 数量 = Math.max(1, Math.min(5, Math.floor(Number(参数.数量) || 1)))
      const 边 = [512, 768, 832, 1024].includes(Number(参数.尺寸)) ? Number(参数.尺寸) : 配置.size

      const 目录 = join(homedir(), '.dsh', 'meme-packs', 'dafeiyu-auto', 'memes', 'auto')
      try { mkdirSync(目录, { recursive: true }) } catch { /* 建不了就在下面报 */ }

      const 参 = [
        '--action', 动作,
        '--count', String(数量),
        '--size', String(边),
        '--out', 目录,
        '--text', 台词,
        '--dim', String(配置.dim),
      ]
      if (配置.seed !== null) 参.push('--seed', String(配置.seed))

      // 先走常驻进程（模型已在显存，每张 8~30 秒）；它挂了再退回一进程一张的老路
      let 结果 = null
      try {
        结果 = await 跑worker({
          action: 动作,
          count: 数量,
          size: 边,
          out: 目录,
          text: 台词,
          dim: 配置.dim,
          seed: 配置.seed,
          base: 配置.base图,
        }, 1800000)
      } catch (e) {
        结果 = null
      }

      let 路径 = []
      if (结果 && 结果.ok && Array.isArray(结果.images) && 结果.images.length) {
        路径 = 结果.images
      } else if (结果 && !结果.ok) {
        return {
          ok: false,
          message: '出图失败。\n' + String(结果.error || 结果.log || '').slice(-900) +
            '\n\n常见原因：Python 没装 / 依赖没装好 / 模型没下完。首次运行会自动准备，再试一次通常就好了。',
        }
      } else {
        const r = await 跑Python(参, 1200000 + 数量 * 300000)
        if (r.码 !== 0) {
          return {
            ok: false,
            message: '出图失败（退出码 ' + r.码 + '）。\n' + (r.错 || r.出 || '').slice(-900) +
              '\n\n常见原因：Python 没装 / 依赖没装好 / 模型没下完。首次运行会自动准备，再试一次通常就好了。',
          }
        }
        const 行 = (r.出 || '').trim().split(/\r?\n/).filter(Boolean)
        路径 = 行.filter((l) => l.startsWith('IMG:')).map((l) => l.slice(4).trim())
        if (路径.length === 0) {
          return { ok: false, message: '脚本没报错但没出图。原始输出：\n' + (r.出 || '').slice(-600) }
        }
      }

      // Python 子进程在很多机器上写不进 ~/.dsh（沙箱/ACL），但宿主进程能写。
      // 所以脚本先落在能写的目录，这里再搬进表情库；搬不动就留在原地，图照样能用。
      const 库 = join(homedir(), '.dsh', 'meme-packs', 'dafeiyu-auto', 'memes', 'auto')
      const 最终 = []
      for (const p of 路径) {
        let 目标 = p
        try {
          mkdirSync(库, { recursive: true })
          const d = join(库, basename(p))
          copyFileSync(p, d)
          目标 = d
        } catch { /* 搬不动就留在原地 */ }
        最终.push(目标)
      }

      return {
        ok: true,
        数量: 最终.length,
        路径: 最终,
        message: '出好了 ' + 最终.length + ' 张：\n' + 最终.map((p) => '![表情包](' + p + ')').join('\n') + (台词 ? '' : '\n\n（没传台词，所以图上没有文字。要字就把「台词」填上。）'),
      }
    },
  })))
  try { ctx.logger && ctx.logger.info('draw-meme: 已加载（工具 draw_meme + 设置 API）') } catch { /* 没 logger 无所谓 */ }
}
