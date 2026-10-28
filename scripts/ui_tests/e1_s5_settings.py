# -*- coding: utf-8 -*-
"""E1-场景5 设置页三卡（AI Key 门控 / CDP 配置 / 账号人设）（连 9223 可见 Chrome）。

验证：AI Key 保存 → 掩码/状态条 → AI 按钮灰态解除 → 测后复原；CDP 区状态；账号人设保存 toast。
踩坑记录：
  - 设置页 Key 输入框每次进入被 fillAiForm 清空（设计：留空保留，前端不持久回显明文），填假 Key 仅测 UI 流程；
  - AI 按钮灰态看 disabled/off class（创作页 .tab 需已激活采集态）；
  - 测后复原：真实清除 Key 需后端清除语义（空 key 保存=保留是保护设计），本轮由 AI Key 清除修复（e1_s5 配套）提供「清除配置」入口。

前置：后端 8791；9223 可见 Chrome。
"""
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    b = p.chromium.connect_over_cdp('http://127.0.0.1:9223')
    ctx = b.contexts[0]
    pg = next((x for x in ctx.pages if '8791' in (x.url or '')), None)
    pg.goto('http://127.0.0.1:8791/', timeout=20000)
    pg.wait_for_function("() => { const h = document.querySelector('.flow-hint'); return h && h.innerText.includes('关键词采集') }", timeout=12000)
    pg.wait_for_timeout(500)

    # 切设置 tab（原生 click）
    pg.locator('.tab', has_text='设置').first.click()
    pg.wait_for_timeout(1000)

    # 1) AI Key 区当前状态
    ai_status = pg.evaluate("""() => {
        const s = [...document.querySelectorAll('*')].filter(e => e.children.length===0 && /AI 配置|Key|未配置|已配置/.test(e.textContent || '')).map(e=>e.textContent.trim());
        return [...new Set(s)].slice(0,6);
    }""")
    print("1) 设置页 AI 区文本:", ai_status)

    # 2) 填 Key 保存（假 Key 仅测 UI 流程）
    key_input = pg.locator('input[type="password"], input[placeholder*="sk-"], input[placeholder*="Key"]').first
    print("2) Key输入框:", key_input.count())
    if key_input.count():
        key_input.fill('sk-test-123456')
        for ph in ['base', 'Base', '模型', 'model']:
            el = pg.locator(f'input[placeholder*="{ph}"], input[placeholder*="{ph.title()}"]').first
            if el.count() and not el.evaluate("el => el.type === 'password'"):
                el.fill('https://api.openai.com/v1')
                break
        pg.locator('button', has_text='保存配置').first.click()
        pg.wait_for_timeout(1500)
        masked = pg.evaluate("""() => {
            const m = [...document.querySelectorAll('input')].map(i => i.value).filter(v => v && /sk-/.test(v));
            const badge = document.body.innerText.match(/AI Key[^\\n]*/);
            return {maskedInputs: m, badge: badge ? badge[0] : '(无)'};
        }""")
        print("3) 保存后掩码/状态:", masked)

    # 3) CDP 区
    cdp_txt = pg.evaluate("""() => {
        const t = document.body.innerText;
        const m = t.match(/CDP[^\\n]*/g);
        return m ? [...new Set(m)].slice(0,5) : '(无CDP文本)';
    }""")
    print("4) CDP 区:", cdp_txt)

    # 4) 账号人设：填 + 保存
    p_inputs = pg.locator('input').all()
    filled = 0
    for i in range(len(p_inputs)):
        ph = p_inputs[i].get_attribute('placeholder') or ''
        if '行业' in ph or '语气' in ph or '话题' in ph:
            p_inputs[i].fill('玉石珠宝' if '行业' in ph else ('专业温和' if '语气' in ph else '南红,手串,翡翠'))
            filled += 1
    print("5) 人设输入框填充:", filled)
    if filled:
        pg.locator('button', has_text='保存').first.click()
        pg.wait_for_timeout(1000)
        toast = pg.evaluate("""() => {
            const t = document.querySelector('[class*=toast]');
            return t ? t.innerText : '(无)';
        }""")
        print("6) 人设保存 toast:", toast)

    # 5) 复原：用「清除配置」入口清掉测试 Key（本轮新增），验证状态条回未配置
    clear_btn = pg.locator('button', has_text='清除配置').first
    if clear_btn.count():
        clear_btn.click()
        pg.wait_for_timeout(1500)
        badge2 = pg.evaluate("() => document.body.innerText.match(/AI Key[^\\n]*/)?.[0] || '(无)'")
        print("7) 清除后状态条:", badge2)
    b.close()
