// 冒烟:连开发者工具(自动化端口),打开指定页面,记录 console/exception/请求结果并截图。
// 只读,不下单、不写数据。用法: node smoke.js <输出目录> [页面路径,默认 pages/index/index] [端口,默认9420]
const automator = require(process.env.AUTOMATOR_PATH || 'miniprogram-automator');
const fs = require('fs');
const path = require('path');
const out = process.argv[2];
const target = process.argv[3] || 'pages/index/index';
const port = process.argv[4] || '9420';
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
fs.mkdirSync(out, { recursive: true });

(async () => {
  let mp;
  for (let i = 0; i < 15 && !mp; i++) {
    try { mp = await automator.connect({ wsEndpoint: `ws://127.0.0.1:${port}` }); } catch (e) { await sleep(1000); }
  }
  if (!mp) { console.log('RESULT 连接失败'); process.exit(1); }
  const logs = [];
  mp.on('console', (m) => logs.push(`[console.${m.type}] ${String(m.args && m.args.map((a) => (typeof a === 'string' ? a : JSON.stringify(a))).join(' ')).slice(0, 240)}`));
  mp.on('exception', (e) => logs.push(`[exception] ${String(e.message || e).slice(0, 300)}`));
  await sleep(4000);
  // 记录页面发出的网络请求结果:在逻辑层包一层 wx.request
  await mp.evaluate(() => {
    if (wx.__wrapped) return;
    wx.__wrapped = true;
    wx.__reqs = [];
    const raw = wx.request;
    Object.defineProperty(wx, 'request', {
      configurable: true, writable: true,
      value: function (o) {
        const s = o.success, f = o.fail;
        o.success = function (r) { wx.__reqs.push({ url: o.url, status: r.statusCode, code: r.data && r.data.code, msg: r.data && r.data.message }); s && s.apply(this, arguments); };
        o.fail = function (e) { wx.__reqs.push({ url: o.url, fail: e && e.errMsg }); f && f.apply(this, arguments); };
        return raw.call(this, o);
      },
    });
  });
  await mp.reLaunch('/' + target);
  await sleep(6000);
  const page = await mp.currentPage();
  console.log('RESULT 当前页', page && page.path);
  const data = page && (await page.data());
  console.log('RESULT page.data 键', data ? Object.keys(data).slice(0, 12).join(',') : '无');
  const reqs = await mp.evaluate(() => wx.__reqs || []);
  console.log('RESULT 请求数', reqs.length);
  reqs.slice(0, 15).forEach((r) => console.log('RESULT req', JSON.stringify(r)));
  const shot = path.join(out, target.replace(/[\/]/g, '_') + '.png');
  await mp.screenshot({ path: shot });
  console.log('RESULT 截图', shot);
  logs.slice(0, 25).forEach((l) => console.log('RESULT log', l));
  console.log('RESULT 日志条数', logs.length);
  mp.disconnect();
  process.exit(0);
})().catch((e) => { console.log('RESULT 出错', e.message); process.exit(1); });
