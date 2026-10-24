const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '../../miniprogram');
let api, storage, requests, reply, navigations;
function setup() {
  storage = new Map(); requests = []; navigations = [];
  reply = r => r.success({ statusCode: 200, data: { ok: true, data: {} } });
  global.wx = {
    getStorageSync: k => storage.get(k), setStorageSync: (k, v) => storage.set(k, v), removeStorageSync: k => storage.delete(k),
    request: r => { requests.push(r); reply(r); },
    reLaunch: r => { navigations.push(r.url); if (r.complete) r.complete(); },
    navigateTo: r => navigations.push(r.url), redirectTo: r => navigations.push(r.url), switchTab: r => navigations.push(r.url),
    showModal: r => r.success({ confirm: true }), showToast() {}, stopPullDownRefresh() {}
  };
  for (const name of ['utils/api.js', 'controllers.js']) delete require.cache[require.resolve(path.join(root, name))];
  api = require(path.join(root, 'utils/api'));
}
function signedIn() { storage.set('campus-mini-session', { token: 'a'.repeat(32), user: { id: 1, display_name: '模拟学生' }, base: api.baseUrl(), expiresAt: Date.now() + 7200000 }); }
function page(name) {
  const definition = require(path.join(root, 'controllers')).create(name);
  definition.setData = function (values) {
    for (const [key, value] of Object.entries(values)) {
      const parts = key.split('.'); let target = this.data;
      for (const p of parts.slice(0, -1)) { if (!target[p]) target[p] = {}; target = target[p]; }
      target[parts[parts.length - 1]] = value;
    }
  };
  return definition;
}
const values = () => ({ resource_id: 1, starts_at: '2027-01-01T10:00:00', ends_at: '2027-01-01T11:00:00', purpose: '模拟用途', participants: 1 });
test.beforeEach(setup);

test('overview failure remains visible when resource discovery succeeds',async()=>{
  signedIn();const p=page('resources');
  reply=r=>r.url.endsWith('/me/') ? r.fail({errMsg:'request:fail timeout'}) : r.success({statusCode:200,data:{ok:true,data:{items:[],labs:[],page:1,total:0,has_more:false}}});
  await p.onShow();assert.match(p.data.overviewError,/连接超时/);assert.equal(p.data.overviewReady,false);assert.equal(p.data.loading,false);assert.equal(p.data.overviewLoading,false);
});

test('overview refresh failure marks previous data stale and a successful retry clears the warning',async()=>{
  signedIn();const p=page('resources');p.setData({overviewReady:true,stats:{pending:4},upcoming:[{id:9}]});
  reply=r=>r.url.endsWith('/me/') ? r.fail({errMsg:'request:fail timeout'}) : r.success({statusCode:200,data:{ok:true,data:{items:[],labs:[],page:1,total:0,has_more:false}}});
  await p.onShow();assert.equal(p.data.stats.pending,4);assert.equal(p.data.upcoming[0].id,9);assert.equal(p.data.overviewReady,true);assert.ok(p.data.overviewError);
  reply=r=>r.success({statusCode:200,data:{ok:true,data:r.url.endsWith('/me/') ? {user:{display_name:'模拟学生'},stats:{pending:0},policy:{window_days:14},upcoming:[]} : {items:[],labs:[],page:1,total:0,has_more:false}}});
  await p.onShow();assert.equal(p.data.stats.pending,0);assert.equal(p.data.upcoming.length,0);assert.equal(p.data.overviewError,'');
});

test('slow overview does not delay discovery and older overview cannot overwrite new results',async()=>{
  signedIn();const p=page('resources'),pending=[];
  reply=r=>{if(r.url.endsWith('/me/'))pending.push(r);else r.success({statusCode:200,data:{ok:true,data:{items:[],labs:[],page:1,total:0,has_more:false}}});};
  const old=p.onShow();await new Promise(resolve=>setImmediate(resolve));
  assert.equal(p.data.loading,false);assert.equal(p.data.overviewLoading,true);assert.equal(requests.length,2);
  const latest=p.onShow();
  pending[1].success({statusCode:200,data:{ok:true,data:{user:{display_name:'模拟学生'},stats:{pending:2},policy:{window_days:14},upcoming:[]}}});await latest;
  pending[0].success({statusCode:200,data:{ok:true,data:{user:{display_name:'过期响应'},stats:{pending:8},policy:{window_days:14},upcoming:[]}}});await old;
  assert.equal(p.data.stats.pending,2);assert.equal(p.data.overviewReady,true);assert.equal(p.data.overviewError,'');
});

