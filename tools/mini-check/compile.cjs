/* Optional authoring check. The application and mini client need no npm install. */
const fs = require('node:fs');
const path = require('node:path');
const compiler = require('miniprogram-compiler');
const root = path.resolve(__dirname, '../../miniprogram');
const config = JSON.parse(fs.readFileSync(path.join(root, 'app.json'), 'utf8'));
const wxml = compiler.wxmlToJs(root);
const wxss = compiler.wxssToJs(root);
global.window = {};
const gwx = new Function('global', wxml)({});
for (const page of config.pages) {
  const render = gwx(page + '.wxml');
  if (typeof render !== 'function') throw new Error('Missing native template ' + page);
}
if (!wxss.length) throw new Error('Empty native stylesheet compilation');
console.log(JSON.stringify({ compiler: 'miniprogram-compiler@0.2.3', native_pages: config.pages.length, wxml: 'pass', wxss: 'pass', scope: 'official wcc/wcsc syntax validation; not a WeChat device runtime' }));
