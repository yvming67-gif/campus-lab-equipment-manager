const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '../../miniprogram');
let api, storage, requests, navigations;

function signedIn(id = 1, token = 'a'.repeat(32)) {
  storage.set('campus-mini-session', {token, user:{id, display_name:'模拟用户'}, base:api.baseUrl(), expiresAt:Date.now()+86400000});
}
function page(name) {
  const p = require(path.join(root, 'controllers')).create(name);
  p.setData = function(values) {
    for(const [key,value] of Object.entries(values)) {
      const parts=key.split('.'); let target=this.data;
      for(const part of parts.slice(0,-1)) {if(!target[part])target[part]={};target=target[part];}
      target[parts.at(-1)]=value;
    }
  };
  return p;
}
function ok(request, data) {request.success({statusCode:200,data:{ok:true,data}});}
function fail(request) {request.fail({errMsg:'request:fail timeout'});}
function booking(status='CONFIRMED', id=1) {
  return {id,status,kind:'EQUIPMENT',starts_at:'2027-01-01T10:00:00+08:00',ends_at:'2027-01-01T11:00:00+08:00',version:status==='CANCELLED'?2:1,timeline:[],can_cancel:status==='CONFIRMED'};
}
function values(id=1, purpose='模拟用途') {
  return {resource_id:id,starts_at:'2027-01-01T10:00:00',ends_at:'2027-01-01T11:00:00',purpose,participants:1};
}
function list(items=[], hasMore=false) {return {items,page:1,has_more:hasMore,total:items.length,labs:[],unread:0};}
const tick=()=>new Promise(resolve=>setImmediate(resolve));

test.beforeEach(()=>{
  storage=new Map(); requests=[]; navigations=[];
  global.wx={getStorageSync:k=>storage.get(k),setStorageSync:(k,v)=>storage.set(k,v),removeStorageSync:k=>storage.delete(k),
    request:r=>requests.push(r),reLaunch:r=>{navigations.push(r.url);r.complete?.();},navigateTo:r=>navigations.push(r.url),
    redirectTo:r=>navigations.push(r.url),switchTab:r=>navigations.push(r.url),showModal:r=>r.success({confirm:true}),showToast(){},stopPullDownRefresh(){}};
  for(const file of ['utils/api.js','controllers.js'])delete require.cache[require.resolve(path.join(root,file))];
  api=require(path.join(root,'utils/api'));
});

test('AUD-0101 old 401 cannot clear a newer session or redirect its user',async()=>{
  signedIn();const old=api.request('/me/');const rejected=assert.rejects(old,{code:'SESSION_CHANGED'});
  signedIn(2,'b'.repeat(32));requests[0].success({statusCode:401,data:{ok:false,error:{message:'会话到期'}}});
  await rejected;assert.equal(api.session().user.id,2);assert.equal(navigations.length,0);
});

test('AUD-0101 old successful protected response cannot supply another account data',async()=>{
  signedIn();const old=api.request('/me/');const rejected=assert.rejects(old,{code:'SESSION_CHANGED'});
  signedIn(2,'b'.repeat(32));ok(requests[0],{user:{id:1}});await rejected;
  assert.equal(api.session().user.id,2);
});

test('AUD-0101 current session unauthorized result still clears its drafts',async()=>{
  signedIn();const sending=api.submitBooking(values());const rejected=assert.rejects(sending);
  requests[0].success({statusCode:401,data:{ok:false,error:{message:'会话到期'}}});await rejected;
  assert.equal(api.session(),null);assert.equal(api.draft(),null);assert.equal(storage.has('campus-mini-pending-drafts'),false);
  assert.deepEqual(navigations,['/pages/login/index']);
});

test('AUD-0101 delayed logout cannot clear a replacement session',async()=>{
  signedIn();const leaving=api.logout();const rejected=assert.rejects(leaving,{code:'SESSION_CHANGED'});
  signedIn(2,'b'.repeat(32));ok(requests[0],{});await rejected;assert.equal(api.session().user.id,2);
});

