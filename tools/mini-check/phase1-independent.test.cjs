const test=require('node:test'),assert=require('node:assert/strict'),path=require('node:path');
const root=path.resolve(__dirname,'../../miniprogram');
let storage,requests,dialogs,api;
function sign(id){storage.set('campus-mini-session',{token:String(id).repeat(32),user:{id},base:api.baseUrl(),expiresAt:Date.now()+7200000});}
function page(name){const p=require(path.join(root,'controllers')).create(name);p.setData=function(d){for(const [k,v] of Object.entries(d)){const bits=k.split('.');let item=this.data;for(const bit of bits.slice(0,-1)){if(!item[bit])item[bit]={};item=item[bit];}item[bits.at(-1)]=v;}};return p;}
const ok=(request,data)=>request.success({statusCode:200,data:{ok:true,data}});
test.beforeEach(()=>{
 storage=new Map();requests=[];dialogs=[];
 global.wx={getStorageSync:k=>storage.get(k),setStorageSync:(k,v)=>storage.set(k,v),removeStorageSync:k=>storage.delete(k),request:r=>requests.push(r),showModal:r=>dialogs.push(r),showToast(){},reLaunch:r=>r.complete?.(),navigateTo(){},redirectTo(){},stopPullDownRefresh(){}};
 for(const name of ['utils/api.js','controllers.js'])delete require.cache[require.resolve(path.join(root,name))];
 api=require(path.join(root,'utils/api'));sign(1);
});

test('REV02: a confirmation started by another account cannot submit under the replacement account',async()=>{
 const p=page('incident');p.load=async()=>{};
 p.onLoad({resource:'1'});await p.onShow();
 p.setData({item:{id:1},form:{date:'2026-01-01',time:'10:00',description:'旧账号尚未确认的异常事实'}});
 const pending=p.submit();assert.equal(dialogs.length,1);
 sign(2);await p.onShow();
 dialogs[0].success({confirm:true});
 await new Promise(r=>setImmediate(r));
 // Settle the unintended request on the unfixed implementation so the test fails deterministically.
 if(requests.length)ok(requests[0],{id:9,status:'PENDING'});
 await pending;
 assert.equal(requests.length,0,'confirmation must be bound to its original account and page state');
 assert.equal(p.data.result,null);
});

test('late receipt lookup from a replaced account cannot clear the new account pending action',async()=>{
 const a=api.submitAction('/incidents/',{resource_id:1,description:'原内容'},'incident:1');
 const caught=assert.rejects(a);requests[0].fail({errMsg:'request:fail timeout'});await caught;
 const lookup=api.checkAction('incident:1');const stale=assert.rejects(lookup,{code:'SESSION_CHANGED'});
 sign(2);
 const b=api.submitAction('/incidents/',{resource_id:1,description:'新内容'},'incident:1');
 const failed=assert.rejects(b);const key=requests[2].data.request_key;
 ok(requests[1],{found:true,result:{id:1}});await stale;
 requests[2].fail({errMsg:'request:fail timeout'});await failed;
 assert.equal(api.actionDraft('incident:1').key,key);
 assert.equal(api.actionDraft('incident:1').values.description,'新内容');
});

test('REV02: old logout confirmation cannot log out a replacement account',async()=>{
 const p=page('profile');p.load=async()=>{};await p.onShow();
 const pending=p.signout();assert.equal(dialogs.length,1);
 sign(2);await p.onShow();dialogs[0].success({confirm:true});
 await new Promise(r=>setImmediate(r));
 if(requests.length)ok(requests[0],{signed_out:true});
 await pending;
 assert.equal(requests.length,0,'logout confirmation must remain bound to its original session');
 assert.equal(api.session().user.id,2);
});

test('REV02: old cancellation confirmation does not inject an error into a replacement account page',async()=>{
 const p=page('booking');p.load=async()=>{};p.onLoad({id:'3'});await p.onShow();
 p.setData({item:{id:3,version:1,can_cancel:true},form:{reason:'旧账号决定取消',supplement:''}});
 const pending=p.cancel();assert.equal(dialogs.length,1);
 sign(2);await p.onShow();dialogs[0].success({confirm:true});await pending;
 assert.equal(requests.length,0);
 assert.equal(p.data.error,'');assert.equal(api.session().user.id,2);
});

test('a server failure retains the original mutation identity for a later retry',async()=>{
 const body={reason:'一次性原说明'};
 const a=api.submitAction('/bookings/4/supplement/',body,'supplement:4');const caught=assert.rejects(a);
 const key=requests[0].data.request_key;
 requests[0].success({statusCode:503,data:{ok:false,error:{message:'暂不可用'}}});await caught;
 const b=api.submitAction('/bookings/4/supplement/',body,'supplement:4');
 assert.equal(requests[1].data.request_key,key);ok(requests[1],{note_id:1,replayed:true});await b;
 assert.equal(api.actionDraft('supplement:4'),null);
});
