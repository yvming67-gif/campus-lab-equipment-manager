// Bundled real reference photographs. They do not identify the simulated resource.
const entries=require('../assets/photos/catalog');
const byId=new Map(entries.map(p=>[p.id,Object.assign({},p,{src:'/assets/photos/'+p.file})]));
function get(id){return byId.get(id)||null;}
function forResource(item){
 if(item.kind==='ROOM')return get('scene');
 return /机器人|robot/i.test(String(item.name||'')+' '+String(item.category||''))?get('robot-platforms'):null;
}
module.exports={get,forResource};