test('time-oriented discovery sends the displayed interval and carries it to the detail form',async()=>{
  signedIn();const p=page('resources');p.setData({timeSearch:true,slot:{date:'2027-01-02',start:'10:00',end:'11:00'}});
  reply=r=>r.success({statusCode:200,data:{ok:true,data:{items:[{id:1,kind:'EQUIPMENT',service_status:'NORMAL',available:true}],labs:[],page:1,has_more:false,total:1}}});
  await p.load(1);assert.equal(requests[0].data.starts_at,'2027-01-02T10:00:00');
  p.openResource({currentTarget:{dataset:{id:1}}});assert.match(navigations[0],/date=2027-01-02/);
  const detail=page('resource');detail.onLoad({id:1,date:'2027-01-02',start:'10:00',end:'11:00'});
  assert.equal(detail.data.form.date,'2027-01-02');assert.equal(detail.data.scheduleDay,'2027-01-02');
});

test('editing time or search before loading more replaces results instead of mixing queries',async()=>{
  signedIn();const p=page('resources');p.setData({timeSearch:true,slot:{date:'2027-01-02',start:'10:00',end:'11:00'}});
  reply=r=>r.success({statusCode:200,data:{ok:true,data:{items:[{id:requests.length,kind:'EQUIPMENT',service_status:'NORMAL'}],labs:[],page:1,has_more:true,total:2}}});
  await p.load(1);p.chooseQueryStart({detail:{value:'12:00'}});p.chooseQueryEnd({detail:{value:'13:00'}});await p.more();
  assert.equal(requests[1].data.page,1);assert.equal(p.data.items.length,1);assert.equal(p.data.items[0].id,2);assert.match(p.data.appliedTime,/12:00/);
});

test('native absent storage value does not reset a manually selected booking filter',async()=>{
  signedIn();global.wx.getStorageSync=k=>storage.has(k)?storage.get(k):'';
  const p=page('bookings');p.setData({statusIndex:2});
  reply=r=>r.success({statusCode:200,data:{ok:true,data:{items:[],page:1,has_more:false,total:0}}});
  await p.onShow();assert.equal(p.data.statusIndex,2);
  const home=page('resources');home.openBookings({currentTarget:{dataset:{status:'PENDING'}}});await p.onShow();
  assert.equal(p.data.statusIndex,1);assert.equal(storage.has('campus-mini-booking-filter'),false);
});

test('unread messages use server-side filtering instead of filtering one loaded page',async()=>{
  signedIn();const p=page('notifications');reply=r=>r.success({statusCode:200,data:{ok:true,data:{items:[],page:1,has_more:false,unread:4}}});
  await p.chooseUnread({currentTarget:{dataset:{mode:'unread'}}});assert.equal(requests[0].data.unread,'1');assert.equal(p.data.unread,4);
});

test('AUD-0017 older notification reads cannot overwrite a newer result',async()=>{
  signedIn();const pending=[];reply=r=>pending.push(r);const p=page('notifications');
  const first=p.load(1),second=p.load(1);
  pending[1].success({statusCode:200,data:{ok:true,data:{items:[{id:1,read_at:'2026-10-20'}],page:1,has_more:false,unread:0}}});await second;
  pending[0].success({statusCode:200,data:{ok:true,data:{items:[{id:1,read_at:null}],page:1,has_more:false,unread:1}}});await first;
  assert.equal(p.data.unread,0);assert.equal(p.data.items[0].unread,false);
});

test('AUD-0018 changing dates invalidates an outstanding availability response',async()=>{
  signedIn();const pending=[];reply=r=>pending.push(r);const p=page('resource');p.data.id=1;
  const checking=p.checkAvailability();p.chooseDate({detail:{value:'2027-01-15'}});
  pending[0].success({statusCode:200,data:{ok:true,data:{available:true,available_label:'时段可预约'}}});await checking;
  assert.equal(p.data.available,null);assert.equal(p.data.availability,'');
});

test('AUD-0036 offline logout clears local credentials and returns to login',async()=>{
  signedIn();reply=r=>r.fail({errMsg:'offline'});const p=page('profile');await p.signout();
  assert.equal(api.session(),null);assert.ok(navigations.includes('/pages/login/index'));
  const login=page('login');const count=navigations.length;login.onShow();assert.equal(navigations.length,count);
});

