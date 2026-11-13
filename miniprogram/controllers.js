const api = require('./utils/api');
const config = require('./config');
const fmt = require('./utils/format');
const photos = require('./utils/photos');
function photoError(e) {const key=e.currentTarget.dataset.photo;if(photos.get(key))this.setData({['photoErrors.'+key]:true});}
function copyPhotoSource(e) {const p=photos.get(e.currentTarget.dataset.photo);if(p)wx.setClipboardData({data:p.source_page});}
const statuses = [{ code: '', name: '全部' }, { code: 'PENDING', name: '待审核' }, { code: 'CONFIRMED', name: '已确认' }, { code: 'IN_USE', name: '使用中' }, { code: 'COMPLETED', name: '已完成' }, { code: 'CANCELLED', name: '已取消' }, { code: 'REJECTED', name: '已驳回' }, { code: 'EXPIRED', name: '已失效' }];
function error(page, e) { page.setData({ error: api.errorText(e) }); }
function input(event) { this.setData({ ['form.' + event.currentTarget.dataset.field]: event.detail.value, error: '' }); }
function openResource(event) {
  const slot = this._appliedSlot;
  const query = slot ? '&date=' + encodeURIComponent(slot.date) + '&start=' + encodeURIComponent(slot.start) + '&end=' + encodeURIComponent(slot.end) : '';
  wx.navigateTo({ url: '/pages/resource/index?id=' + event.currentTarget.dataset.id + query });
}
function openBookings(event) {
  wx.setStorageSync('campus-mini-booking-filter', {status:event.currentTarget.dataset.status || ''});
  wx.switchTab({url:'/pages/bookings/index'});
}
function openBooking(event) { wx.navigateTo({ url: '/pages/booking/index?id=' + event.currentTarget.dataset.id }); }
function endRefresh() { if (wx.stopPullDownRefresh) wx.stopPullDownRefresh(); }
function invalidateAvailability(page) {
  page._availabilitySerial=(page._availabilitySerial||0)+1;
  const loading=page._availabilityPending ? false : page.data.loading;
  page._availabilityPending=false;
  return loading;
}
function confirm(options) { return new Promise(resolve => wx.showModal(Object.assign({}, options, { success: r => resolve(r.confirm), fail: () => resolve(false) }))); }
function durationOptions(policy) {
  const step=Number(policy && policy.slot_minutes)||30,max=(Number(policy && policy.max_hours)||8)*60;
  return [...new Set([step,60,120,240,480])].filter(n=>n<=max && n%step===0).sort((a,b)=>a-b).map(n=>({minutes:n,label:n+'分钟'}));
}
function incidentContext(resourceId,reservationId) {return 'incident:'+resourceId+':'+(reservationId||'none');}
function nowFields() {const local=new Date(Date.now()+8*3600000).toISOString();return {date:local.slice(0,10),time:local.slice(11,16)};}
function positiveId(value) {const n=Number(value);return Number.isSafeInteger(n)&&n>0?n:null;}
function ownerChanged(page) {
  const saved=api.session(),owner=saved && saved.draftScope;
  const changed=page._ownerScope && page._ownerScope!==owner;page._ownerScope=owner;return changed;
}
function sessionStamp() {
  const value=api.session();
  return value ? JSON.stringify([value.token,value.base,value.user.id,value.draftScope]) : '';
}

