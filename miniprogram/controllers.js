const api = require('./utils/api');
const config = require('./config');
const fmt = require('./utils/format');
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
    data: { items: [], labs: [{ id: '', name: '全部实验室' }], labIndex: 0, kind: 'EQUIPMENT', form: { q: '' }, loading: false, error: '', hasMore: false, page: 1, total: 0, user: null, stats: { pending: 0, confirmed: 0, using: 0 }, upcoming: [], policy: null, overviewReady: false, overviewLoading: false, overviewError: '', timeSearch: false, slot: fmt.defaultForm(), appliedTime: '', minDate: fmt.day(), maxDate: fmt.day(Date.now()+14*86400000) },
    async onShow() {
      if (!api.requireLogin()) return;
      const serial=(this._overviewSerial || 0)+1;this._overviewSerial=serial;
      this.setData({user:api.session().user,overviewLoading:true,overviewError:'',minDate:fmt.day()});
      const overview=api.request('/me/').then(data=>{
        if(serial!==this._overviewSerial)return;
        this.setData({user:data.user,stats:data.stats,overdue:data.overdue,policy:data.policy,upcoming:(data.upcoming || []).map(fmt.booking),overviewReady:true,maxDate:fmt.day(Date.now()+(data.policy ? data.policy.window_days : 14)*86400000)});
      }).catch(e=>{if(serial===this._overviewSerial)this.setData({overviewError:api.errorText(e)});})
        .finally(()=>{if(serial===this._overviewSerial)this.setData({overviewLoading:false});});
      return Promise.all([overview,this.load(1)]);
    },
    input, openResource, openBooking, openBookings,
    toggleTime() {this.setData({timeSearch:!this.data.timeSearch});if(!this.data.timeSearch)return this.load(1);},
    chooseQueryDate(e) {this.setData({'slot.date':e.detail.value});},
    chooseQueryStart(e) {this.setData({'slot.start':e.detail.value});},
    chooseQueryEnd(e) {this.setData({'slot.end':e.detail.value});},
    resetSearch() {this.setData({form:{q:''},labIndex:0,timeSearch:false});return this.load(1);},
    async load(page) {
      if (!api.requireLogin()) return;
      page = page || 1;
      const slot = this.data.timeSearch ? Object.assign({},this.data.slot) : null;
      if(slot && slot.start>=slot.end){this.setData({error:'查询结束时间必须晚于开始时间'});return;}
      const querySignature=JSON.stringify([this.data.kind,this.data.form.q,(this.data.labs[this.data.labIndex] || this.data.labs[0]).id,slot]);
      if(page>1 && querySignature!==this._appliedQuery) page=1;
      const serial = (this._serial || 0) + 1; this._serial = serial;
      this.setData({ loading: true, error: '' });
      try {
        const data = await api.request('/resources/', 'GET', Object.assign({ kind: this.data.kind, q: this.data.form.q, lab: (this.data.labs[this.data.labIndex] || this.data.labs[0]).id, page }, slot ? {starts_at:slot.date+'T'+slot.start+':00',ends_at:slot.date+'T'+slot.end+':00'} : {}));
        if (serial !== this._serial) return;
        this._appliedSlot=slot;this._appliedQuery=querySignature;
        this.setData({appliedTime:slot ? slot.date+' '+slot.start+'—'+slot.end : '', items: (page === 1 ? [] : this.data.items).concat(data.items.map(fmt.resource)), labs: [{ id: '', name: '全部实验室' }].concat(data.labs), page: data.page, hasMore: data.has_more, total: data.total });
      } catch (e) { if (serial === this._serial) error(this, e); } finally { if (serial === this._serial) this.setData({ loading: false }); }
    },
    search() { return this.load(1); },
    chooseKind(e) { this.setData({ kind: e.currentTarget.dataset.kind }); return this.load(1); },
    chooseLab(e) { this.setData({ labIndex: Number(e.detail.value) }); return this.load(1); },
    more() { if (this.data.hasMore && !this.data.loading) return this.load(this.data.page + 1); },
    onPullDownRefresh() { return this.onShow().finally(endRefresh); },
    onReachBottom() { return this.more(); }
  },
  resource: {
    data: { id: null, item: null, form: fmt.defaultForm(), loading: false, busy: false, error: '', availability: '', available: null, minDate: fmt.day(), maxDate: fmt.day(Date.now() + 14 * 86400000), scheduleDay: '', schedulePage: 1, scheduleTotal: 0, scheduleHasMore: false, scheduleBusy: false, scheduleError: '', slotOptions:null,slotsBusy:false,slotsError:'',durations:[{minutes:60,label:'60分钟'}],durationIndex:0 },
    onLoad(options) {
      this.setData({ id: Number(options.id) });
      if (/^\d{4}-\d{2}-\d{2}$/.test(options.date || '') && [options.start,options.end].every(t => /^\d{2}:\d{2}$/.test(t || ''))) this.setData({'form.date':options.date,'form.start':options.start,'form.end':options.end,scheduleDay:options.date});
      const pending = api.draft(this.data.id);
      if (pending && pending.values.resource_id === this.data.id) {
        const value = pending.values;
        this.setData({ form: { date: value.starts_at.slice(0, 10), start: value.starts_at.slice(11, 16), end: value.ends_at.slice(11, 16), purpose: value.purpose, participants: String(value.participants) } });
      }
    },
    onShow() { if (api.requireLogin()) { if(ownerChanged(this))this.setData({item:null,form:fmt.defaultForm(),slotOptions:null,slotsError:'',availability:'',available:null});this.setData({minDate:fmt.day()}); return this.load(); } },
    async load() {
      const serial=(this._scheduleSerial||0)+1;this._scheduleSerial=serial;
      invalidateAvailability(this);
      this.setData({ loading: true, scheduleBusy: false, error: '' });
      try {
        const item = await api.request('/resources/' + this.data.id + '/', 'GET', {schedule_day:this.data.scheduleDay,schedule_page:1});
        if(serial!==this._scheduleSerial)return;
        this.setData({ item: fmt.resource(item), maxDate: fmt.day(Date.now() + item.policy.window_days * 86400000), schedulePage:item.schedule_page, scheduleTotal:item.schedule_total, scheduleHasMore:item.schedule_has_more, scheduleError:'' });
        const durations=durationOptions(item.policy),selected=durations.findIndex(x=>x.minutes===60);
        this.setData({durations,durationIndex:selected<0?0:selected});
      } catch (e) { if(serial===this._scheduleSerial)error(this, e); } finally { if(serial===this._scheduleSerial)this.setData({ loading: false, scheduleBusy: false }); }
    },
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
    input,
    chooseDate(e) { this._slotsSerial=(this._slotsSerial||0)+1;this.setData({ 'form.date': e.detail.value, availability: '', available: null, loading:invalidateAvailability(this),slotOptions:null,slotsBusy:false,slotsError:'' }); },
    chooseStart(e) { this.setData({ 'form.start': e.detail.value, availability: '', available: null, loading:invalidateAvailability(this) }); },
    chooseEnd(e) { this.setData({ 'form.end': e.detail.value, availability: '', available: null, loading:invalidateAvailability(this) }); },
    chooseDuration(e) {this._slotsSerial=(this._slotsSerial||0)+1;this.setData({durationIndex:Number(e.detail.value),slotOptions:null,slotsError:'',slotsBusy:false});},
    async loadSlots() {
      if(!api.requireLogin() || this.data.slotsBusy || !this.data.item)return;
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
    reportIncident() {if(api.requireLogin() && this.data.item)wx.navigateTo({url:'/pages/incident/index?resource='+this.data.id});},
    async checkAvailability() {
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
      if (this.data.busy || !this.data.item || !api.requireLogin()) return;
      const f = this.data.form, item = this.data.item;
      if (!item.can_apply) { this.setData({ error: '仅教师可预约整间实验室，学生可以预约设备' }); return; }
      const message = fmt.validateForm(f, item.policy);
      if (message) { this.setData({ error: message }); return; }
      if (item.kind === 'ROOM' && (!/^\d+$/.test(f.participants) || Number(f.participants) < 1 || Number(f.participants) > item.capacity)) { this.setData({ error: '参与人数须为1至' + item.capacity + '之间的整数' }); return; }
      this.setData({ busy: true, error: '' });
      try {
        const booking = await api.submitBooking({ resource_id: item.id, starts_at: f.date + 'T' + f.start + ':00', ends_at: f.date + 'T' + f.end + ':00', purpose: f.purpose.trim(), participants: item.kind === 'ROOM' ? Number(f.participants) : 1 });
        wx.redirectTo({ url: '/pages/booking/index?id=' + booking.id });
        wx.showToast({ title: booking.status === 'CONFIRMED' ? '预约已确认' : '申请已提交', icon: 'success' });
      } catch (e) { error(this, e); } finally { this.setData({ busy: false }); }
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
      this.setData({ loading: true, error: '' });
      try {
        const result = await api.request('/bookings/', 'GET', Object.assign({},query,{page}));
        if (serial !== this._serial) return;
        this._appliedQuery=signature;
        this.setData({ items: (page === 1 ? [] : this.data.items).concat(result.items.map(fmt.booking)), page: result.page, hasMore: result.has_more, total: result.total, appliedFilter:!!(query.status || query.q.trim()) });
      } catch (e) { if (serial === this._serial) error(this, e); } finally { if (serial === this._serial) this.setData({ loading: false }); }
    },
    chooseStatus(e) { this.setData({ statusIndex: Number(e.currentTarget.dataset.index) }); return this.load(1); },
    search() { return this.load(1); },
    more() { if (this.data.hasMore && !this.data.loading) return this.load(this.data.page + 1); },
    onPullDownRefresh() { return this.load(1).finally(endRefresh); },
    onReachBottom() { return this.more(); }
  },
  booking: {
    data: { id: null, item: null, loading: false, busy: false, error: '', success:'',supplementUnknown:false, form: { reason: '',supplement:'' } },
    onLoad(options) { this.setData({ id: Number(options.id) }); },
    onShow() { if (api.requireLogin()) {if(ownerChanged(this))this.setData({item:null,form:{reason:'',supplement:''},success:'',supplementUnknown:false});this.restoreSupplement();return this.load();} },
    input,
    async load() {
      if(this._cancelPending || this._supplementPending)return;
      const serial=(this._detailSerial||0)+1;this._detailSerial=serial;
      this.setData({ loading: true, error: '' });
      try {
        const item = await api.request('/bookings/' + this.data.id + '/');
        if(serial!==this._detailSerial)return;
        item.timeline = item.timeline.map(t => Object.assign({}, t, { time_label: fmt.dateTime(t.at) }));
        this.setData({ item: fmt.booking(item) });
      } catch (e) { if(serial===this._detailSerial)error(this, e); } finally { if(serial===this._detailSerial)this.setData({ loading: false }); }
    },
    restoreSupplement() {
      const pending=api.actionDraft('supplement:'+this.data.id);
      this.setData({supplementUnknown:!!pending});
      if(pending)this.setData({'form.supplement':pending.values.reason});
    },
    async checkSupplement() {
      if(this.data.busy)return;
      this.setData({busy:true,error:'',success:''});
      try {
        const result=await api.checkAction('supplement:'+this.data.id);
        if(result.found){this.setData({'form.supplement':'',supplementUnknown:false,success:'已找到原补充说明的保存回执，无需再次提交。'});await this.load();}
        else this.setData({error:result.notice || '暂未找到回执，不能据此判断失败；请保持原内容重试。'});
      }catch(e){error(this,e);}finally{this.setData({busy:false});}
    },
    async supplement() {
      if(this.data.busy || !this.data.item || !api.requireLogin())return;
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
      if(api.requireLogin() && this.data.item && this.data.item.can_report)wx.navigateTo({url:'/pages/incident/index?resource='+this.data.item.resource_id+'&reservation='+this.data.id});
    },
    async cancel() {
      if (this.data.busy || !this.data.item) return;
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
    data: { items: [], loading: false, busy: false, error: '', unread: 0, unreadOnly: false, page: 1, hasMore: false },
    onShow() { if (api.requireLogin()) return this.load(1); },
    async load(page) {
      const serial=(this._serial||0)+1;this._serial=serial;
      this.setData({ loading: true, error: '' });
      try {
        const result = await api.request('/notifications/', 'GET', { page: page || 1, unread: this.data.unreadOnly ? '1' : '' });
        if(serial!==this._serial)return;
        this.setData({ items: (page && page > 1 ? this.data.items : []).concat(result.items.map(fmt.notify)), page: result.page, hasMore: result.has_more, unread: result.unread });
      } catch (e) { if(serial===this._serial)error(this, e); } finally { if(serial===this._serial)this.setData({ loading: false }); }
    },
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
  if (name === 'resources') Object.assign(data,{slot:fmt.defaultForm(),minDate:fmt.day(),maxDate:fmt.day(Date.now()+14*86400000)});
  if (name === 'resource') Object.assign(data, { form: fmt.defaultForm(), minDate: fmt.day(), maxDate: fmt.day(Date.now() + 14 * 86400000) });
  const onShow=definition.onShow;
  const defaults=JSON.parse(JSON.stringify(data));
  return Object.assign({}, definition, { data,onShow(){
    const saved=api.session(),scope=saved && saved.draftScope;
    if(this._displayOwner && this._displayOwner!==scope){
      const route={};for(const key of ['id','resourceId','reservationId'])if(Object.prototype.hasOwnProperty.call(this.data,key))route[key]=this.data[key];
      const fresh=JSON.parse(JSON.stringify(defaults));
      if(name==='resource')fresh.form=fmt.defaultForm();
      if(name==='resources')fresh.slot=fmt.defaultForm();
      if(name==='incident'){const now=nowFields();fresh.form={description:'',date:now.date,time:now.time};}
      this.setData(Object.assign(fresh,route));
      for(const key of ['_serial','_overviewSerial','_scheduleSerial','_detailSerial','_availabilitySerial','_slotsSerial'])this[key]=(this[key]||0)+1;
    }
    this._displayOwner=scope;
    this.setData({reducedEffects:wx.getStorageSync('campus-mini-simple-effects')===true});
    return onShow ? onShow.call(this) : undefined;
  } });
}
module.exports = { create };
