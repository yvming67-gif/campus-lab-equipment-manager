const api = require('./utils/api');
const fmt = require('./utils/format');
const statuses = [{ code: '', name: '全部' }, { code: 'PENDING', name: '待审核' }, { code: 'CONFIRMED', name: '已确认' }, { code: 'IN_USE', name: '使用中' }, { code: 'COMPLETED', name: '已完成' }, { code: 'CANCELLED', name: '已取消' }, { code: 'REJECTED', name: '已驳回' }, { code: 'EXPIRED', name: '已失效' }];
function error(page, e) { page.setData({ error: api.errorText(e) }); }
function input(event) { this.setData({ ['form.' + event.currentTarget.dataset.field]: event.detail.value, error: '' }); }
function openResource(event) { wx.navigateTo({ url: '/pages/resource/index?id=' + event.currentTarget.dataset.id }); }
function openBooking(event) { wx.navigateTo({ url: '/pages/booking/index?id=' + event.currentTarget.dataset.id }); }
function endRefresh() { if (wx.stopPullDownRefresh) wx.stopPullDownRefresh(); }
function confirm(options) { return new Promise(resolve => wx.showModal(Object.assign({}, options, { success: r => resolve(r.confirm), fail: () => resolve(false) }))); }

const screens = {
  login: {
    data: { accounts: [{ username: 'student', name: '学生 · 演示账号01' }, { username: 'student2', name: '学生 · 演示账号02' }, { username: 'teacher', name: '教师 · 演示账号' }], accountIndex: 0, form: { password: 'Demo@2026', base: '' }, busy: false, error: '' },
    onLoad() { this.setData({ 'form.base': api.baseUrl() }); },
    onShow() { if (api.session()) wx.switchTab({ url: '/pages/resources/index' }); },
    input,
    chooseAccount(e) { this.setData({ accountIndex: Number(e.detail.value), error: '' }); },
    async submit() {
      if (this.data.busy) return;
      this.setData({ busy: true, error: '' });
      try { api.setBase(this.data.form.base); await api.login(this.data.accounts[this.data.accountIndex].username, this.data.form.password); wx.switchTab({ url: '/pages/resources/index' }); }
      catch (e) { error(this, e); } finally { this.setData({ busy: false }); }
    }
  },
  resources: {
    data: { items: [], labs: [{ id: '', name: '全部实验室' }], labIndex: 0, kind: 'EQUIPMENT', form: { q: '' }, loading: false, error: '', hasMore: false, page: 1, total: 0, user: null, stats: { pending: 0, confirmed: 0, using: 0 } },
    async onShow() {
      if (!api.requireLogin()) return;
      this.setData({ user: api.session().user });
      try { const data = await api.request('/me/'); this.setData({ user: data.user, stats: data.stats, overdue: data.overdue }); } catch (e) { error(this, e); }
      return this.load(1);
    },
    input, openResource,
    async load(page) {
      if (!api.requireLogin()) return;
      page = page || 1;
      const serial = (this._serial || 0) + 1; this._serial = serial;
      this.setData({ loading: true, error: '' });
      try {
        const data = await api.request('/resources/', 'GET', { kind: this.data.kind, q: this.data.form.q, lab: this.data.labs[this.data.labIndex].id, page });
        if (serial !== this._serial) return;
        this.setData({ items: (page === 1 ? [] : this.data.items).concat(data.items.map(fmt.resource)), labs: [{ id: '', name: '全部实验室' }].concat(data.labs), page: data.page, hasMore: data.has_more, total: data.total });
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
      const pending = api.draft();
      if (pending && pending.values.resource_id === this.data.id) {
        const value = pending.values;
        this.setData({ form: { date: value.starts_at.slice(0, 10), start: value.starts_at.slice(11, 16), end: value.ends_at.slice(11, 16), purpose: value.purpose, participants: String(value.participants) } });
      }
    },
    onShow() { if (api.requireLogin()) return this.load(); },
    async load() {
      const serial=(this._scheduleSerial||0)+1;this._scheduleSerial=serial;
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
    chooseDate(e) { this._availabilitySerial=(this._availabilitySerial||0)+1; this.setData({ 'form.date': e.detail.value, availability: '', available: null }); },
    chooseStart(e) { this._availabilitySerial=(this._availabilitySerial||0)+1; this.setData({ 'form.start': e.detail.value, availability: '', available: null }); },
    chooseEnd(e) { this._availabilitySerial=(this._availabilitySerial||0)+1; this.setData({ 'form.end': e.detail.value, availability: '', available: null }); },
    async checkAvailability() {
      const f = Object.assign({},this.data.form);
      const serial=(this._availabilitySerial||0)+1;this._availabilitySerial=serial;
      this.setData({ loading: true, error: '' });
      try {
        const item = await api.request('/resources/' + this.data.id + '/', 'GET', { starts_at: f.date + 'T' + f.start + ':00', ends_at: f.date + 'T' + f.end + ':00' });
        if(serial===this._availabilitySerial)this.setData({ availability: item.available_label, available: item.available });
      } catch (e) { error(this, e); } finally { this.setData({ loading: false }); }
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
    data: { items: [], statuses, statusIndex: 0, form: { q: '' }, loading: false, error: '', page: 1, hasMore: false, total: 0 },
    onShow() { if (api.requireLogin()) return this.load(1); },
    input, openBooking,
    async load(page) {
      page = page || 1;
      const serial = (this._serial || 0) + 1; this._serial = serial;
      this.setData({ loading: true, error: '' });
      try {
        const result = await api.request('/bookings/', 'GET', { status: statuses[this.data.statusIndex].code, q: this.data.form.q, page });
        if (serial !== this._serial) return;
        this.setData({ items: (page === 1 ? [] : this.data.items).concat(result.items.map(fmt.booking)), page: result.page, hasMore: result.has_more, total: result.total });
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
      this.setData({ loading: true, error: '' });
      try {
        const item = await api.request('/bookings/' + this.data.id + '/');
        item.timeline = item.timeline.map(t => Object.assign({}, t, { time_label: fmt.dateTime(t.at) }));
        this.setData({ item: fmt.booking(item) });
      } catch (e) { error(this, e); } finally { this.setData({ loading: false }); }
    },
    async cancel() {
      if (this.data.busy || !this.data.item) return;
      const reason = this.data.form.reason.trim();
      if (!reason) { this.setData({ error: '请填写取消原因' }); return; }
      if (!await confirm({ title: '取消这项预约？', content: '确认后会释放尚未使用的时段。', confirmText: '确认取消', confirmColor: '#9b5b43' })) return;
      this.setData({ busy: true, error: '' });
      try {
        await api.request('/bookings/' + this.data.id + '/cancel/', 'POST', { reason, version: this.data.item.version });
        await this.load(); wx.showToast({ title: '预约已取消', icon: 'success' });
      } catch (e) { error(this, e); } finally { this.setData({ busy: false }); }
    },
    onPullDownRefresh() { return this.load().finally(endRefresh); }
  },
  notifications: {
    data: { items: [], loading: false, busy: false, error: '', unread: 0, page: 1, hasMore: false },
    onShow() { if (api.requireLogin()) return this.load(1); },
    async load(page) {
      const serial=(this._serial||0)+1;this._serial=serial;
      this.setData({ loading: true, error: '' });
      try {
        const result = await api.request('/notifications/', 'GET', { page: page || 1 });
        if(serial!==this._serial)return;
        this.setData({ items: (page && page > 1 ? this.data.items : []).concat(result.items.map(fmt.notify)), page: result.page, hasMore: result.has_more, unread: result.unread });
      } catch (e) { if(serial===this._serial)error(this, e); } finally { if(serial===this._serial)this.setData({ loading: false }); }
    },
    async open(e) {
      const id = Number(e.currentTarget.dataset.id);const item = this.data.items.find(x => x.id === id);
      if (!item || this.data.busy) return;
      this.setData({ busy: true, error: '' });
      try {
        this._serial=(this._serial||0)+1;
        if (item.unread) await api.request('/notifications/' + id + '/read/', 'POST', {});
        this.setData({ items: this.data.items.map(x => x.id === id ? Object.assign({}, x, { unread: false, read_at: new Date().toISOString() }) : x), unread: Math.max(0, this.data.unread - (item.unread ? 1 : 0)) });
        if (item.booking_id) wx.navigateTo({ url: '/pages/booking/index?id=' + item.booking_id });
      } catch (e2) { error(this, e2); } finally { this.setData({ busy: false }); }
    },
    async markAll() {
      if (this.data.busy) return;
      this.setData({ busy: true, error: '' });
      this._serial=(this._serial||0)+1;
      try { await api.request('/notifications/read-all/', 'POST', {}); await this.load(1); }
      catch (e) { error(this, e); } finally { this.setData({ busy: false }); }
    },
    more() { if (this.data.hasMore && !this.data.loading) return this.load(this.data.page + 1); },
    onReachBottom() { return this.more(); },
    onPullDownRefresh() { return this.load(1).finally(endRefresh); }
  },
  profile: {
    data: { user: null, stats: null, policy: null, loading: false, error: '', base: '', busy: false, roleLabel: '' },
    onShow() { if (api.requireLogin()) return this.load(); },
    async load() {
      this.setData({ loading: true, error: '' });
      try { const data = await api.request('/me/'); this.setData({ user: data.user, stats: data.stats, policy: data.policy, overdue: data.overdue, roleLabel: data.user.roles.map(x => x.name).join(' / '), base: api.baseUrl() }); }
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
  if (name === 'resource') Object.assign(data, { form: fmt.defaultForm(), minDate: fmt.day(), maxDate: fmt.day(Date.now() + 14 * 86400000) });
  return Object.assign({}, definition, { data });
}
module.exports = { create };