test('AUD-0102 confirming A preserves unknown B and its retry key',async()=>{
  signedIn();const a=api.submitBooking(values(1)),b=api.submitBooking(values(2));const rejected=assert.rejects(b);
  const key=requests[1].data.request_key;ok(requests[0],booking());await a;fail(requests[1]);await rejected;
  assert.equal(api.draft(2).key,key);assert.equal(api.draft(1),null);
  const retry=api.submitBooking(values(2));assert.equal(requests[2].data.request_key,key);ok(requests[2],booking('CONFIRMED',2));await retry;
  assert.equal(api.draft(),null);
});

test('AUD-0102 unresolved forms survive A-B-A navigation and distinct changed contents',async()=>{
  signedIn();const a=api.submitBooking(values(1));const ar=assert.rejects(a);const keyA=requests[0].data.request_key;fail(requests[0]);await ar;
  const b=api.submitBooking(values(2));const br=assert.rejects(b);const keyB=requests[1].data.request_key;fail(requests[1]);await br;
  const changed=api.submitBooking(values(1,'改变用途'));const cr=assert.rejects(changed);assert.notEqual(requests[2].data.request_key,keyA);fail(requests[2]);await cr;
  const p=page('resource');p.onLoad({id:'2'});assert.equal(p.data.form.purpose,'模拟用途');
  const retryA=api.submitBooking(values(1));assert.equal(requests[3].data.request_key,keyA);ok(requests[3],booking());await retryA;
  assert.equal(api.draft(2).key,keyB);assert.equal(api.draft(1).values.purpose,'改变用途');
});

test('AUD-0102 old submission completion cannot erase a new account pending form',async()=>{
  signedIn();const old=api.submitBooking(values());const rejected=assert.rejects(old,{code:'SESSION_CHANGED'});
  signedIn(2,'b'.repeat(32));const newer=api.submitBooking(values(2));const newerRejected=assert.rejects(newer);const key=requests[1].data.request_key;
  ok(requests[0],booking());await rejected;fail(requests[1]);await newerRejected;
  assert.equal(api.draft(2).key,key);assert.equal(api.draft(1),null);
});

test('AUD-0102 new login starts a new draft scope even for the same user',async()=>{
  signedIn();const old=api.submitBooking(values());const rejected=assert.rejects(old);fail(requests[0]);await rejected;
  const oldKey=api.draft(1).key;const login=api.login('student','模拟密码');ok(requests[1],{token:'b'.repeat(32),user:{id:1},expires_in:7200});await login;
  assert.equal(api.draft(1),null);const fresh=api.submitBooking(values());assert.notEqual(requests[2].data.request_key,oldKey);ok(requests[2],booking());await fresh;
});

test('AUD-0102 identical concurrent submissions share one outcome and keep its key after uncertainty',async()=>{
  signedIn();const a=api.submitBooking(values()),b=api.submitBooking(values());const ar=assert.rejects(a),br=assert.rejects(b);
  assert.equal(requests.length,1);const key=requests[0].data.request_key;fail(requests[0]);await Promise.all([ar,br]);
  const retry=api.submitBooking(values());assert.equal(requests[1].data.request_key,key);ok(requests[1],booking());await retry;
  assert.equal(api.draft(),null);
});

test('AUD-0103 latest detail wins when older GET completes last',async()=>{
  signedIn();const p=page('booking');p.data.id=1;const old=p.load(),latest=p.load();
  ok(requests[1],booking('CANCELLED'));await latest;ok(requests[0],booking());await old;
  assert.equal(p.data.item.status,'CANCELLED');assert.equal(p.data.loading,false);
});

test('AUD-0103 cancellation waits for fresh detail and preserves committed state if refresh fails',async()=>{
  signedIn();const p=page('booking');p.setData({id:1,item:booking(),'form.reason':'验收结束'});
  const old=p.load();await tick();assert.equal(requests.length,1);assert.equal(p.data.stale,true);
  await p.cancel();assert.equal(requests.length,1);assert.equal(p.data.busy,false);
  ok(requests[0],booking());await old;assert.equal(p.data.stale,false);
  const cancel=p.cancel();await tick();assert.equal(requests.length,2);
  ok(requests[1],booking('CANCELLED'));await tick();assert.equal(requests.length,3);
  fail(requests[2]);await cancel;
  assert.equal(p.data.item.status,'CANCELLED');assert.equal(p.data.item.can_cancel,false);assert.equal(p.data.loading,false);assert.equal(p.data.busy,false);assert.ok(p.data.error);
});