const screens = {
  login: {
    data: { brandName: config.appName, accounts: [{ username: 'student', name: '学生 · 演示账号01' }, { username: 'student2', name: '学生 · 演示账号02' }, { username: 'teacher', name: '教师 · 演示账号' }], accountIndex: 0, form: { password: 'Demo@2026' }, busy: false, error: '' },
    onShow() { if (api.session()) wx.switchTab({ url: '/pages/resources/index' }); },
    input,
    chooseAccount(e) { this.setData({ accountIndex: Number(e.detail.value), error: '' }); },
    async submit() {
      if (this.data.busy) return;
      this.setData({ busy: true, error: '' });
      try { await api.login(this.data.accounts[this.data.accountIndex].username, this.data.form.password); wx.switchTab({ url: '/pages/resources/index' }); }
      catch (e) { error(this, e); } finally { this.setData({ busy: false }); }
    }
  },
  resources: {
    data: { items: [], labs: [{ id: '', name: '全部管理单元' }], labIndex: 0, campuses:[{id:'',name:'全部校区'}],campusIndex:0,buildings:[{id:'',name:'全部楼宇'}],buildingIndex:0,kind: 'EQUIPMENT', form: { q: '', category:'', facility:'', capacity_min:'', capacity_max:'' }, loading: false, error: '', stale: false, hasMore: false, page: 1, total: 0, user: null, stats: { pending: 0, confirmed: 0, using: 0 }, upcoming: [], policy: null, overviewReady: false, overviewLoading: false, overviewError: '', timeSearch: false, advancedFilters:false, serviceStatuses:[{code:'',name:'全部状态'},{code:'NORMAL',name:'正常'},{code:'ISOLATED',name:'异常隔离'},{code:'MAINTENANCE',name:'维护中'},{code:'DISABLED',name:'停用'},{code:'SCRAPPED',name:'报废'}], serviceStatusIndex:0, slot: fmt.defaultForm(), appliedTime: '', minDate: fmt.day(), maxDate: fmt.day(Date.now()+14*86400000) },
    async onShow() {
      if (!api.requireLogin()) return;
      const serial=(this._overviewSerial || 0)+1;this._overviewSerial=serial;
      this.setData({user:api.session().user,overviewLoading:true,overviewError:'',minDate:fmt.day(),quickDates:fmt.quickDates(this.data.slot.date,this.data.policy?.window_days)});
      const overview=api.request('/me/').then(data=>{
        if(serial!==this._overviewSerial)return;
        this.setData({user:data.user,stats:data.stats,overdue:data.overdue,policy:data.policy,upcoming:(data.upcoming || []).map(fmt.booking),overviewReady:true,maxDate:fmt.day(Date.now()+(data.policy ? data.policy.window_days : 14)*86400000),quickDates:fmt.quickDates(this.data.slot.date,data.policy?.window_days)});
      }).catch(e=>{if(serial===this._overviewSerial)this.setData({overviewError:api.errorText(e)});})
        .finally(()=>{if(serial===this._overviewSerial)this.setData({overviewLoading:false});});
      return Promise.all([overview,this.load(1)]);
    },
    input, openResource, openBooking, openBookings,
    toggleTime() {this.setData({timeSearch:!this.data.timeSearch});if(!this.data.timeSearch)return this.load(1);},
    chooseQueryDate(e) {this.setData({'slot.date':e.detail.value,quickDates:fmt.quickDates(e.detail.value,this.data.policy?.window_days)});},
    chooseQuickQueryDate(e) {this.chooseQueryDate({detail:{value:e.currentTarget.dataset.day}});},
    chooseQueryStart(e) {this.setData({'slot.start':e.detail.value});},
    chooseQueryEnd(e) {this.setData({'slot.end':e.detail.value});},
    resetSearch() {this.setData({form:{q:'',category:'',facility:'',capacity_min:'',capacity_max:''},labIndex:0,campusIndex:0,buildingIndex:0,timeSearch:false,advancedFilters:false,serviceStatusIndex:0});return this.load(1);},
    async load(page) {
      if (!api.requireLogin()) return;
      page = page || 1;
      const slot = this.data.timeSearch ? Object.assign({},this.data.slot) : null;
      if(slot && slot.start>=slot.end){this.setData({error:'查询结束时间必须晚于开始时间'});return;}
      const selectedLab=this.data.labs[this.data.labIndex] || this.data.labs[0];
      const selectedCampus=this.data.campuses[this.data.campusIndex] || this.data.campuses[0];
      const selectedBuilding=this.data.buildings[this.data.buildingIndex] || this.data.buildings[0];
      const serviceStatus=(this.data.serviceStatuses[this.data.serviceStatusIndex]||{}).code||'';
      const submitted={kind:this.data.kind,q:this.data.form.q,category:this.data.form.category,facility:this.data.form.facility,capacity_min:this.data.form.capacity_min,capacity_max:this.data.form.capacity_max,serviceStatus,labId:selectedLab.id,campusId:selectedCampus.id,buildingId:selectedBuilding.id,slot};
      const querySignature=JSON.stringify([submitted,slot]);
      if(page>1 && querySignature!==this._appliedQuery) page=1;
      const queryChanged=querySignature!==this._appliedQuery;
      const serial = (this._serial || 0) + 1; this._serial = serial;
      this._failedPage=page;
      this.setData(queryChanged && page===1
        ? { loading: true, error: '', stale: false, items: [], total: 0, hasMore: false, queryReady: false }
        : { loading: true, error: '', stale: false });
      try {
        const data = await api.request('/resources/', 'GET', Object.assign({ kind:submitted.kind, q:submitted.q, category:submitted.category, facility:submitted.facility, capacity_min:submitted.capacity_min, capacity_max:submitted.capacity_max, service_status:submitted.serviceStatus, lab:submitted.labId,campus:submitted.campusId,building:submitted.buildingId, page }, slot ? {starts_at:slot.date+'T'+slot.start+':00',ends_at:slot.date+'T'+slot.end+':00'} : {}));
        if (serial !== this._serial) return;
        this._appliedSlot=slot;this._appliedQuery=querySignature;
        this._appliedFilterValues=submitted;this._appliedFilterOwner=api.session()?.draftScope;
        const labs=[{id:'',name:'全部管理单元'}].concat(data.labs);
        const campuses=[{id:'',name:'全部校区'}].concat(data.campuses||[]);
        const campusId=(campuses.find(c=>String(c.id)===String(submitted.campusId))||campuses[0]).id;
        const buildings=[{id:'',name:'全部楼宇'}].concat((data.buildings||[]).filter(b=>!campusId||String(b.campus_id)===String(campusId)));
        const chips=[];
        if(submitted.q)chips.push({key:'q',label:'搜索：'+submitted.q});
        if(submitted.labId)chips.push({key:'lab',label:'管理单元：'+selectedLab.name});
        if(submitted.campusId)chips.push({key:'campus',label:'校区：'+campuses.find(c=>String(c.id)===String(submitted.campusId))?.name});
        if(submitted.buildingId)chips.push({key:'building',label:'楼宇：'+buildings.find(b=>String(b.id)===String(submitted.buildingId))?.name});
        if(submitted.category)chips.push({key:'category',label:'类别：'+submitted.category});
        if(submitted.facility)chips.push({key:'facility',label:'设施：'+submitted.facility});
        if(submitted.capacity_min)chips.push({key:'capacity_min',label:'容量≥'+submitted.capacity_min});
        if(submitted.capacity_max)chips.push({key:'capacity_max',label:'容量≤'+submitted.capacity_max});
        if(submitted.serviceStatus)chips.push({key:'status',label:'状态：'+(this.data.serviceStatuses[this.data.serviceStatusIndex]||{}).name});
        if(slot)chips.push({key:'time',label:'时段：'+slot.date+' '+slot.start+'—'+slot.end});
        this._failedPage=page;
        this.setData({appliedTime:slot ? slot.date+' '+slot.start+'—'+slot.end : '',appliedChips:chips,appliedKind:submitted.kind==='ROOM'?'整间场地':'设备',queryReady:true,stale:false,items: (page === 1 ? [] : this.data.items).concat(data.items.map(fmt.resource)), labs,labIndex:Math.max(0,labs.findIndex(l=>String(l.id)===String(submitted.labId))),campuses,campusIndex:Math.max(0,campuses.findIndex(c=>String(c.id)===String(submitted.campusId))),buildings,buildingIndex:Math.max(0,buildings.findIndex(b=>String(b.id)===String(submitted.buildingId))),page: data.page, hasMore: data.has_more, total: data.total });
      } catch (e) { if (serial === this._serial) this.setData({error:api.errorText(e),stale:!queryChanged&&this.data.items.length>0}); } finally { if (serial === this._serial) this.setData({ loading: false }); }
    },
    retry() { return this.load(this._failedPage || 1); },
    search() { return this.load(1); },
    clearQueryFilter(e) {
      const saved=api.session(),applied=this._appliedFilterValues,key=e.currentTarget.dataset.key;
      if(!saved||saved.draftScope!==this._appliedFilterOwner||!applied||!['q','lab','campus','building','time','category','facility','capacity_min','capacity_max','status'].includes(key))return;
      const form={q:applied.q,category:applied.category,facility:applied.facility,capacity_min:applied.capacity_min,capacity_max:applied.capacity_max};
      if(key in form)form[key]='';
      this.setData({kind:applied.kind,form,labIndex:key==='lab'?0:Math.max(0,this.data.labs.findIndex(l=>String(l.id)===String(applied.labId))),campusIndex:key==='campus'?0:Math.max(0,this.data.campuses.findIndex(c=>String(c.id)===String(applied.campusId))),buildingIndex:key==='campus'||key==='building'?0:Math.max(0,this.data.buildings.findIndex(b=>String(b.id)===String(applied.buildingId))),timeSearch:key!=='time'&&!!applied.slot,serviceStatusIndex:key==='status'?0:Math.max(0,this.data.serviceStatuses.findIndex(x=>x.code===applied.serviceStatus))});
      if(applied.slot&&key!=='time')this.setData({slot:Object.assign({},applied.slot),quickDates:fmt.quickDates(applied.slot.date,this.data.policy?.window_days)});
      return this.load(1);
    },
    chooseKind(e) { this.setData({ kind: e.currentTarget.dataset.kind }); return this.load(1); },
    openRoomFinder() {
      if (!api.session()?.user.can_book_room) return;
      const filters = { kind: 'ROOM', advancedFilters: true };
      if (this.data.kind !== 'ROOM') {
        filters.form = { ...this.data.form, q: '', category: '', facility: '' };
      }
      this.setData(filters, () => wx.pageScrollTo({ selector: '#resource-search', duration: 0 }));
      return this.load(1);
    },
    chooseLab(e) { this.setData({ labIndex: Number(e.detail.value) }); return this.load(1); },
    chooseCampus(e) { this.setData({ campusIndex:Number(e.detail.value),buildingIndex:0 });return this.load(1); },
    chooseBuilding(e) { this.setData({ buildingIndex:Number(e.detail.value) });return this.load(1); },
    toggleFilters() { this.setData({advancedFilters:!this.data.advancedFilters}); },
    chooseServiceStatus(e) { this.setData({serviceStatusIndex:Number(e.detail.value)}); },
    more() { if (this.data.hasMore && !this.data.loading) return this.load(this.data.page + 1); },
    onPullDownRefresh() { return this.onShow().finally(endRefresh); },
    onReachBottom() { return this.more(); }
  },
  resource: {
    data: { id: null, item: null, form: fmt.defaultForm(), loading: false, busy: false, error: '', stale:false, hasPendingDraft:false, availability: '', available: null, minDate: fmt.day(), maxDate: fmt.day(Date.now() + 14 * 86400000), scheduleDay: '', schedulePage: 1, scheduleTotal: 0, scheduleHasMore: false, scheduleBusy: false, scheduleError: '', slotOptions:null,slotsBusy:false,slotsError:'',durations:[{minutes:60,label:'60分钟'}],durationIndex:0,safetyBusy:false },
    onLoad(options) {
      this.setData({ id: Number(options.id) });
      if (/^\d{4}-\d{2}-\d{2}$/.test(options.date || '') && [options.start,options.end].every(t => /^\d{2}:\d{2}$/.test(t || ''))) this.setData({'form.date':options.date,'form.start':options.start,'form.end':options.end,scheduleDay:options.date});
      const pending = api.draft(this.data.id);
      if (pending && pending.values.resource_id === this.data.id) {
        const value = pending.values;
        this.setData({ form: { date: value.starts_at.slice(0, 10), start: value.starts_at.slice(11, 16), end: value.ends_at.slice(11, 16), purpose: value.purpose, participants: String(value.participants) },hasPendingDraft:true });
      }
    },
    onShow() { if (api.requireLogin()) { if(ownerChanged(this))this.setData({item:null,form:fmt.defaultForm(),slotOptions:null,slotsError:'',availability:'',available:null,hasPendingDraft:false});const pending=api.draft(this.data.id);if(pending)this.setData({hasPendingDraft:true,form:{date:pending.values.starts_at.slice(0,10),start:pending.values.starts_at.slice(11,16),end:pending.values.ends_at.slice(11,16),purpose:pending.values.purpose,participants:String(pending.values.participants)}});else this.setData({hasPendingDraft:false});this.setData({minDate:fmt.day(),quickDates:fmt.quickDates(this.data.form.date,this.data.item?.policy?.window_days)}); return this.load(); } },
    async load() {
      const serial=(this._scheduleSerial||0)+1;this._scheduleSerial=serial;
      invalidateAvailability(this);
      this.setData({ loading: true, scheduleBusy: false, error: '', stale:!!this.data.item });
      try {
        const item = await api.request('/resources/' + this.data.id + '/', 'GET', {schedule_day:this.data.scheduleDay,schedule_page:1});
        if(serial!==this._scheduleSerial)return;
        this.setData({ item: fmt.resource(item), maxDate: fmt.day(Date.now() + item.policy.window_days * 86400000), schedulePage:item.schedule_page, scheduleTotal:item.schedule_total, scheduleHasMore:item.schedule_has_more, scheduleError:'', stale:false });
        const durations=durationOptions(item.policy),selected=durations.findIndex(x=>x.minutes===60);
        this.setData({durations,durationIndex:selected<0?0:selected,quickDates:fmt.quickDates(this.data.form.date,item.policy.window_days)});
      } catch (e) { if(serial===this._scheduleSerial)this.setData({error:api.errorText(e),stale:!!this.data.item}); } finally { if(serial===this._scheduleSerial)this.setData({ loading: false, scheduleBusy: false }); }
    },
    retry() { return this.load(); },
    onPullDownRefresh() { return this.load().finally(endRefresh); },
    async loadSchedule(page) {
      const serial=(this._scheduleSerial||0)+1;this._scheduleSerial=serial;
      this.setData({scheduleBusy:true,scheduleError:''});
      try {
        const item=await api.request('/resources/'+this.data.id+'/', 'GET', {schedule_day:this.data.scheduleDay,schedule_page:page});
        if(serial!==this._scheduleSerial)return;
        const slots=fmt.resource(item).schedule;
        this.setData({'item.schedule':(page===1?[]:this.data.item.schedule).concat(slots),schedulePage:item.schedule_page,scheduleTotal:item.schedule_total,scheduleHasMore:item.schedule_has_more});
      } catch(e) {if(serial===this._scheduleSerial)this.setData({scheduleError:api.errorText(e)});}
      finally {if(serial===this._scheduleSerial)this.setData({scheduleBusy:false,loading:false});}
    },
    chooseScheduleDay(e) {this.setData({scheduleDay:e.detail.value,'item.schedule':[],scheduleTotal:0,scheduleHasMore:false});return this.loadSchedule(1);},
    clearScheduleDay() {this.setData({scheduleDay:'','item.schedule':[],scheduleTotal:0,scheduleHasMore:false});return this.loadSchedule(1);},
    moreSchedule() {if(this.data.scheduleHasMore&&!this.data.scheduleBusy&&!this.data.loading)return this.loadSchedule(this.data.schedulePage+1);},
    input(event) { if(!this.data.busy)input.call(this,event); },
    chooseDate(e) { if(this.data.busy)return;this._slotsSerial=(this._slotsSerial||0)+1;this.setData({ 'form.date': e.detail.value,quickDates:fmt.quickDates(e.detail.value,this.data.item?.policy?.window_days),availability: '', available: null, loading:invalidateAvailability(this),slotOptions:null,slotsBusy:false,slotsError:'' }); },
    chooseQuickDate(e) {this.chooseDate({detail:{value:e.currentTarget.dataset.day}});},
    chooseStart(e) { if(this.data.busy)return;this.setData({ 'form.start': e.detail.value, availability: '', available: null, loading:invalidateAvailability(this) }); },
    chooseEnd(e) { if(this.data.busy)return;this.setData({ 'form.end': e.detail.value, availability: '', available: null, loading:invalidateAvailability(this) }); },
    chooseDuration(e) {if(this.data.busy)return;this._slotsSerial=(this._slotsSerial||0)+1;this.setData({durationIndex:Number(e.detail.value),slotOptions:null,slotsError:'',slotsBusy:false});},
    async loadSlots() {
      if(!api.requireLogin() || this.data.busy || this.data.slotsBusy || !this.data.item || this.data.stale)return;
      const serial=(this._slotsSerial||0)+1;this._slotsSerial=serial;
      const duration=this.data.durations[this.data.durationIndex];if(!duration)return;
      this.setData({slotsBusy:true,slotsError:'',slotOptions:null});
      try {
        const data=await api.request('/resources/'+this.data.id+'/', 'GET', {slot_day:this.data.form.date,duration_minutes:duration.minutes});
        if(serial!==this._slotsSerial)return;
        if(!data.slot_options)throw new Error('服务尚未提供候选时段，请使用下方起止时间查询');
        this.setData({slotOptions:Object.assign({},data.slot_options,{checked_label:fmt.dateTime(data.slot_options.checked_at)})});
      } catch(e){if(serial===this._slotsSerial)this.setData({slotsError:api.errorText(e)});}
      finally{if(serial===this._slotsSerial)this.setData({slotsBusy:false});}
    },
    selectSlot(e) {
      const options=this.data.slotOptions,item=options && options.items[Number(e.currentTarget.dataset.index)];
      if(!item || !item.available || this.data.busy)return;
      this.setData({'form.date':item.starts_at.slice(0,10),'form.start':item.starts_at.slice(11,16),'form.end':item.ends_at.slice(11,16),availability:'已带入所选时段，提交时将重新检查资格、配额与冲突',available:null,loading:invalidateAvailability(this)});
    },
    reportIncident() {if(!this.data.busy && !this.data.safetyBusy && api.requireLogin() && this.data.item)wx.navigateTo({url:'/pages/incident/index?resource='+this.data.id});},
    async acknowledgeSafety(event) {
      const item=this.data.item;
      const requestedId=Number(event && event.currentTarget && event.currentTarget.dataset && event.currentTarget.dataset.procedureId);
      const procedure=(item && (item.safety_procedures||[]).find(row=>row.id===requestedId)) || (item && item.safety_procedure);
      if(!procedure || !procedure.requires_acknowledgement || this.data.safetyBusy || this.data.busy || this.data.stale || !api.requireLogin())return;
      this.setData({safetyBusy:true,error:''});
      try {
        await api.request('/resources/'+this.data.id+'/safety/'+procedure.id+'/ack/','POST',{});
        wx.showToast({title:'已记录本版本阅读确认',icon:'success'});
        await this.load();
      } catch(e) {error(this,e);}
      finally {this.setData({safetyBusy:false});}
    },
    async checkAvailability() {
      if(this.data.busy)return;
      if(this.data.stale){this.setData({error:'资源信息可能已过期，请先重新加载'});return;}
      const f = Object.assign({},this.data.form);
      const serial=(this._availabilitySerial||0)+1;this._availabilitySerial=serial;
      this._availabilityPending=true;
      this.setData({ loading: true, error: '' });
      try {
        const item = await api.request('/resources/' + this.data.id + '/', 'GET', { starts_at: f.date + 'T' + f.start + ':00', ends_at: f.date + 'T' + f.end + ':00' });
        if(serial===this._availabilitySerial)this.setData({ availability: item.available_label, available: item.available });
      } catch (e) { if(serial===this._availabilitySerial)error(this, e); } finally { if(serial===this._availabilitySerial){this._availabilityPending=false;this.setData({ loading: false });} }
    },
    async submit() {
      if (this.data.busy || this.data.safetyBusy || this._submitPending || !api.requireLogin()) return;
      const submitOwner=sessionStamp();
      const f = Object.assign({}, this.data.form),initialItem=this.data.item,resourceId=Number(this.data.id || (initialItem && initialItem.id));
      if(!Number.isInteger(resourceId) || resourceId < 1)return;
      this._submitPending=true;
      this.setData({busy:true,error:''});
      try {
        const pending=api.draft(resourceId);
        if(pending){
          const requested={resource_id:resourceId,starts_at:f.date+'T'+f.start+':00',ends_at:f.date+'T'+f.end+':00',purpose:String(f.purpose||'').trim(),participants:initialItem && initialItem.kind==='ROOM'?Number(f.participants):Number(f.participants||1)};
          const keys=['resource_id','starts_at','ends_at','purpose','participants'];
          if(!keys.every(key=>requested[key]===pending.values[key])){
            const original=pending.values;
            this.setData({form:{date:original.starts_at.slice(0,10),start:original.starts_at.slice(11,16),end:original.ends_at.slice(11,16),purpose:original.purpose,participants:String(original.participants)},error:'上一项预约结果尚未确认，已恢复原申请内容。请重新核对原操作结果。'});
            return;
          }
          const receipt=await api.checkBooking(resourceId);
          let booking;
          if(receipt.found && receipt.kind==='booking' && receipt.result && receipt.result.id){
            booking=receipt.result;
            this.setData({hasPendingDraft:false});
            wx.showToast({title:'已找到此前预约结果',icon:'success'});
          }else if(receipt.found){
            this.setData({error:'请求编号已有其他操作回执，请联系管理员核对；系统未重复提交预约'});return;
          }else{
            // A missing receipt is not proof of failure. Replay only the exact
            // saved payload and idempotency key; server rules still apply.
            booking=await api.submitBooking(pending.values);
            this.setData({hasPendingDraft:false});
            wx.showToast({title:booking.status==='CONFIRMED'?'预约已确认':'申请已提交',icon:'success'});
          }
          wx.redirectTo({url:'/pages/booking/index?id='+booking.id});
          return;
        }
        if(!initialItem)return;
        if (this.data.stale) { this.setData({ error: '资源信息可能已过期，请先重新加载后再预约' }); return; }
        // Approval mode, eligibility and resource restrictions can change after
        // the detail page was opened. Present the confirmation using a fresh
        // server snapshot; the booking endpoint still performs final validation.
        const latestData=await api.request('/resources/'+this.data.id+'/', 'GET');
        if(['date','start','end','purpose','participants'].some(key=>String(this.data.form[key]||'')!==String(f[key]||''))){
          this.setData({error:'预约表单在刷新期间发生变化，请核对当前信息后重新提交'});return;
        }
        const item=fmt.resource(latestData);
        this.setData({item,stale:false});
        if (!item.can_apply) { this.setData({ error: '仅教师可预约整间实验室，学生可以预约设备' }); return; }
        if (!item.can_reserve) { this.setData({ error: item.reservation_block_reason || '所属管理单元或资源当前不可预约' }); return; }
        const message = fmt.validateForm(f, item.policy);
        if (message) { this.setData({ error: message }); return; }
        if (item.kind === 'ROOM' && (!/^\d+$/.test(f.participants) || Number(f.participants) < 1 || Number(f.participants) > item.capacity)) { this.setData({ error: '参与人数须为1至' + item.capacity + '之间的整数' }); return; }
        const purpose=f.purpose.trim();
        const confirmationText=[
          '资源：'+item.name,
          '时段：'+f.date+' '+f.start+'—'+f.end+'（北京时间）',
          '用途：'+purpose,
          '处理方式：'+(item.requires_approval?'提交后由管理员人工审核':'规则通过后自动确认')
        ];
        if(item.kind==='ROOM')confirmationText.push('参与人数：'+Number(f.participants));
        const accepted=await confirm({title:'请核对预约信息',content:confirmationText.join('\n'),confirmText:item.requires_approval?'提交申请':'确认预约',cancelText:'返回修改'});
        if(!accepted)return;
        if(!submitOwner || sessionStamp()!==submitOwner){this.setData({error:'登录身份已变化，本次没有提交预约。请核对当前账号后重新操作'});return;}
        const booking = await api.submitBooking({ resource_id: item.id, starts_at: f.date + 'T' + f.start + ':00', ends_at: f.date + 'T' + f.end + ':00', purpose, participants: item.kind === 'ROOM' ? Number(f.participants) : 1, expected_approval_mode:item.requires_approval?'MANUAL':'AUTO' });
        this.setData({hasPendingDraft:false});
        wx.redirectTo({ url: '/pages/booking/index?id=' + booking.id });
        wx.showToast({ title: booking.status === 'CONFIRMED' ? '预约已确认' : '申请已提交', icon: 'success' });
      } catch (e) { this.setData({hasPendingDraft:!!api.draft(this.data.id)});error(this, e); } finally { this._submitPending=false;this.setData({ busy: false }); }
    }
  },
  bookings: {
    data: { items: [], statuses, statusIndex: 0, form: { q: '' }, loading: false, error: '', page: 1, hasMore: false, total: 0, appliedFilter:false },
    onShow() {
      if (!api.requireLogin()) return;
      const status=wx.getStorageSync('campus-mini-booking-filter');
      if(status && typeof status==='object' && typeof status.status==='string'){wx.removeStorageSync('campus-mini-booking-filter');const index=statuses.findIndex(s=>s.code===status.status);this.setData({statusIndex:index<0?0:index});}
      return this.load(1);
    },
    input, openBooking,
    async load(page) {
      page = page || 1;
      const query={status:statuses[this.data.statusIndex].code,q:this.data.form.q};
      const signature=JSON.stringify(query);
      if(page>1 && signature!==this._appliedQuery)page=1;
      const serial = (this._serial || 0) + 1; this._serial = serial;
      this._failedPage=page;
      this.setData({ loading: true, error: '', ...(page===1?{items:[],hasMore:false,total:0}: {}) });
      try {
        const result = await api.request('/bookings/', 'GET', Object.assign({},query,{page}));
        if (serial !== this._serial) return;
        this._appliedQuery=signature;
        this.setData({ items: (page === 1 ? [] : this.data.items).concat(result.items.map(fmt.booking)), page: result.page, hasMore: result.has_more, total: result.total, appliedFilter:!!(query.status || query.q.trim()) });
      } catch (e) { if (serial === this._serial) error(this, e); } finally { if (serial === this._serial) this.setData({ loading: false }); }
    },
    retry() { return this.load(this._failedPage || 1); },
    chooseStatus(e) { this.setData({ statusIndex: Number(e.currentTarget.dataset.index) }); return this.load(1); },
    search() { return this.load(1); },
    more() { if (this.data.hasMore && !this.data.loading) return this.load(this.data.page + 1); },
    onPullDownRefresh() { return this.load(1).finally(endRefresh); },
    onReachBottom() { return this.more(); }
  },
  booking: {
    data: { id: null, item: null, loading: false, busy: false, error: '', stale:false, success:'',supplementUnknown:false, form: { reason: '',supplement:'' } },
    onLoad(options) { this.setData({ id: Number(options.id) }); },
    onShow() { if (api.requireLogin()) {if(ownerChanged(this))this.setData({item:null,form:{reason:'',supplement:''},success:'',supplementUnknown:false});this.restoreSupplement();return this.load();} },
    input,
    async load() {
      if(this._cancelPending || this._supplementPending)return;
      const serial=(this._detailSerial||0)+1;this._detailSerial=serial;
      this.setData({ loading: true, error: '', stale:!!this.data.item });
      try {
        const item = await api.request('/bookings/' + this.data.id + '/');
        if(serial!==this._detailSerial)return;
        item.timeline = item.timeline.map(t => Object.assign({}, t, { time_label: fmt.dateTime(t.at) }));
        this.setData({ item: fmt.booking(item), stale:false });
      } catch (e) { if(serial===this._detailSerial)this.setData({error:api.errorText(e),stale:!!this.data.item}); } finally { if(serial===this._detailSerial)this.setData({ loading: false }); }
    },
    retry() { return this.load(); },
    restoreSupplement() {
      const pending=api.actionDraft('supplement:'+this.data.id);
      this.setData({supplementUnknown:!!pending});
      if(pending)this.setData({'form.supplement':pending.values.reason});
    },
    async checkSupplement() {
      if(this.data.busy || this.data.stale)return;
      this.setData({busy:true,error:'',success:''});
      try {
        const result=await api.checkAction('supplement:'+this.data.id);
        if(result.found){this.setData({'form.supplement':'',supplementUnknown:false,success:'已找到原补充说明的保存回执，无需再次提交。'});await this.load();}
        else this.setData({error:result.notice || '暂未找到回执，不能据此判断失败；请保持原内容重试。'});
      }catch(e){error(this,e);}finally{this.setData({busy:false});}
    },
    async supplement() {
      if(this.data.busy || !this.data.item || this.data.stale || !api.requireLogin())return;
      if(!this.data.item.can_supplement && !api.actionDraft('supplement:'+this.data.id)){this.setData({error:'当前预约不能补充申请说明，请刷新查看状态'});return;}
      const reason=this.data.form.supplement.trim();
      if(!reason || reason.length>1500){this.setData({error:'请填写1至1500字的补充说明'});return;}
      this._detailSerial=(this._detailSerial||0)+1;this._supplementPending=true;
      this.setData({busy:true,loading:false,error:'',success:''});
      try {
        await api.submitAction('/bookings/'+this.data.id+'/supplement/',{reason},'supplement:'+this.data.id);
        this.setData({'form.supplement':'',supplementUnknown:false,success:'补充说明已保存，预约仍为待审核，管理员会收到站内通知。'});
        this._supplementPending=false;await this.load();
        wx.showToast({title:'说明已保存',icon:'success'});
      } catch(e){this.setData({supplementUnknown:!!api.actionDraft('supplement:'+this.data.id)});error(this,e);}
      finally{this._supplementPending=false;this.setData({busy:false});}
    },
    reportIncident() {
      if(api.requireLogin() && !this.data.stale && this.data.item && this.data.item.can_report)wx.navigateTo({url:'/pages/incident/index?resource='+this.data.item.resource_id+'&reservation='+this.data.id});
    },
    async cancel() {
      if (this.data.busy || !this.data.item || this.data.stale) return;
      const reason = this.data.form.reason.trim();
      if (!reason) { this.setData({ error: '请填写取消原因' }); return; }
      const originalSession=sessionStamp(),bookingId=this.data.id,version=this.data.item.version;
      if (!await confirm({ title: '取消这项预约？', content: '确认后会释放尚未使用的时段。', confirmText: '确认取消', confirmColor: '#9b5b43' })) return;
      if(this.data.busy || !originalSession || sessionStamp()!==originalSession || this.data.id!==bookingId || !this.data.item || this.data.item.version!==version)return;
      this._detailSerial=(this._detailSerial||0)+1;this._cancelPending=true;
      this.setData({ busy: true, loading:false, error: '' });
      try {
        const item=await api.request('/bookings/' + bookingId + '/cancel/', 'POST', { reason, version });
        if(item && item.id){item.timeline=(item.timeline||[]).map(t=>Object.assign({},t,{time_label:fmt.dateTime(t.at)}));this.setData({item:fmt.booking(item)});}
        this._cancelPending=false;
        await this.load(); wx.showToast({ title: '预约已取消', icon: 'success' });
      } catch (e) { error(this, e); } finally { this._cancelPending=false;this.setData({ busy: false }); }
    },
    onPullDownRefresh() { return this.load().finally(endRefresh); }
  },
  incident: {
    data:{resourceId:null,reservationId:null,item:null,booking:null,loading:false,busy:false,error:'',result:null,pendingUnknown:false,kinds:[{code:'DAMAGE',name:'设备损坏'},{code:'MISSING',name:'配件缺失'},{code:'FAULT',name:'运行或场地异常'}],kindIndex:0,form:{description:'',date:'',time:''}},
    onLoad(options) {
      const now=nowFields();this.setData({resourceId:positiveId(options.resource),reservationId:options.reservation?positiveId(options.reservation):null,form:{description:'',date:now.date,time:now.time}});
      if(!this.data.resourceId || (options.reservation && !this.data.reservationId))this.setData({error:'资源或预约编号无效，请返回资源详情重新进入'});
    },
    onShow() {
      if(!api.requireLogin())return;
      if(ownerChanged(this)){const now=nowFields();this.setData({item:null,booking:null,result:null,pendingUnknown:false,form:{description:'',date:now.date,time:now.time}});}
      this.restorePending();return this.load();
    },
    input,
    chooseDate(e){this.setData({'form.date':e.detail.value,error:''});},
    chooseTime(e){this.setData({'form.time':e.detail.value,error:''});},
    chooseKind(e){this.setData({kindIndex:Number(e.detail.value),error:''});},
    restorePending() {
      const pending=api.actionDraft(incidentContext(this.data.resourceId,this.data.reservationId));
      this.setData({pendingUnknown:!!pending});
      if(pending){const f=pending.values;this.setData({form:{description:f.description,date:f.occurred_at.slice(0,10),time:f.occurred_at.slice(11,16)},kindIndex:Math.max(0,this.data.kinds.findIndex(x=>x.code===f.kind))});}
    },
    async checkPending() {
      if(this.data.busy)return;
      this.setData({busy:true,error:''});
      try {
        const result=await api.checkAction(incidentContext(this.data.resourceId,this.data.reservationId));
        if(result.found)this.setData({result:result.result,pendingUnknown:false});
        else this.setData({error:result.notice || '暂未找到回执，不能据此判断失败；请保持原内容重试。'});
      }catch(e){error(this,e);}finally{this.setData({busy:false});}
    },
    async load() {
      if(!this.data.resourceId)return;
      const serial=(this._serial||0)+1;this._serial=serial;
      this.setData({loading:true,error:''});
      try {
        const values=await Promise.all([api.request('/resources/'+this.data.resourceId+'/'),this.data.reservationId?api.request('/bookings/'+this.data.reservationId+'/'):Promise.resolve(null)]);
        if(serial!==this._serial)return;
        if(values[1] && values[1].resource_id!==this.data.resourceId)throw new Error('关联预约与资源不一致，请从预约详情重新进入');
        this.setData({item:fmt.resource(values[0]),booking:values[1]?fmt.booking(values[1]):null});
      }catch(e){if(serial===this._serial){this.setData({item:null,booking:null});error(this,e);}}
      finally{if(serial===this._serial)this.setData({loading:false});}
    },
    async submit() {
      if(this.data.busy || this.data.loading || this.data.result || !this.data.item || !api.requireLogin())return;
      const form=this.data.form,kind=this.data.kinds[this.data.kindIndex],description=form.description.trim();
      if(!description || description.length>2000){this.setData({error:'请填写1至2000字的具体情况'});return;}
      const occurred_at=form.date+'T'+form.time+':00';
      if(!kind || !/^\d{4}-\d{2}-\d{2}$/.test(form.date) || !/^\d{2}:\d{2}$/.test(form.time) || !Number.isFinite(Date.parse(occurred_at+'+08:00')) || Date.parse(occurred_at+'+08:00')>Date.now()){this.setData({error:'请填写有效且不晚于当前时间的发现时间'});return;}
      const values={resource_id:this.data.resourceId,reservation:this.data.reservationId,kind:kind.code,description,occurred_at};
      const submittingSession=sessionStamp();
      if(!await confirm({title:'提交异常说明？',content:'请核实资源、发现时间和具体情况。提交后由管理员按现有规则核实与处置。',confirmText:'确认提交'}))return;
      if(this.data.busy || !submittingSession || sessionStamp()!==submittingSession || this.data.resourceId!==values.resource_id || this.data.reservationId!==values.reservation)return;
      this.setData({busy:true,error:''});
      try {
        const result=await api.submitAction('/incidents/',values,incidentContext(this.data.resourceId,this.data.reservationId));
        this.setData({result,pendingUnknown:false});wx.showToast({title:'异常已登记',icon:'success'});
      }catch(e){this.setData({pendingUnknown:!!api.actionDraft(incidentContext(this.data.resourceId,this.data.reservationId))});error(this,e);}
      finally{this.setData({busy:false});}
    },
    returnToResource(){wx.redirectTo({url:'/pages/resource/index?id='+this.data.resourceId});}
  },
  notifications: {
    data: { items: [], loading: false, busy: false, error: '', unread: 0, unreadOnly: false, page: 1, hasMore: false, staleCount:false },
    onShow() { if (api.requireLogin()) return this.load(1); },
    async load(page) {
      const serial=(this._serial||0)+1;this._serial=serial;
      this._failedPage=page||1;
      this.setData({ loading: true, error: '', ...((!page||page===1)?{items:[],hasMore:false,staleCount:true}: {}) });
      try {
        const result = await api.request('/notifications/', 'GET', { page: this._failedPage, unread: this.data.unreadOnly ? '1' : '' });
        if(serial!==this._serial)return;
        this.setData({ items: (this._failedPage > 1 ? this.data.items : []).concat(result.items.map(fmt.notify)), page: result.page, hasMore: result.has_more, unread: result.unread, staleCount:false });
      } catch (e) { if(serial===this._serial)error(this, e); } finally { if(serial===this._serial)this.setData({ loading: false }); }
    },
    retry() { return this.load(this._failedPage || 1); },
    chooseUnread(e) {this.setData({unreadOnly:e.currentTarget.dataset.mode==='unread'});return this.load(1);},
    async open(e) {
      const id = Number(e.currentTarget.dataset.id);const item = this.data.items.find(x => x.id === id);
      if (!item || this.data.busy) return;
      this.setData({ busy: true, loading:false, error: '' });
      try {
        const serial=(this._serial||0)+1;this._serial=serial;
        if (item.unread) await api.request('/notifications/' + id + '/read/', 'POST', {});
        if(serial===this._serial)this.setData({ items: this.data.items.map(x => x.id === id ? Object.assign({}, x, { unread: false, read_at: new Date().toISOString() }) : x), unread: Math.max(0, this.data.unread - (item.unread ? 1 : 0)) });
        if(this.data.unreadOnly || serial!==this._serial) await this.load(1);
        if (item.booking_id) wx.navigateTo({ url: '/pages/booking/index?id=' + item.booking_id });
      } catch (e2) { error(this, e2); } finally { this.setData({ busy: false }); }
    },
    async markAll() {
      if (this.data.busy) return;
      this.setData({ busy: true, loading:false, error: '' });
      this._serial=(this._serial||0)+1;
      try { await api.request('/notifications/read-all/', 'POST', {}); await this.load(1); }
      catch (e) { error(this, e); } finally { this.setData({ busy: false }); }
    },
    more() { if (this.data.hasMore && !this.data.loading) return this.load(this.data.page + 1); },
    onReachBottom() { return this.more(); },
    onPullDownRefresh() { return this.load(1).finally(endRefresh); }
  },
  profile: {
    data: { user: null, stats: null, policy: null, loading: false, error: '', busy: false, roleLabel: '', qualifications: [] },
    onShow() { if (api.requireLogin()) return this.load(); },
    async load() {
      this.setData({ loading: true, error: '' });
      try { const data = await api.request('/me/'); this.setData({ user: data.user, stats: data.stats, policy: data.policy, overdue: data.overdue, qualifications: (data.qualifications || []).map(q=>Object.assign({},q,{expiry_label:q.expires_at ? fmt.dateTime(q.expires_at) : '未设到期时间'})), roleLabel: data.user.roles.map(x => x.name).join(' / ')}); }
      catch (e) { error(this, e); } finally { this.setData({ loading: false }); }
    },
    chooseEffects(e) {const reduced=e.detail.value===true;wx.setStorageSync('campus-mini-simple-effects',reduced);this.setData({reducedEffects:reduced});},
    async signout() {
      if(this.data.busy)return;
      const originalSession=sessionStamp();
      if (!originalSession || !await confirm({ title: '退出当前账号？', content: '已有预约会保留，登录后可继续查询。', confirmText: '退出' })) return;
      if(this.data.busy || sessionStamp()!==originalSession)return;
      this.setData({ busy: true, error: '' });
      const stillOwnsNavigation=()=>!api.session() || sessionStamp()===originalSession;
      try { await api.logout(); if(stillOwnsNavigation())wx.reLaunch({ url: '/pages/login/index' }); }
      catch (e) { if(stillOwnsNavigation()){wx.reLaunch({ url: '/pages/login/index' });wx.showToast({title:'已本地退出，远端会话将到期',icon:'none'});} } finally { this.setData({ busy: false }); }
    }
  }
};
function create(name) {
  const definition = screens[name];
  if (!definition) throw new Error('Unknown page: ' + name);
  const data = JSON.parse(JSON.stringify(definition.data));
  data.reducedEffects=false;
  if(['resources','resource'].includes(name))Object.assign(data,{scenePhoto:photos.get('scene'),photoErrors:{}});
  if(name==='resources')Object.assign(data,{appliedChips:[],appliedKind:'',queryReady:false});
  if (name === 'resources') Object.assign(data,{slot:fmt.defaultForm(),minDate:fmt.day(),maxDate:fmt.day(Date.now()+14*86400000)});
  if (name === 'resource') Object.assign(data, { form: fmt.defaultForm(), minDate: fmt.day(), maxDate: fmt.day(Date.now() + 14 * 86400000) });
  if(name==='resource'||name==='resources')data.quickDates=fmt.quickDates((name==='resource'?data.form:data.slot).date,14);
  const onShow=definition.onShow;
  const defaults=JSON.parse(JSON.stringify(data));
  return Object.assign({}, definition, { data,photoError,copyPhotoSource,onShow(){
    const saved=api.session(),scope=saved && saved.draftScope;
    if(this._displayOwner && this._displayOwner!==scope){
      const route={};for(const key of ['id','resourceId','reservationId'])if(Object.prototype.hasOwnProperty.call(this.data,key))route[key]=this.data[key];
      const fresh=JSON.parse(JSON.stringify(defaults));
      if(name==='resource')fresh.form=fmt.defaultForm();
      if(name==='resources')fresh.slot=fmt.defaultForm();
      if(name==='incident'){const now=nowFields();fresh.form={description:'',date:now.date,time:now.time};}
      this.setData(Object.assign(fresh,route));
      this._appliedFilterValues=null;this._appliedFilterOwner=null;this._appliedSlot=null;this._appliedQuery=null;
      for(const key of ['_serial','_overviewSerial','_scheduleSerial','_detailSerial','_availabilitySerial','_slotsSerial'])this[key]=(this[key]||0)+1;
    }
    this._displayOwner=scope;
    this.setData({reducedEffects:wx.getStorageSync('campus-mini-simple-effects')===true});
    return onShow ? onShow.call(this) : undefined;
  } });
}
module.exports = { create };
