// 只读诊断:连已开启自动化端口的开发者工具,打印页面栈与最近的 console/exception。
// 用法: node diag.js [ws端口,默认9420]
const automator = require(process.env.AUTOMATOR_PATH || 'miniprogram-automator');
const port = process.argv[2] || '9420';
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
(async () => {
  let mp;
  for (let i = 0; i < 10 && !mp; i++) {
    try { mp = await automator.connect({ wsEndpoint: `ws://127.0.0.1:${port}` }); } catch (e) { await sleep(1000); }
  }
  if (!mp) { console.log('连接失败'); process.exit(1); }
  await sleep(3000);
  const stack = await mp.pageStack();
  console.log('页面栈:', stack.map((p) => p.path));
  const cur = await mp.currentPage();
  console.log('当前页:', cur && cur.path);
  mp.disconnect();
  process.exit(0);
})().catch((e) => { console.log('出错:', e.message); process.exit(1); });
