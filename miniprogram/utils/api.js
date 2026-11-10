const config = require('../config');
const SESSION_KEY = 'campus-mini-session';
const DRAFT_KEY = 'campus-mini-draft';
const DRAFTS_KEY = 'campus-mini-pending-drafts';
const ACTIONS_KEY = 'campus-mini-action-drafts';
const CONFIGURED_BASE = normalizeBase(config.backendBaseUrl);
const PREFIX = '/api/mini/v1';
let redirecting = false;
const submissions = new Map();

function baseUrl() { return CONFIGURED_BASE; }
function normalizeBase(value) {
  const base = String(value || '').trim().replace(/\/+$/, '');
  if (!/^https:\/\/[a-z0-9.-]+(?::\d+)?$/i.test(base) && !/^http:\/\/(?:127\.0\.0\.1|localhost)(?::\d+)?$/i.test(base)) {
    throw new Error('请填写有效的HTTPS服务地址，或本机http://127.0.0.1:端口');
  }
  return base;
}
function sameSession(saved) {
  const current = wx.getStorageSync(SESSION_KEY);
  return !!(saved && current && current.token === saved.token && current.base === saved.base && current.user && current.user.id === saved.user.id);
}
function clearSession(expected) {
  if (expected && !sameSession(expected)) return false;
  wx.removeStorageSync(SESSION_KEY); wx.removeStorageSync(DRAFT_KEY); wx.removeStorageSync(DRAFTS_KEY); wx.removeStorageSync(ACTIONS_KEY); wx.removeStorageSync('campus-mini-booking-filter'); wx.removeStorageSync('campus-mini-base');
  return true;
}
function session() {
  const value = wx.getStorageSync(SESSION_KEY);
  if (!value || !value.token || !value.user || value.expiresAt <= Date.now() || value.base !== CONFIGURED_BASE) {
    if (value) clearSession();
    return null;
  }
  if (!value.draftScope) { value.draftScope = uuid(); wx.setStorageSync(SESSION_KEY, value); }
  return value;
}
function goLogin() {
  if (!redirecting) {
    redirecting = true;
    wx.reLaunch({ url: '/pages/login/index', complete() { redirecting = false; } });
  }
}
function requireLogin() { if (session()) return true; goLogin(); return false; }
function request(path, method, data, options) {
  options = options || {};
  const auth = options.auth !== false;
  const saved = session();
  if (auth && !saved) { goLogin(); return Promise.reject(new Error('请先登录')); }
  return new Promise((resolve, reject) => {
    const header = { 'content-type': 'application/json' };
    if (auth) header.Authorization = 'Bearer ' + saved.token;
    wx.request({
      url: baseUrl() + PREFIX + path, method: method || 'GET', data: data || {}, header, timeout: options.timeout || 10000,
      success(result) {
        if (auth && !sameSession(saved)) {
          const stale = new Error('登录身份已变化，请重新打开当前页面'); stale.code = 'SESSION_CHANGED'; reject(stale); return;
        }
        const body = result.data;
        if (result.statusCode >= 200 && result.statusCode < 300 && body && body.ok === true) { resolve(body.data); return; }
        const detail = body && body.error || {};
        const error = new Error(detail.message || '服务暂时异常，请稍后重试');
        error.code = detail.code; error.fields = detail.fields || {}; error.status = result.statusCode;
        if (auth && (result.statusCode === 401 || detail.code === 'ROLE_NOT_SUPPORTED') && clearSession(saved)) goLogin();
        reject(error);
      },
      fail(detail) {
        const message = detail && detail.errMsg || '';
        let reason = '暂时无法连接服务，请检查网络；测试号真机请开启调试后重试';
        if (/timeout/i.test(message)) reason = '连接超时，云端演示可能正在唤醒，请稍候重试';
        else if (/url not in domain list|not in.*domain|合法域名/i.test(message)) reason = '域名校验未通过，测试号请在右上角开启调试，退出后重新进入';
        else if (/ssl|tls|certificate|cert_/i.test(message)) reason = '安全连接未建立，请检查手机网络和系统时间后重试';
        const mutating = method === 'POST' && (path.indexOf('/bookings/') === 0 || path === '/incidents/');
        const e = new Error(mutating ? '未能确认操作结果，请保持原内容后重试，或先核对相关记录' : reason);
        e.network = true; reject(e);
      }
    });
  });
}
async function login(username, password) {
  const data = await request('/auth/login/', 'POST', { username, password }, { auth: false, timeout: 60000 });
  wx.setStorageSync(SESSION_KEY, { token: data.token, user: data.user, base: CONFIGURED_BASE, expiresAt: Date.now() + data.expires_in * 1000, draftScope: uuid() });
  wx.removeStorageSync('campus-mini-base');
  wx.removeStorageSync(DRAFT_KEY);
  wx.removeStorageSync(DRAFTS_KEY);
  wx.removeStorageSync(ACTIONS_KEY);
  wx.removeStorageSync('campus-mini-booking-filter');
  return data;
}
async function logout() { const saved = session(); try { await request('/auth/logout/', 'POST', {}); } finally { if (saved) clearSession(saved); } }
function uuid() { return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, c => { const n = Math.floor(Math.random() * 16); return (c === 'x' ? n : (n & 3) | 8).toString(16); }); }
function pendingDrafts(saved) {
  const list = wx.getStorageSync(DRAFTS_KEY);
  if (list && list.scope === saved.draftScope && Array.isArray(list.items)) return list.items;
  const legacy = wx.getStorageSync(DRAFT_KEY);
  if (legacy && legacy.userId === saved.user.id && legacy.base === baseUrl() && (!legacy.scope || legacy.scope === saved.draftScope)) {
    const item = Object.assign({}, legacy, { scope: saved.draftScope });
    wx.setStorageSync(DRAFTS_KEY, { scope: saved.draftScope, items: [item] });
    return [item];
  }
  return [];
}
function saveDrafts(saved, items) {
  if (!sameSession(saved)) return;
  if (items.length) {
    wx.setStorageSync(DRAFTS_KEY, { scope: saved.draftScope, items });
    wx.setStorageSync(DRAFT_KEY, items[items.length - 1]);
  } else { wx.removeStorageSync(DRAFTS_KEY); wx.removeStorageSync(DRAFT_KEY); }
}
function draft(resourceId) {
  const saved = session();
  if (!saved) return null;
  const items = pendingDrafts(saved);
  for (let i = items.length - 1; i >= 0; i--) if (resourceId === undefined || items[i].values.resource_id === resourceId) return items[i];
  return null;
}
async function checkBooking(resourceId) {
  const saved=session(),pending=draft(resourceId);
  if(!saved || !pending)throw new Error('没有待核对的原预约，请刷新预约记录');
  const outcome=await request('/receipts/'+pending.key+'/');
  if(outcome.found && outcome.kind==='booking' && sameSession(saved)){
    saveDrafts(saved,pendingDrafts(saved).filter(item=>item.key!==pending.key));
  }
  return outcome;
}
async function submitBooking(values) {
  const saved = session();
  if (!saved) { goLogin(); throw new Error("请先登录"); }
  // This UUID is an idempotency key, never an authentication credential.
  const identity = JSON.stringify([values.resource_id, values.starts_at, values.ends_at, values.purpose, values.participants]);
  const flight = saved.draftScope + ':' + identity;
  if (submissions.has(flight)) return submissions.get(flight);
  let items = pendingDrafts(saved), pending = items.find(item => item.identity === identity);
  if (!pending) pending = { identity, values: Object.assign({}, values), key: uuid(), userId: saved.user.id, base: baseUrl(), scope: saved.draftScope };
  items = items.filter(item => item.key !== pending.key).concat(pending); saveDrafts(saved, items);
  const sending = request('/bookings/', 'POST', Object.assign({}, values, { request_key: pending.key })).then(result => {
    if (sameSession(saved)) {
      const legacy = wx.getStorageSync(DRAFT_KEY);
      saveDrafts(saved, pendingDrafts(saved).filter(item => item.key !== pending.key));
      // Other unresolved submissions remain in the session-scoped collection.
      if (legacy && legacy.key === pending.key) wx.removeStorageSync(DRAFT_KEY);
    }
    return result;
  }).catch(error=>{
    // A definite client/business rejection is transactional and can be corrected.
    // Timeouts and server failures remain unresolved for receipt lookup and replay.
    if(error.status>=400 && error.status<500 && sameSession(saved))
      saveDrafts(saved,pendingDrafts(saved).filter(item=>item.key!==pending.key));
    throw error;
  });
  submissions.set(flight, sending);
  try { return await sending; } finally { if (submissions.get(flight) === sending) submissions.delete(flight); }
}
function errorText(error) {
  const names = { starts_at: '开始时间', ends_at: '结束时间', purpose: '使用目的', participants: '参与人数', reason: '操作说明', request_key: '请求编号', username: '账号', password: '密码', page: '页码', description: '异常情况', occurred_at: '发现时间', resource_id: '异常资源', resource: '异常资源', reservation: '关联预约', kind: '异常类型', duration_minutes: '使用时长', slot_day: '候选日期' };
  const fields = Object.keys(error.fields || {}).map(k => (names[k] || k) + '：' + error.fields[k].join('；'));
  return fields.length ? fields.join('\n') : error.message || '操作未完成，请重试';
}
function actionItems(saved) {
  const value = wx.getStorageSync(ACTIONS_KEY);
  return value && value.scope === saved.draftScope && Array.isArray(value.items) ? value.items : [];
}
function saveActions(saved, items) {
  if (!sameSession(saved)) return;
  if (items.length) wx.setStorageSync(ACTIONS_KEY, {scope:saved.draftScope,items});
  else wx.removeStorageSync(ACTIONS_KEY);
}
function actionDraft(context) {
  const saved=session();
  return saved ? actionItems(saved).find(item=>item.context===context) || null : null;
}
async function submitAction(path, values, context) {
  const saved=session();
  if(!saved){goLogin();throw new Error('请先登录');}
  context=context || path;
  const identity=JSON.stringify([path,values]),flight=saved.draftScope+':action:'+context;
  const previous=actionItems(saved).find(item=>item.context===context);
  if(previous && previous.identity!==identity) throw new Error('上一项操作结果尚未确认，请先恢复原内容并重试，避免重复登记');
  if(submissions.has(flight)) return submissions.get(flight);
  const pending=previous || {context,identity,path,values:Object.assign({},values),key:uuid()};
  const all=actionItems(saved);
  if(!previous && all.length>=20)throw new Error('尚有较多未确认操作，请先核对并重试原操作');
  saveActions(saved,all.filter(item=>item.context!==context).concat(pending));
  const sending=request(path,'POST',Object.assign({},pending.values,{request_key:pending.key})).then(result=>{
    saveActions(saved,actionItems(saved).filter(item=>item.key!==pending.key));
    return result;
  }).catch(e=>{
    // A definite validation/rule rejection created no action; corrected fields may use a new key.
    // Network errors and server failures retain the same payload/key until the result is confirmed.
    if(e.status>=400 && e.status<500)saveActions(saved,actionItems(saved).filter(item=>item.key!==pending.key));
    throw e;
  });
  submissions.set(flight,sending);
  try{return await sending;}finally{if(submissions.get(flight)===sending)submissions.delete(flight);}
}
async function checkAction(context) {
  const saved=session(),pending=actionDraft(context);
  if(!saved || !pending)throw new Error('没有待核对的原操作，请刷新相关记录');
  const outcome=await request('/receipts/'+pending.key+'/');
  if(outcome.found)saveActions(saved,actionItems(saved).filter(item=>item.key!==pending.key));
  return outcome;
}
module.exports = { baseUrl, normalizeBase, session, clearSession, requireLogin, request, login, logout, submitBooking, draft, checkBooking, actionDraft, submitAction, checkAction, errorText, uuid };
