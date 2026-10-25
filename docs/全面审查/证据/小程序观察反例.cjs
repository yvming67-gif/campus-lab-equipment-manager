const test=require('node:test'),assert=require('node:assert/strict'),path=require('node:path');
const root=path.resolve(__dirname,'../../../miniprogram');
let storage,requests,navigations,api,reply;
function ok(r,data){r.success({statusCode:200,data:{ok:true,data}});}
function signed(id=1,token='a'.repeat(32)){storage.set('campus-mini-session',{token,user:{id},base:api.baseUrl(),expiresAt:Date.now()+7200000});}
function page(name){let p=require(path.join(root,'controllers')).create(name);p.setData=function(values){for(const[k,v]of Object.entries(values)){let t=this.data,parts=k.split('.');for(const x of parts.slice(0,-1))t=t[x]||(t[x]={});t[parts.at(-1)]=v;}};return p;}
function values(id){return{resource_id:id,starts_at:'2027-01-01T10:00:00',ends_at:'2027-01-01T11:00:00',purpose:'隔离审查用途'+id,participants:1};}
function booking(status){return{id:1,code:'SIMULATED',status,status_label:status==='CANCELLED'?'已取消':'已确认',kind:'EQUIPMENT',starts_at:'2027-01-01T10:00:00+08:00',ends_at:'2027-01-01T11:00:00+08:00',timeline:[],can_cancel:status==='CONFIRMED'};}
test.beforeEach(()=>{storage=new Map();requests=[];navigations=[];reply=()=>{};global.wx={getStorageSync:k=>storage.get(k)||'',setStorageSync:(k,v)=>storage.set(k,v),removeStorageSync:k=>storage.delete(k),request:r=>{requests.push(r);reply(r);},reLaunch:r=>{navigations.push(r.url);r.complete?.();},showToast(){},stopPullDownRefresh(){}};for(const f of ['controllers','utils/api'])delete require.cache[require.resolve(path.join(root,f))];api=require(path.join(root,'utils/api'));signed();});

test('observed: a late old-token 401 clears a newer authenticated session',async()=>{
  const old=api.request('/me/').catch(e=>e);
  signed(2,'b'.repeat(32));
  requests[0].success({statusCode:401,data:{ok:false,error:{message:'旧会话已失效'}}});await old;
  assert.equal(api.session(),null);assert.equal(navigations.at(-1),'/pages/login/index');
});

test('observed: an earlier successful submission clears another in-flight draft and changes its retry key',async()=>{
  const first=api.submitBooking(values(1));const second=api.submitBooking(values(2)).catch(e=>e);
  const secondKey=requests[1].data.request_key;
  ok(requests[0],{id:10,status:'CONFIRMED'});await first;
  assert.equal(api.draft(),null);
  requests[1].fail({errMsg:'request:fail timeout'});await second;
  const retry=api.submitBooking(values(2));assert.notEqual(requests[2].data.request_key,secondKey);
  ok(requests[2],{id:11,status:'CONFIRMED'});await retry;
});

test('observed: editing booking search before more appends page 2 of a new query to old results',async()=>{
  const p=page('bookings');reply=r=>ok(r,{items:[Object.assign(booking('CONFIRMED'),{id:requests.length})],page:r.data.page,total:30,has_more:true});
  await p.load(1);p.input({currentTarget:{dataset:{field:'q'}},detail:{value:'另一设备'}});await p.more();
  assert.equal(requests[1].data.q,'另一设备');assert.equal(requests[1].data.page,2);assert.equal(p.data.items.length,2);
});

test('observed: an older detail response overwrites a later cancelled result',async()=>{
  const p=page('booking');p.data.id=1;const old=p.load(),recent=p.load();
  ok(requests[1],booking('CANCELLED'));await recent;assert.equal(p.data.item.status,'CANCELLED');
  ok(requests[0],booking('CONFIRMED'));await old;assert.equal(p.data.item.status,'CONFIRMED');assert.equal(p.data.item.can_cancel,true);
});

test('observed: an old availability error overwrites current feedback and releases a newer loading state',async()=>{
  const p=page('resource');p.data.id=1;const old=p.checkAvailability();p.chooseDate({detail:{value:'2027-01-02'}});const recent=p.checkAvailability();
  requests[0].fail({errMsg:'request:fail timeout'});await old;
  assert.equal(p.data.loading,false);assert.match(p.data.error,/连接超时/);
  ok(requests[1],{available:true,available_label:'时段可预约'});await recent;
  assert.equal(p.data.available,true);assert.match(p.data.error,/连接超时/);
});

test('observed: failed mark-all invalidates the pending list but leaves loading permanently true',async()=>{
  const p=page('notifications');const reading=p.load(1);const marking=p.markAll();
  requests[1].fail({errMsg:'offline'});await marking;assert.equal(p.data.busy,false);assert.equal(p.data.loading,true);
  ok(requests[0],{items:[],page:1,has_more:true,unread:1});await reading;assert.equal(p.data.loading,true);
});

test('falsified hypothesis: expired-session onShow still returns Promise because it is async',async()=>{
  const p=page('resources');storage.get('campus-mini-session').expiresAt=Date.now()-1;
  await assert.doesNotReject(p.onPullDownRefresh());assert.equal(navigations.at(-1),'/pages/login/index');
});

test('observed: existing resource page keeps a stale minimum date when shown on a later day',async()=>{
  const realNow=Date.now;
  try{
    Date.now=()=>Date.parse('2026-10-24T10:00:00+08:00');const p=page('resource');p.data.id=1;
    storage.get('campus-mini-session').expiresAt=Date.parse('2026-10-26T10:00:00+08:00');
    assert.equal(p.data.minDate,'2026-10-24');Date.now=()=>Date.parse('2026-10-25T10:00:00+08:00');
    reply=r=>ok(r,{id:1,kind:'EQUIPMENT',service_status:'NORMAL',policy:{window_days:14},schedule:[],schedule_page:1,schedule_total:0,schedule_has_more:false});
    await p.onShow();assert.equal(p.data.minDate,'2026-10-24');assert.equal(p.data.maxDate,'2026-11-08');
  }finally{Date.now=realNow;}
});

test('observed: filtered booking zero result uses the same create-first empty message',async()=>{
  const fs=require('node:fs'),p=page('bookings');p.data.form.q='未匹配资源';p.data.statusIndex=2;
  reply=r=>ok(r,{items:[],page:1,total:0,has_more:false});await p.search();
  assert.equal(p.data.form.q,'未匹配资源');assert.equal(p.data.items.length,0);
  const text=fs.readFileSync(path.join(root,'pages/bookings/index.wxml'),'utf8');
  assert.ok(text.includes('!loading && items.length === 0'));assert.ok(text.includes('这里还没有预约'));assert.ok(text.includes('到资源目录选择设备或场地吧。'));
});

test('observed: three first-load failures satisfy both the error and normal-empty predicates',async()=>{
  for(const name of ['resources','bookings','notifications']){
    const p=page(name);reply=r=>r.fail({errMsg:'request:fail timeout'});await p.load(1);
    assert.ok(p.data.error,name);assert.equal(p.data.loading,false,name);assert.equal(p.data.items.length,0,name);
    const text=require('node:fs').readFileSync(path.join(root,'pages',name,'index.wxml'),'utf8');
    assert.ok(text.includes('!loading && items.length === 0'),name);
  }
});
