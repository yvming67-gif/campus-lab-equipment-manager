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
    data: { id: null, item: null, form: fmt.defaultForm(), loading: false, busy: false, error: '', availability: '', available: null, minDate: fmt.day(), maxDate: fmt.day(Date.now() + 14 * 86400000), scheduleDay: '', schedulePage: 1, scheduleTotal: 0, scheduleHasMore: false, scheduleBusy: false, scheduleError: '' },
    onLoad(options) {
      this.setData({ id: Number(options.id) });
      if (/^\d{4}-\d{2}-\d{2}$/.test(options.date || '') && [options.start,options.end].every(t => /^\d{2}:\d{2}$/.test(t || ''))) this.setData({'form.date':options.date,'form.start':options.start,'form.end':options.end,scheduleDay:options.date});
      const pending = api.draft(this.data.id);
      if (pending && pending.values.resource_id === this.data.id) {
        const value = pending.values;
        this.setData({ form: { date: value.starts_at.slice(0, 10), start: value.starts_at.slice(11, 16), end: value.ends_at.slice(11, 16), purpose: value.purpose, participants: String(value.participants) } });
      }
    },
    onShow() { if (api.requireLogin()) { this.setData({minDate:fmt.day()}); return this.load(); } },
    async load() {
      const serial=(this._scheduleSerial||0)+1;this._scheduleSerial=serial;
      invalidateAvailability(this);
      this.setData({ loading: true, scheduleBusy: false, error: '' });
      try {
        const item = await api.request('/resources/' + this.data.id + '/', 'GET', {schedule_day:this.data.scheduleDay,schedule_page:1});
        if(serial!==this._scheduleSerial)return;
        this.setData({ item: fmt.resource(item), maxDate: fmt.day(Date.now() + item.policy.window_days * 86400000), schedulePage:item.schedule_page, scheduleTotal:item.schedule_total, scheduleHasMore:item.schedule_has_more, scheduleError:'' });
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
    chooseDate(e) { this.setData({ 'form.date': e.detail.value, availability: '', available: null, loading:invalidateAvailability(this) }); },
    chooseStart(e) { this.setData({ 'form.start': e.detail.value, availability: '', available: null, loading:invalidateAvailability(this) }); },
    chooseEnd(e) { this.setData({ 'form.end': e.detail.value, availability: '', available: null, loading:invalidateAvailability(this) }); },
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
    data: { id: null, item: null, loading: false, busy: false, error: '', form: { reason: '' } },
    onLoad(options) { this.setData({ id: Number(options.id) }); },
    onShow() { if (api.requireLogin()) return this.load(); },
    input,
    async load() {
      if(this._cancelPending)return;
      const serial=(this._detailSerial||0)+1;this._detailSerial=serial;
      this.setData({ loading: true, error: '' });
      try {
        const item = await api.request('/bookings/' + this.data.id + '/');
        if(serial!==this._detailSerial)return;
        item.timeline = item.timeline.map(t => Object.assign({}, t, { time_label: fmt.dateTime(t.at) }));
        this.setData({ item: fmt.booking(item) });
      } catch (e) { if(serial===this._detailSerial)error(this, e); } finally { if(serial===this._detailSerial)this.setData({ loading: false }); }
    },
    async cancel() {
      if (this.data.busy || !this.data.item) return;
      const reason = this.data.form.reason.trim();
      if (!reason) { this.setData({ error: '请填写取消原因' }); return; }
      if (!await confirm({ title: '取消这项预约？', content: '确认后会释放尚未使用的时段。', confirmText: '确认取消', confirmColor: '#9b5b43' })) return;
      if(this.data.busy)return;
      this._detailSerial=(this._detailSerial||0)+1;this._cancelPending=true;
      this.setData({ busy: true, loading:false, error: '' });
      try {
        const item=await api.request('/bookings/' + this.data.id + '/cancel/', 'POST', { reason, version: this.data.item.version });
        if(item && item.id){item.timeline=(item.timeline||[]).map(t=>Object.assign({},t,{time_label:fmt.dateTime(t.at)}));this.setData({item:fmt.booking(item)});}
        this._cancelPending=false;
        await this.load(); wx.showToast({ title: '预约已取消', icon: 'success' });
      } catch (e) { error(this, e); } finally { this._cancelPending=false;this.setData({ busy: false }); }
    },
    onPullDownRefresh() { return this.load().finally(endRefresh); }
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
    async signout() {
      if (this.data.busy || !await confirm({ title: '退出当前账号？', content: '已有预约会保留，登录后可继续查询。', confirmText: '退出' })) return;
      this.setData({ busy: true, error: '' });
      try { await api.logout(); wx.reLaunch({ url: '/pages/login/index' }); }
      catch (e) { wx.reLaunch({ url: '/pages/login/index' }); wx.showToast({title:'已本地退出，远端会话将到期',icon:'none'}); } finally { this.setData({ busy: false }); }
    }
  }
};
function create(name) {
  const definition = screens[name];
  if (!definition) throw new Error('Unknown page: ' + name);
  const data = JSON.parse(JSON.stringify(definition.data));
  if (name === 'resources') Object.assign(data,{slot:fmt.defaultForm(),minDate:fmt.day(),maxDate:fmt.day(Date.now()+14*86400000)});
  if (name === 'resource') Object.assign(data, { form: fmt.defaultForm(), minDate: fmt.day(), maxDate: fmt.day(Date.now() + 14 * 86400000) });
  return Object.assign({}, definition, { data });
}
module.exports = { create };
