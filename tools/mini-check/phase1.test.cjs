const test=require('node:test'),assert=require('node:assert/strict'),path=require('node:path'),fs=require('node:fs');
const root=path.resolve(__dirname,'../../miniprogram');
let api,storage,requests,navigations,answer,modals;
function page(name){const p=require(path.join(root,'controllers')).create(name);p.setData=function(values){for(const [key,value] of Object.entries(values)){const parts=key.split('.');let t=this.data;for(const part of parts.slice(0,-1)){if(!t[part])t[part]={};t=t[part];}t[parts.at(-1)]=value;}};return p;}
function sign(id=1){storage.set('campus-mini-session',{token:String(id).repeat(32),user:{id,display_name:'模拟用户'+id},base:api.baseUrl(),expiresAt:Date.now()+7200000});}
function ok(request,data){request.success({statusCode:200,data:{ok:true,data}});}
function fail(request){request.fail({errMsg:'request:fail timeout'});}
function reject(request,status=400){request.success({statusCode:status,data:{ok:false,error:{code:'VALIDATION_ERROR',message:'请核对字段',fields:{reason:['内容无效']}}}});}
const tick=()=>new Promise(r=>setImmediate(r));
const supplement=reason=>({reason});
function booking(){return {id:3,resource_id:1,kind:'EQUIPMENT',status:'PENDING',starts_at:'2027-01-01T10:00:00+08:00',ends_at:'2027-01-01T11:00:00+08:00',timeline:[],supplements:[],can_supplement:true,can_report:true};}
function incidentPage(){const p=page('incident');p.setData({resourceId:1,reservationId:3,item:{id:1},form:{date:'2026-01-01',time:'10:00',description:'设备无法正常开机'}});return p;}
test.beforeEach(()=>{
 storage=new Map();requests=[];navigations=[];modals=[];answer=true;
 global.wx={getStorageSync:k=>storage.get(k),setStorageSync:(k,v)=>storage.set(k,v),removeStorageSync:k=>storage.delete(k),request:r=>requests.push(r),reLaunch:r=>{navigations.push(r.url);r.complete?.();},navigateTo:r=>navigations.push(r.url),redirectTo:r=>navigations.push(r.url),switchTab:r=>navigations.push(r.url),showToast(){},showModal:r=>{modals.push(r);r.success({confirm:answer});},stopPullDownRefresh(){}};
 for(const file of ['utils/api.js','controllers.js'])delete require.cache[require.resolve(path.join(root,file))];api=require(path.join(root,'utils/api'));sign();
});

