// 在开发者工具里走真实登录:wx.login 取真实 code -> 调登录接口 -> 把 token 写进小程序 storage。
// 用法: node login.js <登录URL前缀,code 会拼在末尾> [端口,默认9420] [storage键,默认token]
// 例:   node login.js http://127.0.0.1:8600/customer-api/customer/login/
// 响应约定 {code,message,data:<token>};不同项目格式不同就改 pick()。
const automator = require(process.env.AUTOMATOR_PATH || 'miniprogram-automator');
const prefix = process.argv[2];
const port = process.argv[3] || '9420';
const key = process.argv[4] || 'token';
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const pick = (body) => body && body.data;
if (!prefix) { console.log('用法: node login.js <登录URL前缀> [端口] [storage键]'); process.exit(2); }
(async () => {
  let mp;
  for (let i = 0; i < 15 && !mp; i++) { try { mp = await automator.connect({ wsEndpoint: `ws://127.0.0.1:${port}` }); } catch (e) { await sleep(1000); } }
  if (!mp) { console.log('RESULT 连接失败'); process.exit(1); }
  await sleep(4000);
  // evaluate 里嵌套 wx.request 会超时:只在这里取 code,HTTP 放到 Node 端发
  const code = await mp.evaluate(() => new Promise((res) => wx.login({ success: (l) => res(l.code), fail: (e) => res('ERR:' + e.errMsg) })));
  if (!code || String(code).startsWith('ERR:')) { console.log('RESULT wx.login 失败', code); process.exit(1); }
  const res = await fetch(prefix + code);
  const body = await res.json();
  const token = pick(body);
  console.log('RESULT 登录', JSON.stringify({ codeLen: String(code).length, status: res.status, code: body.code, msg: body.message, tokenLen: token ? String(token).length : 0 }));
  if (token) { await mp.evaluate((k, t) => { wx.setStorageSync(k, t); return 1; }, key, token); console.log('RESULT token 已写入 storage'); }
  mp.disconnect(); process.exit(0);
})().catch((e) => { console.log('RESULT 出错', e.message); process.exit(1); });