test('all seven native pages and lifecycle entry files exist', () => {
  const config = JSON.parse(fs.readFileSync(path.join(root, 'app.json'), 'utf8'));
  assert.equal(config.pages.length, 7);
  for (const stem of config.pages) for (const ext of ['.js', '.json', '.wxml', '.wxss']) assert.ok(fs.existsSync(path.join(root, stem + ext)));
  assert.equal(config.tabBar.list.length, 4);
});
test('WXML event bindings point to actual controller methods', () => {
  for (const name of ['login', 'resources', 'resource', 'bookings', 'booking', 'notifications', 'profile']) {
    const text = fs.readFileSync(path.join(root, 'pages', name, 'index.wxml'), 'utf8');
    const definition = page(name);
    for (const match of text.matchAll(/bind(?:tap|input|change|confirm)="([A-Za-z]+)"/g)) assert.equal(typeof definition[match[1]], 'function', name + ':' + match[1]);
    assert.ok(!text.includes('<br'));
  }
});
test('protected calls require an authenticated session', async () => {
  await assert.rejects(api.request('/me/'), /请先登录/); assert.equal(requests.length, 0); assert.deepEqual(navigations, ['/pages/login/index']);
});
test('expired local session is removed', () => {
  signedIn(); storage.get('campus-mini-session').expiresAt = Date.now() - 1;
  assert.equal(api.session(), null); assert.equal(storage.has('campus-mini-session'), false);
});
test('only HTTPS or loopback HTTP service addresses are accepted', () => {
  assert.equal(api.normalizeBase(' http://localhost:18000/ '), 'http://localhost:18000');
  assert.equal(api.normalizeBase('https://lab.example.edu/'), 'https://lab.example.edu');
  for (const url of ['javascript:alert(1)', 'http://example.com', 'https://user:pass@example.com', 'https://example.com?x=1']) assert.throws(() => api.normalizeBase(url));
});
test('cached service and forged form data cannot redirect login credentials', async () => {
  storage.set('campus-mini-base', 'https://untrusted.example');
  reply = r => r.success({ statusCode: 200, data: { ok: true, data: { token: 'b'.repeat(32), user: { id: 1 }, expires_in: 7200 } } });
  const p = page('login');p.setData({ 'form.base': 'https://untrusted.example' });await p.submit();
  assert.equal(requests.length, 1);assert.equal(requests[0].url, api.baseUrl() + '/api/mini/v1/auth/login/');
  assert.equal(new URL(requests[0].url).origin, require(path.join(root, 'config')).backendBaseUrl);
  assert.equal(storage.has('campus-mini-base'), false);assert.equal(api.session().base, api.baseUrl());
});
for (const oldBase of [undefined, 'https://other.example']) test('legacy or foreign-origin session cannot send bearer token: ' + String(oldBase), async () => {
  signedIn();storage.get('campus-mini-session').base = oldBase;
  storage.set('campus-mini-draft', { key: 'x' });
  await assert.rejects(api.request('/me/'), /请先登录/);
  assert.equal(requests.length, 0);assert.equal(api.session(), null);assert.equal(storage.has('campus-mini-draft'), false);
});
test('developer configuration can select a local backend without user address settings', async () => {
  const vm = require('node:vm'), sandbox = { module: { exports: {} }, wx: global.wx, require: () => ({ backendBaseUrl: 'http://127.0.0.1:18000' }) };
  vm.runInNewContext(fs.readFileSync(path.join(root, 'utils/api.js'), 'utf8'), sandbox);
  const localApi = sandbox.module.exports;
  await localApi.request('/auth/login/', 'POST', { username: 'student', password: 'mock' }, { auth: false });
  assert.equal(requests[0].url, 'http://127.0.0.1:18000/api/mini/v1/auth/login/');assert.equal(localApi.setBase, undefined);
});
test('cloud login allows wake-up time while ordinary requests remain bounded', async () => {
  reply = r => r.success({ statusCode: 200, data: { ok: true, data: { token: 'b'.repeat(32), user: { id: 1 }, expires_in: 7200 } } });
  await api.login('student', 'mock');await api.request('/me/');
  assert.equal(requests[0].timeout, 60000);assert.equal(requests[1].timeout, 10000);
});
for (const [cause, message] of [['request:fail timeout', /连接超时/], ['request:fail url not in domain list', /域名校验未通过/], ['request:fail ssl certificate error', /安全连接未建立/]]) test('network failure reports a specific actionable cause: ' + cause, async () => {
  signedIn();reply = r => r.fail({ errMsg: cause });
  await assert.rejects(api.request('/me/'), message);assert.ok(api.session());assert.equal(navigations.length, 0);
});
test('bearer token is sent in headers, never in the request URL', async () => {
  signedIn();await api.request('/me/');assert.equal(requests[0].header.Authorization, 'Bearer ' + 'a'.repeat(32));assert.ok(!requests[0].url.includes('aaaa'));
});
test('unauthorized server response clears session and redirects', async () => {
  signedIn();reply = r => r.success({ statusCode: 401, data: { ok: false, error: { message: '已失效' } } });
  await assert.rejects(api.request('/me/'), /已失效/);assert.equal(api.session(), null);assert.equal(navigations[0], '/pages/login/index');
});
test('business rejection keeps the authenticated session', async () => {
  signedIn();reply = r => r.success({ statusCode: 409, data: { ok: false, error: { code: 'RULE_REJECTED', message: '时间冲突' } } });
  await assert.rejects(api.request('/bookings/', 'POST', {}), /时间冲突/);assert.ok(api.session());
});
test('unknown HTML response is a visible service error', async () => {
  signedIn();reply = r => r.success({ statusCode: 200, data: '<html>wrong server</html>' });
  await assert.rejects(api.request('/me/'), /服务暂时异常/);
});
test('network failure retains stable idempotency key on retry', async () => {
  signedIn();reply = r => r.fail({ errMsg: 'timeout' });
  await assert.rejects(api.submitBooking(values()), /未能确认/);const key = requests[0].data.request_key;
  await assert.rejects(api.submitBooking(values()), /未能确认/);assert.equal(requests[1].data.request_key, key);assert.match(key, /^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/);
});
test('changed submission gets a new key and success removes draft', async () => {
  signedIn();reply = r => r.fail({});await assert.rejects(api.submitBooking(values()));const key = requests[0].data.request_key;
  reply = r => r.success({ statusCode: 200, data: { ok: true, data: { id: 17 } } });
  await api.submitBooking(Object.assign(values(), { purpose: '新的用途' }));assert.notEqual(requests[1].data.request_key, key);assert.equal(storage.has('campus-mini-draft'), false);
});
test('unauthenticated submit cannot create a draft or issue a request', async () => {
  await assert.rejects(api.submitBooking(values()), /请先登录/);assert.equal(storage.has('campus-mini-draft'), false);assert.equal(requests.length, 0);
});
test('field validation errors expose Chinese labels and causes', () => {
  assert.equal(api.errorText({ fields: { starts_at: ['时间格式无效'] } }), '开始时间：时间格式无效');
});
test('resource draft restores the same form after interrupted submission', async () => {
  signedIn();reply = r => r.fail({});await assert.rejects(api.submitBooking(values()));
  const p = page('resource');p.onLoad({ id: '1' });assert.equal(p.data.form.purpose, '模拟用途');assert.equal(p.data.form.date, '2027-01-01');
});
test('double tap does not duplicate an in-flight login', async () => {
  reply = () => {};const p = page('login');p.submit();p.submit();assert.equal(requests.length, 1);assert.equal(p.data.busy, true);
});
test('student cannot trigger a room reservation from the page controller', async () => {
  signedIn();const p = page('resource');p.setData({ item: { id: 1, kind: 'ROOM', can_apply: false } });await p.submit();assert.match(p.data.error, /仅教师/);assert.equal(requests.length, 0);
});
test('cancellation transmits the displayed optimistic version', async () => {
  signedIn();const p = page('booking');p.setData({ id: 31, item: { version: 7 }, 'form.reason': '课程调整' });p.load = async () => {};
  await p.cancel();assert.equal(requests[0].url.endsWith('/bookings/31/cancel/'), true);assert.deepEqual(requests[0].data, { reason: '课程调整', version: 7 });
});
test('marking one notification updates only that row and unread count', async () => {
  signedIn();const p = page('notifications');p.setData({ unread: 2, items: [{ id: 3, unread: true, booking_id: null }, { id: 4, unread: true, booking_id: null }] });
  await p.open({ currentTarget: { dataset: { id: '3' } } });assert.equal(p.data.items[0].unread, false);assert.equal(p.data.items[1].unread, true);assert.equal(p.data.unread, 1);
});
test('failed remote logout still clears the local session', async () => {
  signedIn();reply = r => r.fail({});await assert.rejects(api.logout());assert.equal(api.session(),null);
});
test('China date formatting is independent of the computer timezone', () => {
  const fmt = require(path.join(root, 'utils/format'));assert.equal(fmt.day(Date.parse('2026-10-19T20:00:00Z')), '2026-10-20');
});