test('AUD-0018 old availability error does not overwrite current query or finish its spinner',async()=>{
  signedIn();const p=page('resource');p.data.id=1;const old=p.checkAvailability();p.chooseDate({detail:{value:'2027-01-02'}});
  const current=p.checkAvailability();fail(requests[0]);await old;
  assert.equal(p.data.loading,true);assert.equal(p.data.error,'');
  ok(requests[1],{available:true,available_label:'可预约'});await current;assert.equal(p.data.loading,false);assert.equal(p.data.available,true);
});

test('AUD-0018 editing a slot ends the invalidated check without stale errors',async()=>{
  signedIn();const p=page('resource');p.data.id=1;const old=p.checkAvailability();p.chooseEnd({detail:{value:'12:00'}});
  assert.equal(p.data.loading,false);fail(requests[0]);await old;assert.equal(p.data.error,'');assert.equal(p.data.available,null);
});

test('AUD-0018 slot edits cannot release a separate resource refresh loading state',async()=>{
  signedIn();const p=page('resource');p.data.id=1;const old=p.checkAvailability(),refresh=p.load();
  p.chooseStart({detail:{value:'12:00'}});assert.equal(p.data.loading,true);
  fail(requests[0]);await old;assert.equal(p.data.loading,true);assert.equal(p.data.error,'');
  ok(requests[1],{id:1,kind:'EQUIPMENT',policy:{window_days:14}});await refresh;assert.equal(p.data.loading,false);
});

test('AUD-0104 failed mark-all releases invalidated list loading and ignores its late response',async()=>{
  signedIn();const p=page('notifications');const old=p.load(1),mark=p.markAll();
  fail(requests[1]);await mark;assert.equal(p.data.busy,false);assert.equal(p.data.loading,false);assert.ok(p.data.error);
  ok(requests[0],list([{id:2,read_at:null}]));await old;assert.equal(p.data.items.length,0);assert.ok(p.data.error);
});

test('AUD-0104 a list refresh removes stale rows before they can be marked read',async()=>{
  signedIn();const p=page('notifications');p.setData({items:[{id:2,unread:true}],unread:1});const old=p.load(1);
  assert.equal(p.data.items.length,0);await p.open({currentTarget:{dataset:{id:2}}});assert.equal(requests.length,1);
  fail(requests[0]);await old;
  assert.equal(p.data.loading,false);assert.equal(p.data.busy,false);assert.equal(p.data.unread,1);assert.ok(p.data.error);
});

test('AUD-0017 a concurrent post-read list result does not cause double unread subtraction',async()=>{
  signedIn();const p=page('notifications');p.setData({items:[{id:2,unread:true}],unread:2});
  const opening=p.open({currentTarget:{dataset:{id:2}}}),refresh=p.load(1);
  ok(requests[1],{...list([{id:2,read_at:'2027-01-01T10:00:00'}]),unread:1});await refresh;
  ok(requests[0],{});await tick();assert.equal(requests.length,3);
  ok(requests[2],{...list([{id:2,read_at:'2027-01-01T10:00:00'}]),unread:1});await opening;
  assert.equal(p.data.unread,1);assert.equal(p.data.items[0].unread,false);assert.equal(p.data.loading,false);
});

test('AUD-0088 edited booking keyword or status starts at page one and replaces previous records',async()=>{
  signedIn();const p=page('bookings');let loading=p.load(1);ok(requests[0],list([booking()],true));await loading;
  p.input({currentTarget:{dataset:{field:'q'}},detail:{value:'新条件'}});loading=p.more();assert.equal(requests[1].data.page,1);ok(requests[1],list([booking('CONFIRMED',2)],true));await loading;
  assert.deepEqual(p.data.items.map(x=>x.id),[2]);assert.equal(p.data.appliedFilter,true);
  p.setData({statusIndex:1});loading=p.more();assert.equal(requests[2].data.page,1);ok(requests[2],list([]));await loading;assert.equal(p.data.items.length,0);
});

