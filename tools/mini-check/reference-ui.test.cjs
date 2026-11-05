const test=require('node:test'),assert=require('node:assert/strict'),path=require('node:path');
const root=path.resolve(__dirname,'../../miniprogram');
let storage,requests,api;
function sign(id=1){storage.set('campus-mini-session',{token:String(id).repeat(32),user:{id},base:api.baseUrl(),expiresAt:Date.now()+3600000,draftScope:'session-'+id});}
function page(name){const p=require(path.join(root,'controllers')).create(name);p.setData=function(values){for(const [key,v]of Object.entries(values)){const keys=key.split('.');let t=this.data;for(const k of keys.slice(0,-1))t=t[k]||(t[k]={});t[keys.at(-1)]=v;}};return p;}
function ok(r,data){r.success({statusCode:200,data:{ok:true,data}});}
function result(){return {items:[],labs:[{id:2,name:'模拟实验室'}],page:1,has_more:false,total:0};}
test.beforeEach(()=>{storage=new Map();requests=[];global.wx={getStorageSync:k=>storage.get(k),setStorageSync:(k,v)=>storage.set(k,v),removeStorageSync:k=>storage.delete(k),request:r=>requests.push(r),reLaunch(){},switchTab(){},navigateTo(){},stopPullDownRefresh(){}};for(const name of ['utils/api','controllers'])delete require.cache[require.resolve(path.join(root,name))];api=require(path.join(root,'utils/api'));sign();});

test('quick dates use Beijing midnight and bound a zero-day or short configured window',()=>{
 const fmt=require(path.join(root,'utils/format')),at=Date.parse('2026-01-31T16:01:00Z');
 const days=fmt.quickDates('2026-02-02',14,at);assert.equal(days.length,7);assert.equal(days[0].day,'2026-02-01');assert.equal(days[0].weekday,'今天');assert.equal(days[1].selected,true);
 assert.equal(fmt.quickDates('',0,at).length,1);assert.equal(fmt.quickDates('',2,at).length,3);
});

test('choosing a quick date invalidates stale candidate data and retains the booking purpose',async()=>{
 const p=page('resource');p.setData({id:1,item:{id:1,policy:{window_days:14}},'form.purpose':'保留实验目的'});const pending=p.loadSlots();
 const day=p.data.quickDates[2].day;p.chooseQuickDate({currentTarget:{dataset:{day}}});
 ok(requests[0],{slot_options:{items:[{available:true}]}});await pending;
 assert.equal(p.data.form.date,day);assert.equal(p.data.form.purpose,'保留实验目的');assert.equal(p.data.slotOptions,null);assert.equal(p.data.quickDates.find(d=>d.day===day).selected,true);
});

test('query summary reflects the accepted request rather than an unsent search draft',async()=>{
 const p=page('resources');p.setData({'form.q':'已执行搜索'});const pending=p.load(1);
 p.input({currentTarget:{dataset:{field:'q'}},detail:{value:'尚未提交的新内容'}});ok(requests[0],result());await pending;
 assert.equal(p.data.form.q,'尚未提交的新内容');assert.deepEqual(p.data.appliedChips,[{key:'q',label:'搜索：已执行搜索'}]);assert.equal(p.data.queryReady,true);
});

test('clearing one filter retains the other applied filters and ignores an unsent draft',async()=>{
 const p=page('resources');p.setData({labs:[{id:'',name:'全部实验室'},{id:2,name:'模拟实验室'}],labIndex:1,form:{q:'已应用内容'},timeSearch:true,slot:{date:'2027-01-02',start:'10:00',end:'11:00'}});
 const first=p.load(1);ok(requests[0],result());await first;p.setData({'form.q':'未应用草稿'});
 const clearing=p.clearQueryFilter({currentTarget:{dataset:{key:'lab'}}});assert.equal(requests[1].data.q,'已应用内容');assert.equal(requests[1].data.lab,'');assert.equal(requests[1].data.starts_at,'2027-01-02T10:00:00');
 ok(requests[1],result());await clearing;assert.equal(p.data.labIndex,0);assert.equal(p.data.form.q,'已应用内容');
});

test('a replacement identity cannot restore the prior account filter snapshot',async()=>{
 const p=page('resources');p.setData({'form.q':'原账号条件'});const first=p.load(1);ok(requests[0],result());await first;sign(2);
 const count=requests.length;await p.clearQueryFilter({currentTarget:{dataset:{key:'q'}}});assert.equal(requests.length,count);
});

test('a failed query retains the prior applied summary and exposes the failure',async()=>{
 const p=page('resources');p.setData({'form.q':'成功条件'});const first=p.load(1);ok(requests[0],result());await first;
 p.setData({'form.q':'失败条件'});const retry=p.load(1);requests[1].fail({errMsg:'request:fail timeout'});await retry;
 assert.equal(p.data.appliedChips[0].label,'搜索：成功条件');assert.ok(p.data.error);assert.equal(p.data.loading,false);
});