test('occupancy next page appends without changing the reservation form', async () => {
  signedIn(); const p=page('resource');p.setData({id:1,item:{schedule:[{starts_at:'2027-01-01T10:00',ends_at:'2027-01-01T11:00'}]},scheduleHasMore:true,schedulePage:1,'form.purpose':'保留草稿'});
  reply=r=>r.success({statusCode:200,data:{ok:true,data:{schedule:[{starts_at:'2027-01-02T10:00',ends_at:'2027-01-02T11:00'}],schedule_page:2,schedule_total:31,schedule_has_more:false}}});
  await p.moreSchedule();assert.equal(requests[0].data.schedule_page,2);assert.equal(p.data.item.schedule.length,2);assert.equal(p.data.form.purpose,'保留草稿');assert.equal(p.data.scheduleHasMore,false);
});

test('date switch invalidates an older occupancy response', async () => {
  signedIn();const waiting=[];reply=r=>waiting.push(r);const p=page('resource');p.setData({id:1,item:{schedule:[]}});
  const old=p.loadSchedule(1);const current=p.chooseScheduleDay({detail:{value:'2027-01-03'}});
  waiting[1].success({statusCode:200,data:{ok:true,data:{schedule:[{starts_at:'2027-01-03T10:00',ends_at:'2027-01-03T11:00'}],schedule_page:1,schedule_total:1,schedule_has_more:false}}});await current;
  waiting[0].success({statusCode:200,data:{ok:true,data:{schedule:[],schedule_page:1,schedule_total:0,schedule_has_more:false}}});await old;
  assert.equal(p.data.scheduleTotal,1);assert.equal(p.data.item.schedule[0].starts_at,'2027-01-03T10:00');assert.equal(p.data.scheduleBusy,false);
});

