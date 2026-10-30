"use strict";
const dev = require("./dev.js");
const test = require("./test.js");
const prod = require("./prod.js");
const envMap = { develop: dev, trial: test, release: prod };
let envVersion = "develop";
try {
  envVersion = wx.getAccountInfoSync().miniProgram.envVersion;
} catch (e) {
}
const env = envMap[envVersion] || dev;
exports.baseUrl = env.baseUrl;
