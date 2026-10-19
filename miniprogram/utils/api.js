const BASE_KEY = 'campus-mini-base';
const SESSION_KEY = 'campus-mini-session';
const DRAFT_KEY = 'campus-mini-draft';
const DEFAULT_BASE = 'http://127.0.0.1:8000';
const PREFIX = '/api/mini/v1';
let redirecting = false;

function baseUrl() { return wx.getStorageSync(BASE_KEY) || DEFAULT_BASE; }
function normalizeBase(value) {
  const base = String(value || '').trim().replace(/\/+$/, '');
  if (!/^https:\/\/[a-z0-9.-]+(?::\d+)?$/i.test(base) && !/^http:\/\/(?:127\.0\.0\.1|localhost)(?::\d+)?$/i.test(base)) {
    throw new Error('请填写有效的HTTPS服务地址，或本机http://127.0.0.1:端口');
  }
  return base;
}
function setBase(value) {
  const base = normalizeBase(value);
  if (base !== baseUrl()) clearSession();
  wx.setStorageSync(BASE_KEY, base);
  return base;
}
function clearSession() { wx.removeStorageSync(SESSION_KEY); wx.removeStorageSync(DRAFT_KEY); }
function session() {
  const value = wx.getStorageSync(SESSION_KEY);
  if (!value || !value.token || !value.user || value.expiresAt <= Date.now()) {
    if (value) clearSession();
    return null;
  }
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
      url: baseUrl() + PREFIX + path, method: method || 'GET', data: data || {}, header, timeout: 10000,
      success(result) {
        const body = result.data;
        if (result.statusCode >= 200 && result.statusCode < 300 && body && body.ok === true) { resolve(body.data); return; }
        const detail = body && body.error || {};
        const error = new Error(detail.message || '服务返回异常，请确认服务地址并重试');
        error.code = detail.code; error.fields = detail.fields || {}; error.status = result.statusCode;
        if (auth && (result.statusCode === 401 || detail.code === 'ROLE_NOT_SUPPORTED')) { clearSession(); goLogin(); }
        reject(error);
      },
      fail() { const e = new Error(method === 'POST' && path.indexOf('/bookings/') === 0 ? '未能确认操作结果，请保持内容并重试，或查看预约记录' : '暂时无法连接服务，请检查启动窗口中的地址后重试'); e.network = true; reject(e); }
    });
  });
}
async function login(username, password) {
  const data = await request('/auth/login/', 'POST', { username, password }, { auth: false });
  wx.setStorageSync(SESSION_KEY, { token: data.token, user: data.user, expiresAt: Date.now() + data.expires_in * 1000 });
  wx.removeStorageSync(DRAFT_KEY);
  return data;
}
async function logout() { await request('/auth/logout/', 'POST', {}); clearSession(); }
function uuid() { return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, c => { const n = Math.floor(Math.random() * 16); return (c === 'x' ? n : (n & 3) | 8).toString(16); }); }
function draft() {
  const value = wx.getStorageSync(DRAFT_KEY), saved = session();
  return value && saved && value.userId === saved.user.id && value.base === baseUrl() ? value : null;
}
async function submitBooking(values) {
  if (!session()) { goLogin(); throw new Error("请先登录"); }
  // This UUID is an idempotency key, never an authentication credential.
  const identity = JSON.stringify([values.resource_id, values.starts_at, values.ends_at, values.purpose, values.participants]);
  let pending = draft();
  if (!pending || pending.identity !== identity) {
    pending = { identity, values, key: uuid(), userId: session().user.id, base: baseUrl() };
    wx.setStorageSync(DRAFT_KEY, pending);
  }
  const result = await request('/bookings/', 'POST', Object.assign({}, values, { request_key: pending.key }));
  wx.removeStorageSync(DRAFT_KEY);
  return result;
}
function errorText(error) {
  const names = { starts_at: '开始时间', ends_at: '结束时间', purpose: '使用目的', participants: '参与人数', reason: '取消原因', request_key: '请求编号', username: '账号', password: '密码', page: '页码' };
  const fields = Object.keys(error.fields || {}).map(k => (names[k] || k) + '：' + error.fields[k].join('；'));
  return fields.length ? fields.join('\n') : error.message || '操作未完成，请重试';
}
module.exports = { baseUrl, setBase, normalizeBase, session, clearSession, requireLogin, request, login, logout, submitBooking, draft, errorText, uuid };
