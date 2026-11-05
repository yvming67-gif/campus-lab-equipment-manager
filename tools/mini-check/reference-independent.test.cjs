const test=require('node:test'),assert=require('node:assert/strict'),path=require('node:path');
const root=path.resolve(__dirname,'../../miniprogram');
let storage,requests,api;
function sign(id=1){storage.set('campus-mini-session',{token:String(id).repeat(32),user:{id},base:api.baseUrl(),expiresAt:Date.now()+3600000,draftScope:'independent-'+id});}
function page(){const p=require(path.join(root,'controllers')).create('resources');p.setData=function(values){for(const [key,v]of Object.entries(values)){const parts=key.split('.');let t=this.data;for(const part of parts.slice(0,-1))t=t[part]||(t[part]={});t[parts.at(-1)]=v;}};return p;}
function result(labs=[]){return {items:[],labs,page:1,has_more:false,total:0};}
function ok(r,data){r.success({statusCode:200,data:{ok:true,data}});}
test.beforeEach(()=>{storage=new Map();requests=[];global.wx={getStorageSync:k=>storage.get(k),setStorageSync:(k,v)=>storage.set(k,v),removeStorageSync:k=>storage.delete(k),request:r=>requests.push(r),reLaunch(){},switchTab(){},navigateTo(){},stopPullDownRefresh(){}};for(const name of ['utils/api','controllers'])delete require.cache[require.resolve(path.join(root,name))];api=require(path.join(root,'utils/api'));sign();});

test('clearing an applied filter supersedes an unresolved draft query response',async()=>{
 const p=page();p.setData({'form.q':'accepted',timeSearch:true,slot:{date:'2026-11-07',start:'10:00',end:'11:00'}});
 const initial=p.load(1);ok(requests[0],result());await initial;
 p.setData({'form.q':'unsubmitted-before-load'});const stale=p.load(1);
 const clearing=p.clearQueryFilter({currentTarget:{dataset:{key:'time'}}});
 assert.equal(requests[2].data.q,'accepted');assert.equal(requests[2].data.starts_at,undefined);
 ok(requests[2],result());await clearing;ok(requests[1],result());await stale;
 assert.equal(p.data.appliedTime,'');assert.deepEqual(p.data.appliedChips,[{key:'q',label:'\u641c\u7d22\uff1aaccepted'}]);
});

test('clearing a filter finds the applied lab by identity after server reorders lab options',async()=>{
 const p=page();p.setData({labs:[{id:'',name:'all'},{id:2,name:'B'}],labIndex:1,'form.q':'needle'});
 const first=p.load(1);ok(requests[0],result([{id:3,name:'C'},{id:2,name:'B'}]));await first;
 assert.equal(p.data.labIndex,2);
 const clearing=p.clearQueryFilter({currentTarget:{dataset:{key:'q'}}});
 assert.equal(requests[1].data.lab,2);assert.equal(requests[1].data.q,'');
 ok(requests[1],result([{id:2,name:'B'},{id:3,name:'C'}]));await clearing;
 assert.equal(p.data.labIndex,1);
});

test('an old successful query cannot install a snapshot owned by a replacement session',async()=>{
 const p=page();p.setData({'form.q':'old-account'});const pending=p.load(1);sign(2);
 ok(requests[0],result());await pending;
 assert.equal(p.data.queryReady,false);assert.equal(p._appliedFilterValues,undefined);
 const before=requests.length;await p.clearQueryFilter({currentTarget:{dataset:{key:'q'}}});assert.equal(requests.length,before);
});
