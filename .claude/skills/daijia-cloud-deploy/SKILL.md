---
name: daijia-cloud-deploy
description: 把乐尚代驾整套后端(6 个中间件 + 13 个微服务)部署到阿里云 SAE,开 NAT 出公网、网关经公网 CLB 暴露,两个小程序切到云上地址,做后端验收,并生成给业务人员的体验说明。也负责本地 IDEA 直连云上依赖。用户说"部署到云上""发布到 SAE""重新部署""更新云上的服务""出交付文档/使用说明""本地连云上依赖""换网络了连不上云上"时使用。启停用 daijia-cloud-power。
---

# 乐尚代驾部署到 SAE

全部由 agent 执行,用户不动手。部署前**不停下确认**(用户已授权直接部署,按量计费),测完用 `daijia-cloud-power` 关停。

入口只有一个脚本(下文简写 `dj`):

```bash
dj() { python -X utf8 .claude/skills/daijia-cloud-deploy/scripts/daijia.py "$@"; }
```

底层调用 `~/.claude/skills/sae-microservice-deploy/scripts/sae.py`(通用 SAE 工具,命令和坑见那个 skill)。部署配置都在本 skill 的 `config/` 里(与项目代码无关,不放进项目目录):`sae.config.json`(`cloud` 段是本 skill 专用的)和 MySQL/Redis/Mongo 的配置文件。脚本每次运行会生成 `.generated/sae.config.json`(已 gitignore)(带本次镜像标签和支付回调地址)给 `sae.py` 用。

## 部署

```bash
dj deploy          # 长耗时(首次 40 分钟以上),用后台运行,不要接管道
```

依次做:

1. NAT 网关 + EIP + SNAT(实例出公网:微信登录/支付、腾讯云 OCR/人脸/COS、腾讯地图)。
2. 构建镜像。全部放**一个私有仓库** `daijia`,微服务标签是 `<应用>-<源码哈希>`。哈希来自模块源码 + 公共模块(`common`/`model`/`service-client`/各级 pom)+ JDK 基础镜像;ACR 里已有同名标签就跳过,所以只有改过的模块会重建,公共模块改了就全部重建。`--force` 强制全部重建。JDK 用阿里云提供的 `openanolis/openjdk:17-8.6`,不推进 ACR。
3. `sync-config`:ConfigMap(各依赖的 K8s 服务名、Nacos 地址、证书路径)和 Secret(数据库/Redis/Mongo/RabbitMQ 密码、微信 appid/secret、微信支付、腾讯云密钥、两个支付证书文件),值都来自 `.env`。
4. 起中间件,建一个公网 CLB `daijia`:中间件端口套白名单(本机直连、推 Nacos),**网关 8600 对全网开放**(手机访问)。
5. 把 `daijia-parent/DEFAULT_GROUP/*.yaml` 推到云上 Nacos 并逐个回读核对。
6. 建/启动 13 个微服务;镜像或配置变了的重新部署。
7. 两个小程序的 `config/dev.js` 改成 `http://<CLB IP>:8600/...`。
8. 后端验收(见下)。

### 完成后告诉用户

- 小程序地址已切到云上:在开发者工具里**重新编译**两个项目,点「预览」,把二维码发给业务人员。
- 业务人员的微信号要在公众平台「成员管理」里加为项目成员,否则扫不开预览码。
- 文档:`dj doc` 生成 `docs/交付说明/使用说明.md`,只有文字,不放截图。
- 司机提交认证后状态停在"审核中":项目没有管理后台,`auth_status` 要由用户自己改成 2,文档里已写"联系管理员审核"。
- 正在计费的资源(`dj status`),测完说"关掉云上环境"即可。

## 验收标准(`dj verify`,不过不算部署完成)

1. 19 个应用都在运行。
2. 13 个服务在云上 Nacos 注册且健康。
3. 经 CLB 调乘客端、司机端登录接口(假 code)返回业务 JSON。
4. `service-customer` 日志里有微信返回的 `40029`(假 code 被拒),说明实例经 NAT 访问到了微信。

失败就 `sae.py logs <应用> --previous --grep ERROR` 查原因,修好后重跑 `dj deploy`(幂等,已完成的步骤会跳过)。同一问题失败超过 5 次停下来告诉用户。

## 其他命令

| 命令 | 作用 |
| --- | --- |
| `dj build [应用...] [--force]` | 只构建推送镜像 |
| `dj nacos-push [--host 127.0.0.1]` | 默认推云上 Nacos,`--host 127.0.0.1` 推本机 Nacos。改了 `DEFAULT_GROUP/*.yaml` 后推到云上 Nacos,随后 `sae.py redeploy <应用>` 生效 |
| `dj seed` | 重新生成 MySQL 初始化 SQL(只在 MySQL 数据目录为空时执行) |
| `dj mp cloud` / `dj mp lan` | 小程序地址指向云上 / 本机局域网 IP |
| `dj devlink up` | 本地 IDEA 直连云上依赖:起中间件 + CLB + 白名单当前出口 IP + 推配置 + 生成各模块 `bootstrap-dev.properties` |
| `dj devlink ip` | 换了网络(家/公司):白名单追加当前出口 IP |
| `dj devlink down` | 删除 `bootstrap-dev.properties`,本地服务恢复连本机 |

## 项目里的约定和坑

- **Nacos 配置只放占位符**(`${daijia.redis.host:localhost}` 等),SAE 里由 ConfigMap 环境变量填 K8s 服务名,本地由 `bootstrap-dev.properties` 填 CLB IP。不要推一份写死 IP 的配置。
- 支付回调地址是 `${WX_PAY_NOTIFY_URL:...}`,脚本按当前 CLB IP 填。微信支付 v3 回调要求 HTTPS,HTTP 回调大概率收不到;订单状态靠小程序付款后调 `queryPayStatus` 主动查单更新,不依赖回调。若下单时微信拒绝 HTTP 的 `notify_url`,停下来找用户。
- `bootstrap-dev.properties` 含 CLB IP 和密码:被 gitignore,打包时脚本会挪开再放回,保证不进 jar。
- 车辆照片、录音上传走腾讯云 COS(原 MinIO 已移除),云上不需要 MinIO。
- 支付证书从 `.env` 的 `WX_PAY_PRIVATE_KEY_PATH`/`WX_PAY_PUBLIC_KEY_PATH` 读取后存进 Secret,挂到 `/etc/daijia-secrets/`。
- 司机接单范围 5 公里;"到达起点"要在 1 公里内,"到达终点"要在 2 公里内,业务文档里已写。
- 应用默认**标准版**(`defaults.sae_version: std`)、**0.5C/1G**(全部 19 个);Java 堆 `-Xms256m -Xmx640m`,MySQL `innodb_buffer_pool_size=256M`。某个应用起不来(日志里 `OutOfMemoryError`、容器被 OOMKilled、启动超过存活探针时间反复重启)时,只给它在 `config/sae.config.json` 里单独调大:先 1C/2G 并把堆改成 `-Xmx1280m`,再跑 `dj deploy` 或 `dj up`(start 会发现规格不同,按新规格拉起)。调过的记在下面。
- 测试数据不清理。