test('AUD-0088 unchanged booking query still appends the next page',async()=>{
  signedIn();const p=page('bookings');let loading=p.load(1);ok(requests[0],list([booking()],true));await loading;
  loading=p.more();assert.equal(requests[1].data.page,2);ok(requests[1],{...list([booking('CONFIRMED',2)]),page:2});await loading;
  assert.deepEqual(p.data.items.map(x=>x.id),[1,2]);
});

test('AUD-0108 both date picker bounds refresh after midnight on return',async()=>{
  const original=Date.now;let now=Date.parse('2027-01-01T23:50:00+08:00');Date.now=()=>now;
  try {
    signedIn();const resources=page('resources'),resource=page('resource');resource.data.id=1;now+=20*60000;
    const home=resources.onShow(),detail=resource.onShow();assert.equal(resources.data.minDate,'2027-01-02');assert.equal(resource.data.minDate,'2027-01-02');
    ok(requests[0],{user:{id:1},stats:{},policy:{window_days:14},upcoming:[]});ok(requests[1],list([]));ok(requests[2],{id:1,kind:'EQUIPMENT',policy:{window_days:14}});await Promise.all([home,detail]);
    assert.equal(resources.data.maxDate,'2027-01-16');assert.equal(resource.data.maxDate,'2027-01-16');
  } finally {Date.now=original;}
});

test('AUD-0109 pending review detail explicitly suspends handover and hides the normal arrival instruction',()=>{
  const text=fs.readFileSync(path.join(root,'pages/booking/index.wxml'),'utf8');
  assert.match(text,/item\.status === 'CONFIRMED' && !item\.review_required/);
  assert.match(text,/wx:if="\{\{item\.review_required\}\}">此预约正在复核，暂不能办理设备领取或场地入场/);
});

test('AUD-0110 booking empty state distinguishes applied filters from no history',async()=>{
  signedIn();const p=page('bookings');p.setData({'form.q':'不存在的资源'});const loading=p.load(1);ok(requests[0],list());await loading;assert.equal(p.data.appliedFilter,true);
  const text=fs.readFileSync(path.join(root,'pages/bookings/index.wxml'),'utf8');assert.match(text,/appliedFilter \? '没有符合筛选条件的预约'/);assert.match(text,/请调整关键词或预约状态后重新查询/);
});

for(const name of ['resources','bookings','notifications'])test(`AUD-0111 ${name} first request failure is an error rather than a successful empty result`,async()=>{
  signedIn();const p=page(name);const loading=p.load(1);fail(requests[0]);await loading;
  assert.ok(p.data.error);assert.equal(p.data.loading,false);assert.equal(p.data.items.length,0);
  const text=fs.readFileSync(path.join(root,`pages/${name}/index.wxml`),'utf8');assert.match(text,/!loading && !error && items\.length === 0/);
});

test('AUD-0111 occupancy date load failure cannot also claim that selected range is empty',async()=>{
  signedIn();const p=page('resource');p.setData({id:1,item:{schedule:[{id:4}]}});
  const loading=p.chooseScheduleDay({detail:{value:'2027-01-02'}});fail(requests[0]);await loading;
  assert.ok(p.data.scheduleError);assert.equal(p.data.scheduleBusy,false);assert.equal(p.data.item.schedule.length,0);
  const text=fs.readFileSync(path.join(root,'pages/resource/index.wxml'),'utf8');
  assert.match(text,/!loading && !error && !scheduleBusy && !scheduleError && !item\.schedule\.length/);
});

test('AUD-0112 reservation confirmation refreshes approval mode before asking the user',async()=>{
  signedIn();const p=page('resource');p.setData({id:1,stale:false,
    item:{id:1,name:'数字示波器',kind:'EQUIPMENT',can_apply:true,can_reserve:true,requires_approval:false,policy:{window_days:14,slot_minutes:30,max_hours:8}},
    form:{date:'2027-01-01',start:'10:00',end:'11:00',purpose:'课程演示',participants:'1'}});
  let modalContent='';wx.showModal=options=>{modalContent=options.content;options.success({confirm:true});};
  const submitting=p.submit();await tick();assert.equal(requests.length,1);assert.match(requests[0].url,/\/resources\/1\/$/);
  ok(requests[0],{id:1,name:'数字示波器',kind:'EQUIPMENT',can_apply:true,can_reserve:true,
    requires_approval:true,policy:{window_days:14,slot_minutes:30,max_hours:8}});
  await tick();assert.match(modalContent,/处理方式：提交后由管理员人工审核/);assert.equal(requests.length,2);
  assert.match(requests[1].url,/\/bookings\/$/);ok(requests[1],booking('PENDING'));await submitting;
  assert.equal(p.data.item.requires_approval,true);
});

