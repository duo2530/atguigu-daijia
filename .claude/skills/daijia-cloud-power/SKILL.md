---
name: daijia-cloud-power
description: 乐尚代驾云上环境(阿里云 SAE)的关停和重新启动。down 停全部应用并删除 CLB、NAT、EIP,小程序改回本机局域网 IP;up 按顺序重新启动已部署的环境(不重新构建)。用户说"关掉云上环境""停掉 SAE 省钱""测完了关了吧""重新打开云上环境""启动云上服务""云上还在计费吗"时使用。首次部署或更新代码用 daijia-cloud-deploy。
---

# 乐尚代驾云上环境启停

由 agent 执行,不需要用户确认。脚本是 `daijia-cloud-deploy` 的同一个入口:

```bash
dj() { python -X utf8 .claude/skills/daijia-cloud-deploy/scripts/daijia.py "$@"; }
```

## 关停:`dj down`

1. 删除本地直连用的 `bootstrap-dev.properties`。
2. 解绑网关和中间件的公网 CLB,删除 CLB(白名单 ACL 不计费,保留)。
3. 按层倒序停掉全部 19 个应用。
4. 删除 NAT 网关、释放 EIP。
5. 两个小程序 `config/dev.js` 改回本机局域网 IP(调用 `mp-lan-ip-sync`)。
6. 打印剩余计费资源,应为"无"。

保留(不计费或很便宜,下次直接起):NAS 上的数据库数据、ACR 镜像、Nacos 配置(在 MySQL 里)、ConfigMap/Secret。

## 启动:`dj up`

建 NAT → 起中间件 → 建 CLB 并绑中间件端口(白名单)→ 按新 CLB IP 更新支付回调地址 → 按层起 13 个微服务 → 网关绑 8600 → 小程序地址切到云上 → 后端验收。长耗时,后台运行。

CLB 每次新建 IP 都会变,所以 up 之后:

- 告诉用户在开发者工具里**重新编译**两个项目再点「预览」,发新的二维码给业务人员(旧码指向旧 IP)。
- 本地直连要重新 `dj devlink up`。

## 查状态:`dj status`

应用运行情况、CLB、NAT、EIP,以及"仍在计费的"汇总。
