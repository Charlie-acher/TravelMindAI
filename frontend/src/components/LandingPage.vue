<script setup lang="ts">
/** 品牌首页：保留确认的山景与布局；只上报规划意图，登录与智能体调用交给App。 */
import { nextTick, onBeforeUnmount, onMounted, ref } from 'vue'

defineProps<{ busy: boolean; error: string }>()
const question = defineModel<string>({ default: '' })
const emit = defineEmits<{ plan: []; send: [question: string] }>()
const textarea = ref<HTMLTextAreaElement | null>(null)
const brandDialog = ref<HTMLDialogElement | null>(null)
const paused = ref(false)
const hidden = ref(false)
let reducedMotion: MediaQueryList | null = null

/** 示例只填入；发送才交给父页面检查登录。 */
async function chooseExample(value: string): Promise<void> {
  question.value = value
  await nextTick()
  textarea.value?.focus()
}
function submit(): void {
  if (question.value.trim()) emit('send', question.value.trim())
}
function onKeydown(event: KeyboardEvent): void {
  if (event.key === 'Enter' && !event.shiftKey && !event.isComposing && event.keyCode !== 229) {
    event.preventDefault(); submit()
  }
}
function syncVisibility(): void { hidden.value = document.hidden }
function syncReducedMotion(): void { paused.value = reducedMotion?.matches ?? false }
onMounted(() => {
  reducedMotion = matchMedia('(prefers-reduced-motion: reduce)')
  syncReducedMotion(); syncVisibility()
  reducedMotion.addEventListener('change', syncReducedMotion)
  document.addEventListener('visibilitychange', syncVisibility)
})
onBeforeUnmount(() => {
  reducedMotion?.removeEventListener('change', syncReducedMotion)
  document.removeEventListener('visibilitychange', syncVisibility)
})
</script>