test('AUD-0112 approval mode can change from manual to automatic before confirmation',async()=>{
  signedIn();const p=page('resource');p.setData({id:1,stale:false,
    item:{id:1,name:'全画幅摄影套装',kind:'EQUIPMENT',can_apply:true,can_reserve:true,requires_approval:true,policy:{window_days:14,slot_minutes:30,max_hours:8}},
    form:{date:'2027-01-01',start:'10:00',end:'11:00',purpose:'模式反向变化测试',participants:'1'}});
  let modalContent='';wx.showModal=options=>{modalContent=options.content;options.success({confirm:true});};
  const submitting=p.submit();await tick();
  ok(requests[0],{id:1,name:'全画幅摄影套装',kind:'EQUIPMENT',can_apply:true,can_reserve:true,
    requires_approval:false,policy:{window_days:14,slot_minutes:30,max_hours:8}});
  await tick();assert.match(modalContent,/处理方式：规则通过后自动确认/);
  assert.equal(requests[1].data.expected_approval_mode,'AUTO');
  ok(requests[1],booking('CONFIRMED'));await submitting;
});

test('AUD-0112 a newly restricted resource cannot proceed from a stale detail page',async()=>{
  signedIn();const p=page('resource');p.setData({id:1,stale:false,
    item:{id:1,name:'数字示波器',kind:'EQUIPMENT',can_apply:true,can_reserve:true,requires_approval:false,policy:{window_days:14,slot_minutes:30,max_hours:8}},
    form:{date:'2027-01-01',start:'10:00',end:'11:00',purpose:'课程演示',participants:'1'}});
  const submitting=p.submit();await tick();
  ok(requests[0],{id:1,name:'数字示波器',kind:'EQUIPMENT',can_apply:true,can_reserve:false,
    reservation_block_reason:'资源维护中',requires_approval:false,policy:{window_days:14,slot_minutes:30,max_hours:8}});
  await submitting;assert.equal(requests.length,1);assert.equal(p.data.error,'资源维护中');
});

test('AUD-0113 reservation form stays immutable during the fresh confirmation request',async()=>{
  signedIn();const p=page('resource');p.setData({id:1,stale:false,
    item:{id:1,name:'数字示波器',kind:'EQUIPMENT',can_apply:true,can_reserve:true,requires_approval:false,policy:{window_days:14,slot_minutes:30,max_hours:8}},
    form:{date:'2027-01-01',start:'10:00',end:'11:00',purpose:'课程演示',participants:'1'}});
  const submitting=p.submit();await tick();assert.equal(p.data.busy,true);assert.equal(requests.length,1);
  p.input({currentTarget:{dataset:{field:'purpose'}},detail:{value:'篡改中的用途'}});
  p.chooseDate({detail:{value:'2027-01-02'}});p.chooseStart({detail:{value:'12:00'}});
  p.reportIncident();p.acknowledgeSafety();
  assert.deepEqual(p.data.form,{date:'2027-01-01',start:'10:00',end:'11:00',purpose:'课程演示',participants:'1'});
  assert.equal(navigations.length,0);
  ok(requests[0],{id:1,name:'数字示波器',kind:'EQUIPMENT',can_apply:true,can_reserve:true,requires_approval:false,policy:{window_days:14,slot_minutes:30,max_hours:8}});
  await tick();assert.equal(requests.length,2);assert.equal(requests[1].data.starts_at,'2027-01-01T10:00:00');assert.equal(requests[1].data.purpose,'课程演示');
  ok(requests[1],booking('CONFIRMED',1));await submitting;assert.equal(p.data.busy,false);
});

