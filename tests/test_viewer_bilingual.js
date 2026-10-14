// 无浏览器/网络依赖：验证自动请求、双语保留、去重及原文变更隔离。
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const widgets = [];
const requests = [];
function element() {
  return {children: [], dataset: {}, textContent: '', className: '',
    classList: {toggle() {}}, append(...items) { this.children.push(...items); }};
}
const document = {
  createElement() { const e = element(); widgets.push(e); return e; },
  querySelectorAll() { return widgets.filter(e => e.dataset.translationKey); },
};
const context = vm.createContext({document, fetch: async (url, options) => {
  requests.push(JSON.parse(options.body));
  return {ok: true, json: async () => ({translation: '自动译文', source: 'MyMemory'})};
}});
const source = fs.readFileSync(path.join(__dirname, '../viewer/app.js'), 'utf8');
vm.runInContext(source.slice(0, source.indexOf('$("view-zh").addEventListener')), context);
context.local = element();
context.missing = element();
context.duplicate = element();
vm.runInContext(`
appendBilingual(local, '本地中文', 'Original English', 'test_en_001', 'card_text');
appendBilingual(missing, null, 'Missing English', 'test_en_001', 'card_text');
appendBilingual(duplicate, null, 'Missing English', 'test_en_001', 'card_text');
`, context);
assert.deepEqual(context.local.children.map(e => e.textContent), ['本地中文', 'Original English']);
assert.equal(requests.length, 1);
assert.equal(requests[0].expected_source, 'Missing English');
assert.equal(context.missing.children[1].textContent, 'Missing English');
setImmediate(() => {
  assert.equal(context.missing.children[0].textContent, '机器翻译 · 自动译文');
  assert.equal(context.duplicate.children[0].textContent, '机器翻译 · 自动译文');
  assert.equal(context.missing.children[1].textContent, 'Missing English');
  context.changed = element();
  vm.runInContext(`appendBilingual(changed, null, 'Changed English', 'test_en_001', 'card_text');`, context);
  assert.equal(requests.length, 2);
  assert.equal(requests[1].expected_source, 'Changed English');
  assert.equal(widgets.some(e => e.className.includes('translate-button')), false);
  console.log('viewer bilingual tests passed');
});