<template>
<div class="landing-page">
  <!-- 山峦以浅色分层，山路从远处延伸至前景；所有装饰均不参与读屏与交互。 -->
  <div class="backdrop" aria-hidden="true" :data-paused="paused || hidden">
    <svg class="landscape" id="landscape" viewBox="0 0 1600 1000" preserveAspectRatio="xMidYMid slice">
      <defs><radialGradient id="painted-mist"><stop stop-color="#f6f9f3" stop-opacity=".44"></stop><stop offset=".5" stop-color="#f6f9f3" stop-opacity=".22"></stop><stop offset="1" stop-color="#f6f9f3" stop-opacity="0"></stop></radialGradient></defs>
      <!-- 参考用户提供的连绵山脊，以青绿淡彩绘画重新创作；保留前中后三层。 -->
      <image class="painted-mountains" href="/brand/mountains-pastel-reference.png" x="-20" y="-15" width="1640" height="1030" preserveAspectRatio="xMidYMid slice"></image>
      <g class="valley-mist"><ellipse cx="660" cy="595" rx="780" ry="135" fill="url(#painted-mist)"></ellipse><ellipse cx="1110" cy="793" rx="500" ry="90" fill="url(#painted-mist)" opacity=".65"></ellipse></g>
    </svg>
  </div><div class="veil" aria-hidden="true"></div>
  <svg width="0" height="0" style="position:absolute" aria-hidden="true"><defs><symbol id="mark" viewBox="0 0 64 64"><path d="M11 48 21 20Q22 17 24 21L32 39Q33 42 35 38L43 20Q45 16 47 21L53 39" fill="none" stroke="currentColor" stroke-width="6.5" stroke-linecap="round" stroke-linejoin="round"></path><circle cx="55" cy="48" r="4" fill="#f4a261"></circle></symbol><symbol id="spark" viewBox="0 0 24 24"><path d="m12 2 2.6 7.4L22 12l-7.4 2.6L12 22l-2.6-7.4L2 12l7.4-2.6Z" fill="none" stroke="currentColor" stroke-width="1.5"></path></symbol></defs></svg>
  <div class="page" data-screen-label="TravelMindAI 品牌首页">
    <header>
      <a class="brand" href="./" aria-label="TravelMindAI 首页"><svg style="color:var(--brand)" aria-hidden="true"><use href="#mark"></use></svg><span class="wordmark">TravelMind<em>AI</em></span></a>
      <nav class="nav" aria-label="页面导航"><button class="nav-link" @click="brandDialog?.showModal()">品牌与配色</button><button class="nav-action" @click="emit('plan')" :disabled="busy">开始规划 <span aria-hidden="true">↗</span></button></nav>
    </header>
    <main>
      <section class="hero" aria-labelledby="hero-title">
        <p class="eyebrow"><svg aria-hidden="true"><use href="#spark"></use></svg>你的 AI 旅行伙伴</p>
        <h1 id="hero-title">下一程，<span>从你的想法开始。</span></h1>
        <p class="subtitle">告诉我目的地、预算和期待，一起安排这段旅程。</p>
        <p v-if="error" class="entry-error" role="alert">{{ error }}</p>
        <form class="composer" @submit.prevent="submit" :aria-busy="busy">
          <textarea ref="textarea" v-model="question" :disabled="busy" @keydown="onKeydown" aria-label="你的旅行想法" placeholder="想去哪里？或者，聊聊你期待的旅行……" maxlength="2000" required></textarea>
          <div class="composer-bottom"><span class="mode"><svg aria-hidden="true"><use href="#spark"></use></svg>灵感与规划</span><span class="input-hint">Enter 发送 · Shift + Enter 换行</span><button class="send" type="submit" :disabled="busy" :aria-label="busy ? '正在检查登录' : '发送旅行想法'"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true"><path d="M12 20V4m-7 7 7-7 7 7" stroke-linecap="round" stroke-linejoin="round"></path></svg></button></div>
        </form>
        <div class="suggestions" aria-label="试试这些旅行想法"><button class="suggestion" :disabled="busy" @click="chooseExample('想去杭州玩三天，预算 2000 元，希望轻松一点。')"><span aria-hidden="true">↗</span>杭州三日慢游</button><button class="suggestion" :disabled="busy" @click="chooseExample('想安排一个周末短途旅行，请先问问我的出发城市和偏好。')"><span aria-hidden="true">↗</span>周末去哪里</button><button class="suggestion" :disabled="busy" @click="chooseExample('想带爸妈去旅行，希望少走路，节奏舒适，帮我一起规划。')"><span aria-hidden="true">↗</span>带爸妈轻松出游</button></div>
        <p class="quiet-note">从一个念头，到一段值得记住的旅程<i aria-hidden="true"></i></p>
      </section>
    </main>
    <footer><div class="footer-brand">TravelMindAI<span>你的 AI 旅行伙伴</span></div><button class="motion" :aria-pressed="paused" @click="paused = !paused"><span class="motion-dot" aria-hidden="true"></span><span>{{ paused ? '继续背景流动' : '暂停背景流动' }}</span></button></footer>
  </div>
  <dialog ref="brandDialog" class="brand-dialog" aria-labelledby="brand-title"><div class="dialog-head"><h2 id="brand-title">一路灵感，自在出发。</h2><button class="close" aria-label="关闭品牌说明" @click="brandDialog?.close()">×</button></div><div class="brand large-logo"><svg style="color:var(--brand)" aria-hidden="true"><use href="#mark"></use></svg><span class="wordmark">TravelMind<em>AI</em></span></div><p class="dialog-copy">一条路线，组成 Mind 的 M。圆润的转折是旅程的节奏，橙色圆点是下一站，也是出发的期待。</p><div class="palette"><div><div class="swatch" style="background:#0f766e"></div>湖蓝青<small>#0F766E · 品牌</small></div><div><div class="swatch" style="background:#f8faf9"></div>暖白<small>#F8FAF9 · 背景</small></div><div><div class="swatch" style="background:#f4a261"></div>日光橙<small>#F4A261 · 点缀</small></div><div><div class="swatch" style="background:#115e59"></div>深青<small>#115E59 · 交互</small></div><div><div class="swatch" style="background:#e6f4f1"></div>浅薄荷<small>#E6F4F1 · 衬底</small></div><div><div class="swatch" style="background:#18332f"></div>墨青灰<small>#18332F · 正文</small></div></div><a class="download" href="/brand/logo.svg" download="TravelMindAI-logo.svg">下载横版 Logo · SVG</a><a class="download" href="/brand/logo-mark.svg" download="TravelMindAI-mark.svg">下载独立图标 · SVG</a></dialog>
</div>
</template>