test('AUD-0114 stale resource page can recover a committed booking receipt before checking current availability',async()=>{
  signedIn();const original=api.submitBooking(values());const rejected=assert.rejects(original);fail(requests[0]);await rejected;
  const pending=api.draft(1),p=page('resource');p.setData({id:1,stale:true,hasPendingDraft:true,
    form:{date:pending.values.starts_at.slice(0,10),start:pending.values.starts_at.slice(11,16),end:pending.values.ends_at.slice(11,16),purpose:pending.values.purpose,participants:'1'}});
  const retry=p.submit();await tick();assert.equal(requests.length,2,JSON.stringify({error:p.data.error,form:p.data.form,pending:api.draft(1)}));assert.match(requests[1].url,/\/receipts\//);
  ok(requests[1],{found:true,kind:'booking',result:{id:77,status:'CONFIRMED'}});await retry;
  assert.equal(requests.length,2);assert.equal(api.draft(1),null);assert.ok(navigations.includes('/pages/booking/index?id=77'));
});

test('AUD-0114 missing receipt replays only the original request after the resource becomes unavailable',async()=>{
  signedIn();const original=api.submitBooking(values());const rejected=assert.rejects(original);fail(requests[0]);await rejected;
  const pending=api.draft(1),key=pending.key,p=page('resource');p.setData({id:1,stale:true,hasPendingDraft:true,
    item:{id:1,kind:'EQUIPMENT',can_apply:true,can_reserve:false},
    form:{date:pending.values.starts_at.slice(0,10),start:pending.values.starts_at.slice(11,16),end:pending.values.ends_at.slice(11,16),purpose:pending.values.purpose,participants:'1'}});
  const retry=p.submit();await tick();assert.match(requests[1].url,/\/receipts\//);ok(requests[1],{found:false,kind:null,result:null});await tick();
  assert.equal(requests.length,3);assert.match(requests[2].url,/\/bookings\/$/);assert.equal(requests[2].data.request_key,key);assert.equal(requests[2].data.purpose,pending.values.purpose);
  requests[2].success({statusCode:409,data:{ok:false,error:{code:'RULE_REJECTED',message:'资源维护中'}}});await retry;
  assert.equal(api.draft(1),null);assert.equal(p.data.hasPendingDraft,false);assert.match(p.data.error,/资源维护中/);
});

test('AUD-0115 booking confirmation cannot cross into a replacement account',async()=>{
  signedIn();const p=page('resource');p.setData({id:1,stale:false,
    item:{id:1,name:'数字示波器',kind:'EQUIPMENT',can_apply:true,can_reserve:true,requires_approval:false,policy:{window_days:14,slot_minutes:30,max_hours:8}},
    form:{date:'2027-01-01',start:'10:00',end:'11:00',purpose:'旧账号申请',participants:'1'}});
  let modal;wx.showModal=options=>{modal=options;};
  const submitting=p.submit();await tick();ok(requests[0],{id:1,name:'数字示波器',kind:'EQUIPMENT',can_apply:true,can_reserve:true,requires_approval:false,policy:{window_days:14,slot_minutes:30,max_hours:8}});await tick();
  assert.ok(modal);signedIn(2,'b'.repeat(32));modal.success({confirm:true});await submitting;
  assert.equal(requests.length,1);assert.match(p.data.error,/登录身份已变化/);assert.equal(api.session().user.id,2);
});

test('AUD-0115 safety acknowledgement and reservation cannot overlap',async()=>{
  signedIn();const p=page('resource');p.setData({id:1,safetyBusy:true,item:{id:1,kind:'ROOM',can_apply:true,can_reserve:true},form:{date:'2027-01-01',start:'10:00',end:'11:00',purpose:'课程实验',participants:'2'}});
  await p.submit();p.reportIncident();assert.equal(requests.length,0);assert.equal(navigations.length,0);assert.equal(p.data.busy,false);
  const text=fs.readFileSync(path.join(root,'pages/resource/index.wxml'),'utf8');
  assert.match(text,/disabled="\{\{busy \|\| safetyBusy/);assert.match(text,/disabled="\{\{busy \|\| safetyBusy\}\}" bindtap="reportIncident"/);
});

test('AUD-0114 WXML leaves stale-result reconciliation enabled while ordinary stale booking remains disabled',()=>{
  const text=fs.readFileSync(path.join(root,'pages/resource/index.wxml'),'utf8');
  assert.match(text,/\(stale && !hasPendingDraft\)/);assert.match(text,/核对上次预约结果/);
});
