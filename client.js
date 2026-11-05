/**
 * dsh-draw-meme — 「表情包绘制」设置页（Client 半边）。
 *
 * 以 dsh.client bundle 格式加载：注册 settings.section 一页，
 * 读写 ~/.dsh/draw-meme.json（走 host 的 /dsh-draw-meme-api）。
 * 注册 id 必须等于 loader entry 名（dsh-draw-meme），否则 ModuleLoader 报
 * "loaded without registering dsh-draw-meme"。
 */
window.__ModuleLoader__.load({
  id: 'dsh-draw-meme',
  factory: (require) => {
    var module = { exports: {} }
    var exports = module.exports

    const React = require('react')
    const h = React.createElement

    const 样式 = {
      面板: { display: 'flex', flexDirection: 'column', gap: 14, padding: '4px 0', fontSize: 13 },
      行: { display: 'flex', flexDirection: 'column', gap: 5 },
      标签: { fontSize: 12, color: 'var(--dsw-alias-label-secondary)' },
      输入: {
        background: 'var(--dsw-alias-bg-layer-1)', color: 'var(--dsw-alias-label-primary)',
        border: '1px solid var(--dsw-alias-border-l1)', borderRadius: 8,
        padding: '6px 10px', fontSize: 13, width: '100%', boxSizing: 'border-box', outline: 'none',
      },
      按钮: {
        background: 'var(--dsw-alias-button-primary-fill)', color: 'var(--dsw-alias-label-primary-foreground)',
        border: 'none', borderRadius: 8, padding: '7px 18px', fontSize: 13, cursor: 'pointer',
        alignSelf: 'flex-start',
      },
      提示: { fontSize: 12, color: 'var(--dsw-alias-label-secondary)', lineHeight: 1.7 },
      状态: { fontSize: 12, color: 'var(--dsw-alias-brand-primary)' },
    }

    function 格子(标签, 值, 改, 占位) {
      return h('div', { style: 样式.行 },
        h('span', { style: 样式.标签 }, 标签),
        h('input', {
          style: 样式.输入,
          value: 值,
          placeholder: 占位 || '',
          onChange: (e) => 改(e.target.value),
        }),
      )
    }

    function 面板() {
      const [配置, 设置配置] = React.useState(null)
      const [状态, 设置状态] = React.useState('')

      React.useEffect(() => {
        fetch('/dsh-draw-meme-api')
          .then((r) => r.json())
          .then((d) => {
            if (!d.ok) { 设置状态('读配置失败：' + (d.error || '')); return }
            const c = d.config || {}
            设置配置({
              python: c.python || '',
              base: c.base || '',
              lora: c.lora || '',
              size: String(c.size || 832),
              dim: String(c.dim || 0.96),
              seed: c.seed === null || c.seed === undefined ? '' : String(c.seed),
              文件: d.file || '',
              内置LoRA: !!d.loraPacked,
            })
          })
          .catch((e) => 设置状态('读配置失败：' + e.message))
      }, [])

      if (!配置) {
        return h('div', { style: 样式.面板 }, h('span', { style: 样式.提示 }, 状态 || '读取中…'))
      }

      const 改 = (键) => (值) => 设置配置({ ...配置, [键]: 值 })

      const 保存 = async () => {
        设置状态('保存中…')
        try {
          const 体 = {
            python: 配置.python,
            base: 配置.base,
            lora: 配置.lora,
            size: Number(配置.size) || 832,
            dim: Number(配置.dim) || 0.96,
            seed: 配置.seed === '' ? null : Number(配置.seed),
          }
          const r = await fetch('/dsh-draw-meme-api', {
            method: 'POST',
            headers: { 'content-type': 'application/json' },
            body: JSON.stringify(体),
          })
          const d = await r.json()
          设置状态(d.ok ? '已保存，下一次出图就用新参数（不用重启）' : '保存失败：' + (d.error || ''))
        } catch (e) {
          设置状态('保存失败：' + e.message)
        }
      }

      return h('div', { style: 样式.面板 },
        h('span', { style: 样式.提示 },
          '本地出图，不依赖 ComfyUI。第一次用会自动装 Python 依赖、下底模（约 7G），之后每次几秒到一分钟。'),

        格子('Python 解释器（留空自动找）', 配置.python, 改('python'), 'C:/Python312/python.exe'),
        格子('底模（本地 .safetensors 或 HF 仓库名，留空自动下）', 配置.base, 改('base'), 'cagliostrolab/animagine-xl-4.0'),
        格子('LoRA（留空用内置的大肥鱼 v17）', 配置.lora, 改('lora'),
          配置.内置LoRA ? '已内置 models/dafeiyu_v17.safetensors' : '没找到内置 LoRA'),
        格子('出图边长（512 / 768 / 832 / 1024）', 配置.size, 改('size'), '832'),
        格子('整体压暗（0.96 = 定稿值，1 不压）', 配置.dim, 改('dim'), '0.96'),
        格子('固定种子（留空每次随机；填 51001 可复现验收那批）', 配置.seed, 改('seed'), '留空'),

        h('button', { style: 样式.按钮, onClick: 保存 }, '保存'),
        h('span', { style: 样式.提示 }, '配置文件：' + (配置.文件 || '~/.dsh/draw-meme.json')),
        状态 ? h('span', { style: 样式.状态 }, 状态) : null,
      )
    }

    function apply(ctx) {
      const slots = ctx.get('slots')
      if (slots === undefined) return
      slots.inject('settings.section', () => slots.register(
        { name: 'settings.section', id: 'draw-meme', order: 26, label: '表情包绘制' },
        () => h(面板),
      ))
    }

    exports.apply = apply
    exports.inject = []
    return module.exports
  },
})