test('failed occupancy load keeps the previous page and supports retry', async () => {
  signedIn();const p=page('resource');p.setData({id:1,item:{schedule:[]},schedulePage:1,scheduleHasMore:true});reply=r=>r.fail({});
  await p.moreSchedule();assert.equal(p.data.schedulePage,1);assert.equal(p.data.scheduleHasMore,true);assert.ok(p.data.scheduleError);assert.equal(p.data.scheduleBusy,false);
});

for (const order of ['fresh-first','old-first']) test('AUD-0086 returning to resource page releases pagination: '+order, async () => {
  signedIn();const waiting=[];reply=r=>waiting.push(r);const p=page('resource');
  p.setData({id:1,item:{schedule:[]},schedulePage:1,scheduleHasMore:true});
  const old=p.loadSchedule(2),fresh=p.onShow();
  const send=(index,pageNumber)=>waiting[index].success({statusCode:200,data:{ok:true,data:{policy:{window_days:14},schedule:[],schedule_page:pageNumber,schedule_total:31,schedule_has_more:true}}});
  if(order==='fresh-first'){send(1,1);await fresh;send(0,2);await old;}
  else{send(0,2);await old;send(1,1);await fresh;}
  assert.equal(p.data.schedulePage,1);assert.equal(p.data.scheduleBusy,false);assert.equal(p.data.loading,false);
  reply=r=>r.success({statusCode:200,data:{ok:true,data:{schedule:[],schedule_page:2,schedule_total:31,schedule_has_more:false}}});
  await p.moreSchedule();assert.equal(requests.length,3);assert.equal(p.data.schedulePage,2);
});

test('AUD-0086 failed return reload also releases older pagination ownership', async () => {
  signedIn();const waiting=[];reply=r=>waiting.push(r);const p=page('resource');
  p.setData({id:1,item:{schedule:[]},schedulePage:1,scheduleHasMore:true});
  const old=p.loadSchedule(2),fresh=p.onShow();waiting[1].fail({errMsg:'offline'});await fresh;
  waiting[0].success({statusCode:200,data:{ok:true,data:{schedule:[],schedule_page:2,schedule_total:31,schedule_has_more:false}}});await old;
  assert.equal(p.data.scheduleBusy,false);assert.equal(p.data.loading,false);assert.ok(p.data.error);
});
