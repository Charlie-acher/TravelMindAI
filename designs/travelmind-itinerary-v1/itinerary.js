// 设计交互层：只使用截图场景的示例数据，查询填入草稿，不发送真实请求。
const days = [
  { day: 1, title: '第 1 天 · 湘江边走走', subtitle: '橘子洲 · 1 站', stops: [{time:'14:00',name:'橘子洲景区',duration:'2 小时 30 分钟',address:'湖南省长沙市岳麓区橘子洲头 2 号'}] },
  { day: 2, title: '第 2 天 · 山间到街巷', subtitle: '岳麓山 → 岳麓书院 → 黄兴路', stops: [{time:'09:00',name:'岳麓山国家重点风景名胜区',duration:'1 小时 30 分钟',address:'湖南省长沙市岳麓区登高路 58 号',transfer:'步行 · 预留 30 分钟'},{time:'11:00',name:'岳麓书院',duration:'2 小时',address:'湖南省长沙市岳麓区麓山路 273 号',transfer:'步行 · 预留 40 分钟'},{time:'14:00',name:'黄兴路步行街',duration:'1 小时',address:'湖南省长沙市芙蓉区黄兴南路 383 号'}] },
  { day: 3, title: '第 3 天 · 留一点时间给历史', subtitle: '湖南省博物馆 · 1 站', stops: [{time:'09:30',name:'湖南省博物馆',duration:'2 小时 30 分钟',address:'湖南省长沙市开福区东风路 50 号'}] }
];
const daysRoot = document.querySelector('#days');
daysRoot.innerHTML = days.map(day => `<section class="day-section" id="day-${day.day}" aria-label="第 ${day.day} 天"><header class="day-heading"><span class="day-number">0${day.day}</span><div><h2>${day.title}</h2><p>${day.subtitle}</p></div><button data-route="${day.day}">查路线 ↗</button></header><ol class="stops">${day.stops.map((stop,index) => `<li class="stop"><time>${stop.time}</time><div class="stop-body"><div class="stop-title"><h3>${stop.name}</h3><button class="map-button" data-place="${day.day}-${index}" aria-label="查看${stop.name}地图入口">地图 ↗</button></div><p class="duration">建议游玩 ${stop.duration}</p><details class="place-detail"><summary>地址与资料依据</summary><div class="detail-content"><p>${stop.address}</p><button data-source="${stop.name}">查看资料依据 · 1 条 ↗</button></div></details>${stop.transfer?`<p class="transfer">${stop.transfer} <span>· 待核实</span></p>`:''}</div></li>`).join('')}</ol></section>`).join('');

const message = document.querySelector('#message');
const send = document.querySelector('.send');
const dialog = document.querySelector('#info-dialog');
let toastTimer;
let restored = false;
function toast(text) {
  const node = document.querySelector('#toast');
  node.textContent = text;
  node.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => node.classList.remove('show'), 3000);
}
function fillQuestion(text) {
  message.value = text;
  send.disabled = false;
  message.focus();
  toast('已填入输入框，你可以补充后再发送');
}
function showDialog(title, paragraphs) {
  document.querySelector('#dialog-title').textContent = title;
  const content = document.querySelector('#dialog-content');
  content.replaceChildren(...paragraphs.map(text => { const p=document.createElement('p');p.textContent=text;return p; }));
  dialog.showModal();
}
document.querySelector('#close-dialog').onclick = () => dialog.close();
document.querySelector('#dialog-done').onclick = () => dialog.close();
// 点击按天跳转不折叠其他天，始终保留整程连续阅读。
document.querySelectorAll('.day-nav a').forEach(link => link.addEventListener('click', event => {
  event.preventDefault();
  document.querySelector(link.getAttribute('href')).scrollIntoView({behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'instant':'smooth',block:'start'});
}));
document.addEventListener('click', event => {
  const button = event.target.closest('button');
  if (!button) return;
  if (button.dataset.query) fillQuestion(button.dataset.query);
  if (button.dataset.route) {
    const day = days[Number(button.dataset.route)-1];
    fillQuestion(`请查询长沙第 ${day.day} 天的路线：${day.stops.map(stop=>stop.name).join(' → ')}。各站之间怎么走、耗时多久？请注明尚未核实的信息。`);
  }
  if (button.dataset.place) {
    const [day,index]=button.dataset.place.split('-').map(Number);
    const stop=days[day-1].stops[index];
    showDialog(stop.name,[stop.address,'地图入口设计预览。此示例未查询地图坐标，不展示未经核实的路线。']);
  }
  if (button.dataset.source) showDialog('资料依据',[button.dataset.source,'这里展示资料名称、原文摘录与来源链接。本设计稿没有加载真实引用，正式接入时沿用现有资料依据。']);
  const action=button.dataset.action;
  if (action==='changes') showDialog(restored?'版本记录 · 已恢复':'本次调整 · 草稿 v2',[restored?'已恢复上一版的设计状态。':'第 2 天的日期或活动安排已调整。','此处演示版本与变更摘要，不改写真实会话记录。']);
  if (action==='undo') {
    restored=!restored;
    document.querySelector('#version').innerHTML=restored?'草稿 v1 <span>↗</span>':'草稿 v2 <span>↗</span>';
    document.querySelector('#change-note>span:nth-child(2)').textContent=restored?'已恢复上一版 · 交互示例':'已调整第 2 天的活动安排';
    button.textContent=restored?'恢复修改':'撤销';
    toast('设计预览：版本状态已切换，示例行程保持不变');
  }
  if (action==='tickets') showDialog('门票与往返交通',['门票、机票与火车票尚待查询。请先确认出行日期，再核对余票、价格及预约要求。','正式入口沿用 12306、航空公司与景区官方渠道；此设计预览不进行订票。']);
  if (action==='attachment') showDialog('添加旅行资料',['此设计稿聚焦旅行草稿卡片，资料上传仍使用主程序现有入口。']);
  if (action==='new') {message.value='';send.disabled=true;toast('当前为旅行草稿设计预览，未创建真实对话');}
  if (action==='history') {document.querySelector('#scroll-area').scrollTo({top:0,behavior:'smooth'});}
});
document.querySelector('.search input').addEventListener('input', event => {
  document.querySelector('.history').hidden = !'从银川出发，去长沙玩三天'.includes(event.target.value.trim());
});
message.addEventListener('input',()=>{send.disabled=!message.value.trim();});
document.querySelector('#composer').addEventListener('submit', event => {
  event.preventDefault();
  if (!message.value.trim()) return;
  showDialog('已提交调整想法 · 设计演示',[message.value,'这版用于确认界面与交互，不调用智能体，不产生真实行程修改。']);
});
message.addEventListener('keydown',event=>{if(event.key==='Enter'&&!event.shiftKey&&!event.isComposing){event.preventDefault();document.querySelector('#composer').requestSubmit();}});
document.addEventListener('keydown',event=>{if((event.ctrlKey||event.metaKey)&&event.key.toLowerCase()==='k'&&!dialog.open){event.preventDefault();document.querySelector('.new-chat').click();message.focus();}});
