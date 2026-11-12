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

    function 勾选框(标签, 打勾, 改, 说明, 禁用) {
      return h('div', { style: { ...样式.行, opacity: 禁用 ? 0.55 : 1 } },
        h('label', { style: { display: 'flex', alignItems: 'center', gap: 8, cursor: 禁用 ? 'not-allowed' : 'pointer' } },
          h('input', {
            type: 'checkbox', checked: !!打勾, disabled: !!禁用,
            onChange: (e) => 改(e.target.checked),
          }),
          h('span', { style: { fontSize: 13 } }, 标签),
        ),
        说明 ? h('span', { style: 样式.提示 }, 说明) : null,
      )
    }

    function 下拉(标签, 值, 选项, 改, 说明) {
      return h('div', { style: 样式.行 },
        h('span', { style: 样式.标签 }, 标签),
        h('select', {
          style: 样式.输入, value: 值,
          onChange: (e) => 改(e.target.value),
        }, ...选项.map(([v, t]) => h('option', { key: v, value: v }, t))),
        说明 ? h('span', { style: 样式.提示 }, 说明) : null,
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
              低显存: !!c.低显存,
              要字: c.要字 === undefined ? !c.noText : !!c.要字,
              字体: c.字体 || 'auto',
              字号: String(c.字号 || 1),
              字重: String(c.字重 || 1),
              步数: String(c.步数 || 20),
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
            低显存: !!配置.低显存,
            要字: !!配置.要字,
            字体: 配置.字体,
            字号: Number(配置.字号) || 1,
            字重: Number(配置.字重) || 1,
            步数: Number(配置.步数) || 20,
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
        格子('LoRA（留空用内置的大肥鱼 v24）', 配置.lora, 改('lora'),
          配置.内置LoRA ? '已内置 models/dafeiyu_v24.safetensors' : '没找到内置 LoRA'),
        下拉('出图边长', String(配置.size), [
          ['512', '512（快，1.17 秒；细节糊，构图偏最多）'],
          ['768', '768（2.35 秒；接近 832）'],
          ['832', '832（推荐，2.72 秒；唯一不用缩放底图，最稳）'],
          ['1024', '1024（最细，3.89 秒；慢 43%）'],
        ], 改('size'), '非 832 会把底图缩放过去，构图会略飘'),
        格子('采样总步数（默认 20；底图已跑 2 步，实际采样 = 总步数-2）', 配置.步数, 改('步数'),
          '20 = 2.66 秒/张；15 步 2.07 秒；28 步 3.9 秒；10 步以下会糊'),
        格子('整体压暗（0.96 = 定稿值，1 不压）', 配置.dim, 改('dim'), '0.96'),
        格子('固定种子（留空每次随机；填 51001 可复现验收那批）', 配置.seed, 改('seed'), '留空'),

        h('span', { style: { ...样式.标签, marginTop: 6 } }, '图上的字'),
        勾选框('在图上写台词', 配置.要字, 改('要字'), '关掉就只出图，字你自己后期加。'),
        下拉('字体', 配置.字体, [
          ['auto', '默认（微软雅黑粗体）'],
          ['行楷', '行楷（夸张、适合兴奋/震惊）'],
          ['楷体', '楷体（软、适合委屈/害羞）'],
          ['黑体', '黑体（硬、适合生气/得意）'],
          ['雅黑', '雅黑粗体'],
        ], 改('字体'), '也可以直接把 .ttf/.ttc 的完整路径填在下面。'),
        格子('字体文件路径（留空用上面选的）', /[\/]/.test(配置.字体) ? 配置.字体 : '', (v) => 设置配置({ ...配置, 字体: v || 'auto' }), 'D:/字体/某某.ttf'),
        格子('字号（1 = 原样，1.5 = 更大）', 配置.字号, 改('字号'), '1'),
        格子('字重（1 = 原样，2 = 描边更粗）', 配置.字重, 改('字重'), '1'),

        勾选框('低显存档（显存峰值约 0.95G，慢 2.3 倍）', 配置.低显存, 改('低显存'),
          '勾上之后走 comfy 自带的低显存档：峰值显存从 7.5G 降到 0.95G，画质几乎不变'
            + '（同一个底图同一个种子实测差 0.36%），代价是每张从 3.3 秒变成 7.7 秒。'),

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
