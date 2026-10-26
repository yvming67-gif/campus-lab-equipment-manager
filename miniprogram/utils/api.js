const config = require('../config');
const SESSION_KEY = 'campus-mini-session';
const DRAFT_KEY = 'campus-mini-draft';
const DRAFTS_KEY = 'campus-mini-pending-drafts';
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
  wx.removeStorageSync(SESSION_KEY); wx.removeStorageSync(DRAFT_KEY); wx.removeStorageSync(DRAFTS_KEY); wx.removeStorageSync('campus-mini-base');
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
        const e = new Error(method === 'POST' && path.indexOf('/bookings/') === 0 ? '未能确认操作结果，请保持内容并重试，或查看预约记录' : reason);
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
  });
  submissions.set(flight, sending);
  try { return await sending; } finally { if (submissions.get(flight) === sending) submissions.delete(flight); }
}
function errorText(error) {
  const names = { starts_at: '开始时间', ends_at: '结束时间', purpose: '使用目的', participants: '参与人数', reason: '取消原因', request_key: '请求编号', username: '账号', password: '密码', page: '页码' };
  const fields = Object.keys(error.fields || {}).map(k => (names[k] || k) + '：' + error.fields[k].join('；'));
  return fields.length ? fields.join('\n') : error.message || '操作未完成，请重试';
}
module.exports = { baseUrl, normalizeBase, session, clearSession, requireLogin, request, login, logout, submitBooking, draft, errorText, uuid };
