const tones = { PENDING: 'amber', CONFIRMED: 'green', IN_USE: 'blue', COMPLETED: 'muted', CANCELLED: 'muted', REJECTED: 'red', EXPIRED: 'muted' };
function day(value) { const d = new Date((value == null ? Date.now() : value) + 8 * 3600000); return d.toISOString().slice(0, 10); }
function dateTime(value) { return value ? value.slice(0, 16).replace('T', ' ') : ''; }
function booking(item) { return Object.assign({}, item, { tone: tones[item.status] || 'muted', time_label: dateTime(item.starts_at) + ' — ' + String(item.ends_at || '').slice(11, 16), kind_label: item.kind === 'ROOM' ? '场地' : '设备' }); }
function resource(item) { return Object.assign({}, item, { schedule: (item.schedule || []).map(x => Object.assign({}, x, { time_label: dateTime(x.starts_at) + " — " + x.ends_at.slice(11, 16) })), mode_label: item.requires_approval ? '人工审核' : '自动确认', category_label: item.kind === 'ROOM' ? '整间场地 · ' + item.capacity + '人' : item.category, availability_tone: item.available === true ? 'green' : (item.available === false ? 'red' : 'muted'), tone: item.service_status === 'NORMAL' ? 'green' : 'red' }); }
function notify(item) { return Object.assign({}, item, { time_label: dateTime(item.created_at), unread: !item.read_at }); }
function defaultForm() { return { date: day(Date.now() + 86400000), start: '10:00', end: '11:00', purpose: '', participants: '1' }; }
function validateForm(form, policy) {
  if (!form.purpose.trim()) return '请填写本次使用目的';
  if (form.purpose.trim().length > 1000) return '使用目的不能超过1000字';
  const a = new Date(form.date + 'T' + form.start + ':00+08:00').getTime();
  const b = new Date(form.date + 'T' + form.end + ':00+08:00').getTime();
  if (!Number.isFinite(a) || !Number.isFinite(b) || a >= b) return '结束时间必须晚于开始时间';
  if (a < Date.now()) return '请选择尚未开始的预约时段';
  if (b - a > policy.max_hours * 3600000) return '单次使用不能超过' + policy.max_hours + '小时';
  if ([form.start, form.end].some(t => Number(t.slice(3)) % policy.slot_minutes !== 0)) return '起止时间须以' + policy.slot_minutes + '分钟为单位';
  return '';
}
module.exports = { day, dateTime, booking, resource, notify, defaultForm, validateForm };