test('candidate request uses displayed day and duration; selection keeps purpose and never trusts disabled rows',async()=>{
 const p=page('resource');p.setData({id:1,item:{id:1},'form.date':'2027-01-02','form.purpose':'保留用途'});
 const loading=p.loadSlots();assert.deepEqual(requests[0].data,{slot_day:'2027-01-02',duration_minutes:60});
 ok(requests[0],{slot_options:{day:'2027-01-02',duration_minutes:60,checked_at:'2027-01-01T09:00:00+08:00',items:[{starts_at:'2027-01-02T10:00:00+08:00',ends_at:'2027-01-02T11:00:00+08:00',available:false},{starts_at:'2027-01-02T13:00:00+08:00',ends_at:'2027-01-02T14:00:00+08:00',available:true}]}});await loading;
 p.selectSlot({currentTarget:{dataset:{index:0}}});assert.equal(p.data.form.start,'10:00');
 p.selectSlot({currentTarget:{dataset:{index:1}}});assert.equal(p.data.form.start,'13:00');assert.equal(p.data.form.end,'14:00');assert.equal(p.data.form.purpose,'保留用途');assert.equal(p.data.available,null);assert.match(p.data.availability,/重新检查/);
});
test('changing candidate date invalidates a slow old response',async()=>{
 const p=page('resource');p.setData({id:1,item:{id:1}});const pending=p.loadSlots();p.chooseDate({detail:{value:'2027-01-04'}});
 ok(requests[0],{slot_options:{items:[{available:true}]}});await pending;assert.equal(p.data.slotOptions,null);assert.equal(p.data.slotsBusy,false);assert.equal(p.data.form.date,'2027-01-04');
});
test('candidate failure is shown as failure without retaining old actionable options',async()=>{
 const p=page('resource');p.setData({id:1,item:{id:1},slotOptions:{items:[{available:true}]}});const pending=p.loadSlots();fail(requests[0]);await pending;assert.equal(p.data.slotOptions,null);assert.ok(p.data.slotsError);assert.equal(p.data.slotsBusy,false);
});
test('unknown supplement result keeps same request key and rejects edited payload until reconciliation',async()=>{
 const first=api.submitAction('/bookings/3/supplement/',supplement('课程材料'),'supplement:3');const caught=assert.rejects(first);const key=requests[0].data.request_key;fail(requests[0]);await caught;
 assert.equal(api.actionDraft('supplement:3').key,key);
 await assert.rejects(api.submitAction('/bookings/3/supplement/',supplement('不同内容'),'supplement:3'),/恢复原内容/);assert.equal(requests.length,1);
 const retry=api.submitAction('/bookings/3/supplement/',supplement('课程材料'),'supplement:3');assert.equal(requests[1].data.request_key,key);ok(requests[1],{note_id:5,replayed:true});await retry;assert.equal(api.actionDraft('supplement:3'),null);
});
test('in-flight repeated action uses one request and definite rejection permits correction',async()=>{
 const a=api.submitAction('/bookings/3/supplement/',supplement('旧内容'),'supplement:3');const b=api.submitAction('/bookings/3/supplement/',supplement('旧内容'),'supplement:3');const caught=[assert.rejects(a),assert.rejects(b)];assert.equal(requests.length,1);const old=requests[0].data.request_key;reject(requests[0]);await Promise.all(caught);
 const changed=api.submitAction('/bookings/3/supplement/',supplement('已修正内容'),'supplement:3');assert.notEqual(requests[1].data.request_key,old);ok(requests[1],{note_id:6});await changed;
});
test('old account action completion cannot erase replacement account draft',async()=>{
 const old=api.submitAction('/bookings/3/supplement/',supplement('旧账号'),'supplement:3');const oldRejected=assert.rejects(old,{code:'SESSION_CHANGED'});sign(2);
 const current=api.submitAction('/bookings/3/supplement/',supplement('新账号'),'supplement:3');const currentRejected=assert.rejects(current);const key=requests[1].data.request_key;
 ok(requests[0],{note_id:8});await oldRejected;fail(requests[1]);await currentRejected;assert.equal(api.actionDraft('supplement:3').key,key);assert.equal(api.actionDraft('supplement:3').values.reason,'新账号');
});
test('read-only receipt lookup retains an unknown action until a matching saved receipt is found',async()=>{
 const first=api.submitAction('/bookings/3/supplement/',supplement('核对原说明'),'supplement:3');const caught=assert.rejects(first);const key=requests[0].data.request_key;fail(requests[0]);await caught;
 const missing=api.checkAction('supplement:3');assert.equal(requests[1].method,'GET');assert.ok(requests[1].url.endsWith('/receipts/'+key+'/'));ok(requests[1],{found:false,result:null,notice:'未找到不代表失败'});await missing;assert.equal(api.actionDraft('supplement:3').key,key);
 const found=api.checkAction('supplement:3');ok(requests[2],{found:true,result:{note_id:15}});assert.equal((await found).result.note_id,15);assert.equal(api.actionDraft('supplement:3'),null);assert.equal(requests.filter(x=>x.method==='POST').length,1);
});
test('successful supplement remains acknowledged even if detail refresh fails',async()=>{
 const p=page('booking');p.setData({id:3,item:booking(),'form.supplement':'补充课程用途'});const pending=p.supplement();assert.equal(p.data.busy,true);assert.equal(requests[0].data.reason,'补充课程用途');ok(requests[0],{booking_id:3,note_id:4,status:'PENDING'});await tick();fail(requests[1]);await pending;
 assert.match(p.data.success,/已保存/);assert.ok(p.data.error);assert.equal(p.data.form.supplement,'');assert.equal(p.data.busy,false);
});
test('unknown saved supplement can reconcile after booking ceases to accept new notes',async()=>{
 const sending=api.submitAction('/bookings/3/supplement/',supplement('待确认说明'),'supplement:3');const caught=assert.rejects(sending);fail(requests[0]);await caught;
 const p=page('booking');p.setData({id:3,item:Object.assign(booking(),{can_supplement:false})});p.restoreSupplement();const retry=p.supplement();assert.equal(requests[1].data.request_key,requests[0].data.request_key);ok(requests[1],{note_id:9,replayed:true});await tick();ok(requests[2],Object.assign(booking(),{status:'CONFIRMED',can_supplement:false}));await retry;assert.match(p.data.success,/已保存/);
});
test('incident confirmation cancellation produces no mutation',async()=>{
 const p=incidentPage();answer=false;await p.submit();assert.equal(requests.length,0);assert.equal(p.data.form.description,'设备无法正常开机');assert.equal(p.data.result,null);
});
test('incident rejects future observation locally and field errors use business names',async()=>{
 const p=incidentPage();p.setData({'form.date':'2099-01-01'});await p.submit();assert.equal(requests.length,0);assert.match(p.data.error,/不晚于当前时间/);
 assert.equal(api.errorText({fields:{description:['请说明现象'],occurred_at:['格式错误']}}),'异常情况：请说明现象\n发现时间：格式错误');
});
test('incident timeout restores original payload and server receipt is retained after safe retry',async()=>{
 const p=incidentPage(),pending=p.submit();await tick();const key=requests[0].data.request_key;assert.equal(requests[0].data.reservation,3);fail(requests[0]);await pending;assert.equal(p.data.pendingUnknown,true);
 p.setData({'form.description':'错误修改'});p.restorePending();assert.equal(p.data.form.description,'设备无法正常开机');const retry=p.submit();await tick();assert.equal(requests[1].data.request_key,key);ok(requests[1],{id:11,code:'IN-DEMO-11',status:'PENDING',status_label:'待核实',blocks_service:false,replayed:true});await retry;
 assert.equal(p.data.result.code,'IN-DEMO-11');assert.equal(p.data.pendingUnknown,false);await p.submit();assert.equal(requests.length,2);
});
test('incident receipt lookup shows saved record without a second mutation',async()=>{
 const p=incidentPage(),sending=p.submit();await tick();fail(requests[0]);await sending;const checking=p.checkPending();ok(requests[1],{found:true,result:{id:11,code:'IN-DEMO-11',status_label:'待核实'}});await checking;assert.equal(p.data.result.code,'IN-DEMO-11');assert.equal(p.data.pendingUnknown,false);assert.equal(requests[1].method,'GET');assert.equal(requests.filter(x=>x.method==='POST').length,1);
});
test('linked incident loading rejects resource mismatch without presenting another resource',async()=>{
 const p=incidentPage();const loading=p.load();ok(requests[0],{id:1,kind:'EQUIPMENT',service_status:'NORMAL'});ok(requests[1],Object.assign(booking(),{resource_id:2}));await loading;assert.equal(p.data.item,null);assert.match(p.data.error,/不一致/);
});
test('page owner switch clears old profile before replacement request fails',async()=>{
 const p=page('profile'),old=p.onShow();ok(requests[0],{user:{id:1,roles:[]},stats:{},policy:{},qualifications:[]});await old;assert.equal(p.data.user.id,1);sign(2);const next=p.onShow();assert.equal(p.data.user,null);fail(requests[1]);await next;assert.equal(p.data.user,null);assert.ok(p.data.error);
});
test('simple effects preference carries between pages without changing authorization',async()=>{
 const profile=page('profile');profile.chooseEffects({detail:{value:true}});assert.equal(profile.data.reducedEffects,true);const resource=page('resource');resource.load=async()=>{};await resource.onShow();assert.equal(resource.data.reducedEffects,true);assert.equal(api.session().user.id,1);
});
test('resource identity distinguishes categories and new form is a registered native page',()=>{
 const fmt=require(path.join(root,'utils/format'));assert.notEqual(fmt.resource({kind:'EQUIPMENT',category:'摄影设备'}).icon_mark,fmt.resource({kind:'EQUIPMENT',category:'示波器'}).icon_mark);assert.equal(fmt.resource({kind:'ROOM'}).icon_mark,'场');
 const config=JSON.parse(fs.readFileSync(path.join(root,'app.json'),'utf8'));assert.equal(config.pages.includes('pages/incident/index'),true);for(const ext of ['js','json','wxml','wxss'])assert.ok(fs.existsSync(path.join(root,'pages/incident/index.'+ext)));
 assert.equal(JSON.parse(fs.readFileSync(path.join(root,'project.config.json'),'utf8')).appid,'touristappid');
});
