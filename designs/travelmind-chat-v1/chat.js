/** 独立交互设计稿：所有对话和附件状态仅存于本页内存，不调用真实接口。 */
const $ = selector => document.querySelector(selector)
const icon = name => `<svg aria-hidden="true"><use href="#i-${name}"></use></svg>`
const escapeText = value => String(value).replace(/[&<>"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char]))
const examples = {
  user: [
    { id: 'u1', title: '杭州三日，慢慢逛西湖', group: '今天', question: '想去杭州玩三天，希望轻松一点，留些时间喝茶。', answer: 'hangzhou' },
    { id: 'u2', title: '周末去安吉，看山和竹海', group: '今天', question: '周末想去安吉，喜欢山景，希望行程不要太赶。', answer: 'weekend' },
    { id: 'u3', title: '带爸妈去苏州，少走路的安排', group: '昨天', question: '想带爸妈去苏州，三天两晚，尽量少走路。', answer: 'parents' },
    { id: 'u4', title: '云南七日 · 大理到丽江', group: '最近 7 天', question: '第一次去云南，想安排大理和丽江。', answer: 'general' },
    { id: 'u5', title: '成都有哪些值得逛的街巷', group: '最近 7 天', question: '成都有哪些可以边散步边吃东西的街巷？', answer: 'general' },
  ],
  admin: [
    { id: 'a1', title: '杭州周末散步路线', group: '今天', question: '周末在杭州散步，帮我找一条轻松的路线。', answer: 'hangzhou' },
    { id: 'a2', title: '苏州园林慢游', group: '昨天', question: '苏州园林怎么安排比较轻松？', answer: 'general' },
  ],
}
const collections = Object.fromEntries(Object.entries(examples).map(([role, rows]) => [role, rows.map(row => ({ ...row, draft: '', files: [], messages: [{ role: 'user', text: row.question }, { role: 'assistant', kind: row.answer }] }))]))
let role = 'user'
let currentId = null
let blank = { draft: '', files: [], messages: [] }
let menuId = null
let toastTimer
const current = () => collections[role].find(row => row.id === currentId) ?? blank

function notify(text) {
  clearTimeout(toastTimer)
  $('#toast').textContent = text; $('#toast').hidden = false
  toastTimer = setTimeout(() => { $('#toast').hidden = true }, 3300)
}
function closePopovers() { document.querySelectorAll(':popover-open').forEach(node => node.hidePopover()) }
function placePopover(menu, anchor, above = false) {
  const box = anchor.getBoundingClientRect()
  const x = menu.id === 'session-menu' ? box.right + 6 : box.left
  const y = above ? box.top - menu.offsetHeight - 9 : box.bottom + 7
  menu.style.left = `${Math.max(12, Math.min(x, innerWidth - menu.offsetWidth - 12))}px`
  menu.style.top = `${Math.max(12, Math.min(y, innerHeight - menu.offsetHeight - 12))}px`
}
function hideMobileSidebar() { $('.shell').classList.remove('mobile-open') }
function renderHistory() {
  const filter = $('#search').value.trim().toLowerCase()
  const rows = collections[role].filter(row => row.title.toLowerCase().includes(filter))
  $('#history-count').textContent = collections[role].length
  let group = ''
  $('#history-list').innerHTML = rows.map(row => {
    const heading = row.group !== group ? `<h2 class="history-group">${escapeText(row.group)}</h2>` : ''
    group = row.group
    return `${heading}<div class="history-row ${row.id === currentId ? 'selected' : ''}"><button class="history-select" data-session="${row.id}" ${row.id === currentId ? 'aria-current="page"' : ''} title="${escapeText(row.title)}">${icon('chat')}<span>${escapeText(row.title)}</span></button><button class="icon-button row-more" data-menu="${row.id}" aria-label="${escapeText(row.title)}：更多操作" aria-haspopup="menu">${icon('more')}</button></div>`
  }).join('') || `<p class="empty-history">${filter ? '没有找到相关对话。<br>试试另一个关键词。' : '下一段旅程，从新对话开始。'}</p>`
}
const fileUrls = new Map()
function fileCard(file, index = null) {
  if (!fileUrls.has(file)) fileUrls.set(file, URL.createObjectURL(file))
  return `<div class="attachment"><a class="file-open" href="${fileUrls.get(file)}" target="_blank" rel="noopener" aria-label="打开附件 ${escapeText(file.name)}"><span class="file-kind">${escapeText(file.name.split('.').pop().toUpperCase().slice(0,8))}</span><span class="attachment-info"><strong title="${escapeText(file.name)}">${escapeText(file.name)}</strong><small>${formatSize(file.size)} · 已选择</small></span></a>${index === null ? '' : `<button class="icon-button" type="button" data-remove="${index}" aria-label="移除${escapeText(file.name)}">${icon('close')}</button>`}</div>`
}
window.addEventListener('beforeunload', () => fileUrls.forEach(url => URL.revokeObjectURL(url)))
function renderFiles() {
  $('#attachments').innerHTML = current().files.map((file, index) => fileCard(file, index)).join('')
  $('.send').disabled = !$('#message').value.trim() && !current().files.length
}
function formatSize(size) { return size >= 1_000_000 ? `${(size / 1_000_000).toFixed(1)} MB` : `${Math.max(1, Math.round(size / 1000))} KB` }
function answerMarkup(kind) {
  if (kind === 'hangzhou') return `<p>当然可以。把行程留白一些，杭州很适合慢慢走。</p><h2>三天两晚，把时间留给西湖。</h2><div class="trip-summary"><span>杭州 · 3 天</span><span>轻松慢游</span><span>湖景 / 茶园 / 街巷</span></div><div class="trip-day"><span class="day-number">DAY 01</span><div><strong>沿着湖边，慢慢进入假期</strong><p>湖滨散步 → 柳浪闻莺 → 南山路晚餐<br>第一天不排太满，给抵达和休息留出时间。</p></div></div><div class="trip-day"><span class="day-number">DAY 02</span><div><strong>山间喝茶，看看绿意</strong><p>龙井村 → 茶园小路 → 找一家茶馆坐坐<br>根据体力选择步行距离，不必走完整条路线。</p></div></div><div class="trip-day"><span class="day-number">DAY 03</span><div><strong>逛逛街巷，带一点杭州回家</strong><p>小河直街 → 运河边午餐 → 返程<br>具体安排可以按返程时间再调整。</p></div></div><p style="margin-top:22px">你准备从哪里出发？大概预算是多少？我可以接着帮你细化。</p>`
  if (kind === 'weekend') return '<p>山景和竹海很适合一个慢节奏周末。可以把一天留给山间散步，另一天安排民宿休息和附近短途游。</p><p>你从哪个城市出发？准备自驾，还是乘坐公共交通？</p>'
  if (kind === 'parents') return '<p>带爸妈出游，可以把重点放在交通方便、步行短、休息充足。每天保留一个主要景点，午后安排一段休息时间。</p><p>爸妈平时能接受多长时间的步行？我会按这个节奏来安排。</p>'
  return '<p>已收到你的旅行想法。我们可以先确定目的地、时间和节奏，再慢慢补充沿途的安排。</p><p>你准备从哪里出发，想玩几天？</p>'
}
function renderMessages() {
  $('#messages').innerHTML = current().messages.map(message => message.role === 'user'
    ? `<article class="message user"><div class="user-content">${message.files?.length ? `<div class="message-files">${message.files.map(file => fileCard(file)).join('')}</div>` : ''}<div class="user-bubble">${escapeText(message.text)}</div></div></article>`
    : `<article class="message assistant"><div class="assistant-heading"><img src="logo-mark.svg" alt="">TravelMind AI<span class="example-badge">示例回复</span></div><div class="assistant-content">${answerMarkup(message.kind)}<div class="answer-actions"><button class="copy-answer">复制回复</button><span>以上为界面示例，未调用智能体</span></div></div></article>`).join('')
}
function render() {
  const chat = current()
  $('#chat-area').classList.toggle('is-empty', !chat.messages.length)
  $('#conversation-title').textContent = currentId ? chat.title : '新对话'
  $('#message').value = chat.draft
  $('#composer-note').innerHTML = chat.messages.length ? '交互设计预览 · 文件不会上传，回复为示例' : '把行程交给规划，把期待留给出发。<i></i>'
  renderHistory(); renderMessages(); renderFiles()
}
function newChat() {
  closePopovers(); currentId = null; $('#search').value = ''; hideMobileSidebar(); render(); $('#message').focus()
}
$('.new-chat').onclick = newChat
$('.sidebar-brand .brand').onclick = event => { event.preventDefault(); newChat() }
$('#search').oninput = renderHistory
$('#history-list').onclick = event => {
  const row = event.target.closest('[data-session]')
  if (row) { closePopovers(); currentId = row.dataset.session; hideMobileSidebar(); render(); return }
  const button = event.target.closest('[data-menu]')
  if (button) {
    closePopovers(); menuId = button.dataset.menu
    $('#session-menu').showPopover(); placePopover($('#session-menu'), button)
  }
}
$('#rename-action').onclick = () => {
  closePopovers(); const chat = collections[role].find(row => row.id === menuId)
  if (!chat) return
  $('#new-title').value = chat.title; $('#rename-dialog').showModal(); $('#new-title').focus(); $('#new-title').select()
}
$('#rename-form').onsubmit = event => {
  event.preventDefault(); const title = $('#new-title').value.trim()
  if (!title) { $('#new-title').setCustomValidity('请输入对话名称'); $('#new-title').reportValidity(); return }
  const chat = collections[role].find(row => row.id === menuId)
  if (chat) chat.title = title
  $('#rename-dialog').close(); render(); notify('对话名称已更新（本页预览）')
}
$('#new-title').oninput = () => $('#new-title').setCustomValidity('')
$('#delete-action').onclick = () => {
  closePopovers(); const chat = collections[role].find(row => row.id === menuId)
  if (chat) { $('#delete-name').textContent = chat.title; $('#delete-dialog').showModal() }
}
$('#confirm-delete').onclick = () => {
  collections[role] = collections[role].filter(row => row.id !== menuId)
  if (currentId === menuId) currentId = null
  $('#delete-dialog').close(); render(); notify('已从本页预览中删除对话')
}
document.querySelectorAll('.dialog-close').forEach(button => button.onclick = () => button.closest('dialog').close())
document.querySelectorAll('dialog').forEach(dialog => dialog.addEventListener('click', event => { if (event.target === dialog && (event.offsetX < 0 || event.offsetX > dialog.offsetWidth || event.offsetY < 0 || event.offsetY > dialog.offsetHeight)) dialog.close() }))

/** 输入与附件只在本地选择，演示发送后的位置变化，不模拟上传成功。 */
$('#message').oninput = () => { current().draft = $('#message').value; renderFiles() }
$('#message').onkeydown = event => {
  if (event.key === 'Enter' && !event.shiftKey && !event.isComposing && event.keyCode !== 229) { event.preventDefault(); $('#composer').requestSubmit() }
}
document.querySelectorAll('[data-prompt]').forEach(button => button.onclick = () => { current().draft = button.dataset.prompt; $('#message').value = current().draft; renderFiles(); $('#message').focus() })
$('#composer').onsubmit = event => {
  event.preventDefault(); const text = $('#message').value.trim(); let chat = current()
  if (!text && !chat.files.length) return
  if (!currentId) {
    chat = { ...blank, id: crypto.randomUUID(), title: (text || `关于${blank.files[0].name}`).slice(0, 80), group: '今天' }
    collections[role].unshift(chat); currentId = chat.id; blank = { draft: '', files: [], messages: [] }
  }
  chat.messages.push({ role: 'user', text: text || '请参考这份资料帮我规划旅行。', files: [...chat.files] }, { role: 'assistant', kind: text.includes('杭州') ? 'hangzhou' : 'general' })
  chat.draft = ''; chat.files = []; render(); $('#messages').scrollTop = $('#messages').scrollHeight; $('#message').focus()
}
$('#upload-menu').addEventListener('toggle', event => { if (event.newState === 'open') placePopover($('#upload-menu'), $('.attach-button'), true) })
$('#choose-files').onclick = () => { closePopovers(); $('#file-input').click() }
function addFiles(files) {
  let added = 0
  for (const file of files) {
    if (!/\.(pdf|docx|txt|md|markdown|png|jpe?g|webp)$/i.test(file.name)) { notify('请选择 PDF、Word、图片或文本文件'); continue }
    if (!file.size || file.size > (/\.pdf$/i.test(file.name) ? 30_000_000 : 10 * 1024 * 1024)) { notify('文件不能为空；PDF最大30MB，其他文件最大10MiB'); continue }
    if (current().files.some(item => item.name === file.name && item.size === file.size)) continue
    if (current().files.length >= 3) { notify('每条消息最多添加3份文件'); break }
    current().files.push(file); added++
  }
  renderFiles()
  if (added) notify(`已选择 ${added} 份资料，仅在本页预览，不会上传`)
}
$('#file-input').onchange = event => { addFiles(Array.from(event.target.files)); event.target.value = '' }
let dragDepth = 0
const isFileDrag = event => Array.from(event.dataTransfer?.types ?? []).includes('Files')
$('#composer').ondragenter = event => { if (isFileDrag(event)) { event.preventDefault(); dragDepth++; $('#composer').classList.add('is-dragging'); closePopovers() } }
$('#composer').ondragover = event => { if (isFileDrag(event)) { event.preventDefault(); event.dataTransfer.dropEffect = 'copy' } }
$('#composer').ondragleave = () => { if (!--dragDepth || dragDepth < 0) { dragDepth = 0; $('#composer').classList.remove('is-dragging') } }
$('#composer').ondrop = event => { if (!isFileDrag(event)) return; event.preventDefault(); dragDepth = 0; $('#composer').classList.remove('is-dragging'); addFiles(Array.from(event.dataTransfer.files)) }
for (const name of ['dragover', 'drop']) window.addEventListener(name, event => { if (isFileDrag(event)) event.preventDefault() })
$('#attachments').onclick = event => { const button = event.target.closest('[data-remove]'); if (button) { current().files.splice(Number(button.dataset.remove), 1); renderFiles() } }
$('#account-menu').addEventListener('toggle', event => { if (event.newState === 'open') placePopover($('#account-menu'), $('#account-button'), true) })
function showInfo(title, content) { closePopovers(); $('#info-title').textContent = title; $('#info-text').textContent = content; $('#info-dialog').showModal() }
$('#logout').onclick = () => showInfo('退出登录', '这里展示账号菜单的退出入口。当前为独立设计稿，不会影响正式页面的登录状态。')
$('#knowledge-entry').onclick = () => showInfo('知识库管理', '管理员从这里进入共享知识库，管理旅行资料。个人上传的攻略只关联自己的对话，不进入共享知识库。本轮先确认入口设计。')
$('#role').onchange = event => {
  role = event.target.value; currentId = null; blank = { draft: '', files: [], messages: [] }; $('#search').value = ''; closePopovers()
  const name = role === 'admin' ? 'TravelMind 内容管理员' : 'charlie'
  $('#account-name').textContent = name; $('#profile-name').textContent = name; $('#avatar').textContent = role === 'admin' ? 'T' : 'C'
  $('#account-role').textContent = role === 'admin' ? '管理员账号' : '个人账号'
  $('#profile-role').textContent = role === 'admin' ? '管理员 · 共享知识库管理' : '个人账号 · 私人旅行对话'
  $('#knowledge-entry').hidden = role !== 'admin'; render()
}
$('.collapse').onclick = () => { closePopovers(); if (innerWidth <= 700) hideMobileSidebar(); else $('.shell').classList.add('collapsed') }
$('.expand').onclick = () => { if (innerWidth <= 700) $('.shell').classList.add('mobile-open'); else $('.shell').classList.remove('collapsed') }
$('.sidebar-scrim').onclick = hideMobileSidebar
document.addEventListener('keydown', event => { if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') { event.preventDefault(); newChat() } if (event.key === 'Escape') hideMobileSidebar() })
window.addEventListener('resize', closePopovers)
$('#messages').onclick = async event => {
  if (!event.target.closest('.copy-answer')) return
  const answer = event.target.closest('.assistant-content').cloneNode(true)
  answer.querySelector('.answer-actions')?.remove()
  try { await navigator.clipboard.writeText(answer.textContent); notify('已复制示例回复') } catch { notify('复制未完成，请选中文字后复制') }
}
render()