<style scoped>
    .landing-page{isolation:isolate;position:relative;min-height:100svh;background:#f8faf9;--brand:#0f766e;--hover:#115e59;--tint:#e6f4f1;--paper:#f8faf9;--ink:#18332f;--muted:#586f69;--orange:#f4a261;--line:rgba(27,93,79,.14)}
    *{box-sizing:border-box}.landing-page{margin:0;background:var(--paper);color:var(--ink);font-family:Inter,"Segoe UI","Microsoft YaHei",sans-serif;font-size:14px}button,textarea{font:inherit}button,a{-webkit-tap-highlight-color:transparent}button{cursor:pointer}button:focus-visible,a:focus-visible{outline:3px solid var(--brand);outline-offset:5px}button{color:inherit}a{color:inherit;text-decoration:none}
    .backdrop{position:fixed;inset:0;z-index:-2;overflow:hidden;background:#f2f7f2}.landscape{width:100%;height:100%;display:block}.painted-mountains{animation:mountain-drift 55s ease-in-out infinite alternate;transform-origin:center}.valley-mist{animation:mist-drift 38s ease-in-out infinite alternate}.backdrop[data-paused="true"] *{animation-play-state:paused!important}@keyframes mountain-drift{from{transform:translate(-4px,2px)}to{transform:translate(5px,-3px)}}@keyframes mist-drift{from{transform:translate(-20px,3px);opacity:.55}to{transform:translate(24px,-4px);opacity:.9}}
    .veil{position:fixed;inset:0;pointer-events:none;z-index:-1;background:radial-gradient(ellipse at 50% 43%,rgba(248,250,249,.93),rgba(248,250,249,.25) 58%,transparent 80%),linear-gradient(0deg,rgba(248,250,249,.5),transparent 22%,transparent 90%,rgba(248,250,249,.35))}
    .page{min-height:100svh;display:flex;flex-direction:column;padding:0 6vw}header{width:100%;max-width:1280px;margin:0 auto;height:112px;display:flex;justify-content:space-between;align-items:center;gap:24px}.brand{display:flex;align-items:center;gap:10px}.brand svg{width:43px;height:43px}.wordmark{font-size:25px;font-weight:650;letter-spacing:.8px;white-space:nowrap}.wordmark em{font-style:normal;color:var(--brand);font-weight:750;margin-left:3px}.nav{display:flex;gap:28px;align-items:center}.nav-link{border:0;background:none;padding:10px 0;color:var(--muted);font-size:13px}.nav-link:hover{color:var(--brand)}.nav-action{padding:10px 18px;border:1px solid var(--line);border-radius:100px;background:rgba(255,255,255,.5);font-size:13px}
    main{flex:1;display:flex;align-items:center;justify-content:center;padding:45px 0 80px}.hero{width:min(100%,820px);text-align:center}.eyebrow{display:flex;align-items:center;justify-content:center;gap:10px;color:var(--brand);font-size:13px;letter-spacing:2px;margin:0 0 24px}.eyebrow svg{width:19px;height:19px}.hero h1{font-size:clamp(32px,3.9vw,54px);font-weight:500;line-height:1.48;letter-spacing:3px;margin:0 0 19px}.hero h1 span{color:var(--brand)}.subtitle{color:var(--muted);font-size:15px;letter-spacing:.65px;line-height:1.9;margin:0 0 40px}
    .composer{background:rgba(255,255,255,.63);border:1.5px solid rgba(255,255,255,.95);border-radius:24px;padding:23px 24px 18px;box-shadow:0 16px 60px -28px rgba(22,78,62,.2),0 0 0 1px rgba(38,110,88,.035);backdrop-filter:blur(18px);transition:box-shadow .25s;text-align:left}.composer:focus-within{box-shadow:0 16px 60px -28px rgba(22,78,62,.22),0 0 0 2px rgba(15,118,110,.2)}textarea{border:0;background:none;outline:none;resize:none;width:100%;min-height:80px;color:var(--ink);font-size:16px;line-height:1.8}textarea::placeholder{color:#647a74}.composer-bottom{display:flex;justify-content:space-between;align-items:center;gap:12px}.mode{display:inline-flex;align-items:center;gap:7px;border-radius:100px;padding:7px 12px;color:var(--brand);background:var(--tint);font-size:12px}.mode svg{width:15px;height:15px}.input-hint{margin-left:auto;font-size:11px;color:var(--muted)}.send{width:42px;height:42px;border-radius:50%;background:var(--brand);color:white;border:0;display:grid;place-items:center;transition:background .2s,transform .2s}.send:hover{background:var(--hover);transform:translateY(-2px)}.send svg{width:21px;height:21px}
    .suggestions{display:flex;gap:10px;justify-content:center;flex-wrap:wrap;margin-top:22px}.suggestion{border:1px solid var(--line);border-radius:100px;background:rgba(255,255,255,.38);padding:10px 15px;color:#46635a;font-size:12px;transition:background .2s,transform .2s}.suggestion:hover{background:rgba(255,255,255,.85);transform:translateY(-2px)}.suggestion span{margin-right:7px;color:var(--brand)}.quiet-note{margin:29px 0 0;font-size:11px;letter-spacing:1px;color:#647a73}.quiet-note i{display:inline-block;background:var(--orange);width:5px;height:5px;border-radius:50%;margin:0 9px 2px}
    /* 仅下移输入区及其下方提示，不影响标题和页脚的位置。 */
    .composer,.suggestions,.quiet-note{position:relative;top:50px}
    footer{max-width:1280px;width:100%;margin:0 auto;display:flex;justify-content:space-between;gap:20px;align-items:center;padding:24px 0 28px;color:var(--muted);font-size:11px;letter-spacing:.35px}.footer-brand{display:flex;align-items:center;gap:15px}.footer-brand span{color:#698079}.motion{border:0;background:transparent;color:var(--muted);font-size:11px;padding:8px;display:flex;align-items:center;gap:8px}.motion-dot{width:6px;height:6px;border-radius:50%;background:var(--brand)}.motion[aria-pressed="true"] .motion-dot{background:#899b95}
    .brand-dialog{max-width:520px;width:calc(100% - 36px);max-height:85svh;overflow:auto;border:1px solid white;border-radius:24px;background:var(--paper);padding:30px;color:var(--ink);box-shadow:0 30px 100px rgba(24,51,47,.16)}.brand-dialog::backdrop{background:rgba(24,51,47,.2);backdrop-filter:blur(7px)}.dialog-head{display:flex;justify-content:space-between;align-items:center}.dialog-head h2{font-size:20px;font-weight:550;margin:0}.close{border:0;background:var(--tint);width:32px;height:32px;border-radius:50%;font-size:20px}.large-logo{padding:36px 0 26px;justify-content:center}.large-logo .wordmark{font-size:29px}.large-logo svg{height:52px;width:52px}.dialog-copy{color:var(--muted);font-size:13px;line-height:1.9}.palette{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;margin:23px 0}.swatch{height:62px;border-radius:12px;margin-bottom:8px;border:1px solid var(--line)}.palette small{display:block;color:var(--muted);font-size:10px;margin-top:4px}.download{display:block;text-align:center;border:1px solid var(--line);padding:12px;border-radius:12px;font-size:13px;margin-top:10px}.download:hover{background:var(--tint)}
    @media(min-width:1700px){main{padding-bottom:110px}.hero{width:880px}}
    @media(min-width:601px) and (max-height:800px){header{height:94px}main{padding:20px 0 24px}.subtitle{margin-bottom:30px}.quiet-note{margin-top:22px}footer{padding:16px 0 20px}}
    @media(max-width:600px){.page{padding:0 22px}header{height:88px}.brand{gap:6px}.brand svg{width:34px;height:34px}.wordmark{font-size:21px}.nav{gap:12px}.nav-link{font-size:12px}.nav-link{display:none}.nav-action{padding:9px 12px;font-size:12px}main{padding:45px 0 60px}.hero h1{font-size:34px;letter-spacing:1px}.hero h1 span{display:block}.eyebrow{margin-bottom:20px;font-size:11px}.subtitle{font-size:13px;max-width:265px;margin:0 auto 30px}.composer{padding:18px 17px 14px;border-radius:21px}textarea{font-size:14px;min-height:100px}.input-hint{display:none}.suggestions{gap:8px}.suggestion{font-size:11px;padding:9px 12px}.quiet-note{font-size:10px;letter-spacing:0}footer{padding:16px 0 20px;font-size:10px;align-items:flex-end}.footer-brand{display:block;line-height:1.9}.footer-brand span{display:block}.motion{font-size:10px;white-space:nowrap;padding-right:0}.palette{gap:10px}.brand-dialog{padding:24px}.large-logo .wordmark{font-size:25px}}
    @media(max-width:370px){header{gap:8px}.wordmark{font-size:18px;letter-spacing:.6px}.brand svg{width:29px;height:29px}.nav-link{font-size:11px}.large-logo{gap:6px}.large-logo .wordmark{font-size:21px}}
    @media(prefers-reduced-motion:reduce){*,*::before,*::after{transition:none!important}}
  
    button:disabled{opacity:.55;cursor:wait}.entry-error{max-width:820px;margin:0 auto 18px;color:#a33d35;font-size:13px;line-height:1.7}
</style>
