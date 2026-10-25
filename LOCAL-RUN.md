# 本地运行手册（atguigu-daijia / 乐尚代驾）

> 目标：在一台 Windows 机器上把 **14 个 Spring Boot 进程 + 6 个依赖容器**跑起来，
> 并跑通「乘客登录 → 预估 → 下单 → 派单 → 司机抢单 → 查订单」链路。
> 本文所有命令均在仓库根目录 `D:\Projects\IdeaProjects\atguigu-daijia` 下执行。

---

## 0. 当前状态（已实测通过）

| 项 | 结果 |
|---|---|
| 依赖容器 | 6 个全部 `Up (healthy)` |
| 后端进程 | 14 个全部 `Started ... Application`，Nacos 服务列表 `count=14` |
| 冒烟测试 | `scripts\smoke-test.ps1` **8/8 通过** |
| 派单链路 | 司机上报位置 → 乘客下单 → XXL-JOB 任务立即触发 → 司机端 `findNewOrderQueueData` 拉到该订单 |

---

## 1. 环境要求

| 组件 | 版本/路径 | 说明 |
|---|---|---|
| JDK | 17 —— `D:\Develop\Java\graalvm-ce-java17-22.3.0` | 项目要求 Java 17，**不要**用系统默认的 JDK 8 |
| Maven | 3.9.9 —— `D:\Develop\apache-maven-3.9.9` | 系统自带的 3.3.9 无法构建 Spring Boot 3 |
| Docker | Docker Desktop（WSL2 后端） | 依赖服务全部用 docker-compose 启动 |
| 内存 | 建议 ≥ 20GB | 容器约 2.3GB + 14 个 JVM 堆上限合计 4.0GB |

首次构建（已做过，target 存在可跳过）：

```powershell
$env:JAVA_HOME='D:\Develop\Java\graalvm-ce-java17-22.3.0'
& 'D:\Develop\apache-maven-3.9.9\bin\mvn.cmd' -B -DskipTests install   # workdir=daijia-parent
```

产物：14 个可执行 jar，位于 `daijia-parent\{server-gateway,web\*,service\*}\target\<artifactId>.jar`。

---

## 2. 一键启动（5 步）

```powershell
# 1) 启动 6 个依赖容器（MySQL/Redis/RabbitMQ/MongoDB/Nacos/XXL-JOB admin）
docker compose -f infra\docker-compose.yml up -d
docker compose -f infra\docker-compose.yml ps        # 等 6 个都 healthy

# 2) 把 infra\..\daijia-parent\DEFAULT_GROUP 里的 15 个配置推送到本地 Nacos
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\nacos-import.ps1

# 3) 导入本地测试数据（1 个乘客 + 1 个司机 + 司机设置/账户）
Get-Content scripts\test-data.sql -Raw | docker exec -i daijia-mysql mysql -uroot -p'shangguigu@QY123'

# 4) 启动 14 个 Spring Boot 进程（带 -Xmx 限制）
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\start-all.ps1

# 5) 冒烟测试（等 60~90 秒服务注册完成后执行）
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\smoke-test.ps1
```

停止：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\stop-all.ps1
docker compose -f infra\docker-compose.yml stop      # 保留数据；要清库用 down -v
```

日志：`logs\<模块>.log`（业务日志）与 `logs\<模块>.err.log`（异常栈，GlobalExceptionHandler 会 `printStackTrace` 到这里）。

---

## 3. 端口与账号

| 服务 | 端口 | 账号/说明 |
|---|---|---|
| server-gateway | 8600 | 统一入口，路由 `/customer-api/**` `/driver-api/**` `/mgr-api/**` `/payment/**` |
| web-customer / web-driver / web-mgr | 8601 / 8602 / 8603 | 面向前端的聚合层 |
| service-customer / driver / map / rules / order / payment | 8501 / 8502 / 8503 / 8504 / 8505 / 8506 | |
| service-dispatch / system / coupon / mq | 8509 / 8510 / 8511 / 8282 | |
| MySQL | 3306 | root / `shangguigu@QY123`（8 个 daijia_* 库 + xxl_job） |
| Redis | 6379 | 无密码 |
| RabbitMQ | 5672 / 15672 | admin / admin123，已启用延迟消息插件 |
| MongoDB | 27017 | 无认证，库 `daijia` |
| Nacos | 8848 / 9848 | standalone，无认证（9848 是 2.x 的 gRPC 端口，必须映射） |
| XXL-JOB admin | 8080 | admin / 123456，accessToken `default_token` |
| Knife4j 文档 | 各服务 `<host:port>/doc.html` | 如 http://127.0.0.1:8505/doc.html |

---

## 4. 堆内存限制（按要求配置）

### 4.1 容器（infra/docker-compose.yml）

每个容器都加了 `mem_limit`，Java 进程固定在容器内：

| 容器 | mem_limit | JVM 堆参数 |
|---|---|---|
| mysql | 768m | — |
| redis | 256m | `--maxmemory 256mb --maxmemory-policy noeviction` |
| rabbitmq | 512m | — |
| mongo | 512m | `--wiredTigerCacheSizeGB 0.25` |
| nacos | 800m | `JVM_XMS=384m / JVM_XMX=384m / JVM_XMN=128m` |
| xxl-job-admin | 512m | `JAVA_TOOL_OPTIONS=-Xms192m -Xmx192m -XX:MaxMetaspaceSize=128m` |

> 坑：xxl-job 镜像入口是 `java -jar $JAVA_OPTS /app.jar`，`-Xmx` 放 `JAVA_OPTS` 会跑到 `-jar` 后面导致启动失败，
> 必须用 `JAVA_TOOL_OPTIONS`。

### 4.2 后端 14 个 JVM（scripts/start-all.ps1 的 `$heapTable`）

| 模块 | -Xmx | 模块 | -Xmx |
|---|---|---|---|
| service-system | 320m | service-payment | 320m |
| service-customer | 256m | service-dispatch | 320m |
| service-driver | 320m | service-mq | 192m |
| service-map | 256m | web-customer | 256m |
| service-rules | 384m | web-driver | 320m |
| service-coupon | 256m | web-mgr | 256m |
| service-order | 384m | server-gateway | 192m |

统一附加：`-Xms64m`、`-XX:MaxMetaspaceSize=160m`、`-XX:ReservedCodeCacheSize=64m`、`-Xss512k`、`-XX:+ExitOnOutOfMemoryError`、`-Dfile.encoding=UTF-8`、`-Duser.timezone=Asia/Shanghai`。
调参：`start-all.ps1 -HeapMb 512` 可改未单独配置模块的默认值，或直接改 `$heapTable`。

---

## 5. 为本地运行做的改动（git diff 可查看，26 个文件 + 3 个新目录）

### 5.1 14 个 `bootstrap.properties`
`spring.cloud.nacos.{discovery,config}.server-addr`：`192.168.6.129:8848` → `127.0.0.1:8848`。

### 5.2 15 个 `daijia-parent/DEFAULT_GROUP/*.yaml`
| 改动 | 原因 |
|---|---|
| MySQL `139.198.104.58` → `127.0.0.1`；`com.mysql.jdbc.Driver` → `com.mysql.cj.jdbc.Driver` | 原库在阿里云，已销毁 |
| RabbitMQ `139.198.163.91`/`139.198.152.148` → `127.0.0.1`，guest/guest → admin/admin123 | 容器外不能用 guest |
| MongoDB `39.98.123.211` → `127.0.0.1` | |
| MinIO `139.198.127.41` → `127.0.0.1` | 暂未启动 MinIO，仅影响文件上传 |
| `common-account.yaml`：`privateKeyPath` 指向仓库内 `apiclient_key.pem`；`notifyUrl` → `127.0.0.1:8600`；`tencent.map.key` 换成可用 key | 原路径不存在会导致 service-payment 启动失败；地图 key 已实测可用 |
| `service-system-dev.yaml`：库名 `tingshu_system` → `daijia_system`，`useSSL=false`，删掉 Sentinel dashboard（占用 8080 与 XXL-JOB 冲突） | 原配置抄自另一课程，库不存在 |
| `service-dispatch-dev.yaml`：xxl 执行器日志目录 `/data/applogs` → `D:/logs/xxl-job/jobhandler` | Windows 无此路径 |

### 5.3 新增目录
- `infra/`：`docker-compose.yml`、README、`artifacts/xxl-job-admin.jar`（见 5.4）、`scripts/verify.ps1` 等
- `scripts/`：`nacos-import.ps1`、`start-all.ps1`、`stop-all.ps1`、`smoke-test.ps1`、`test-data.sql`
- `logs/`：运行日志（已加入根 `.gitignore`）

回滚配置：`git checkout -- daijia-parent`（恢复到课程原始远程地址）。

### 5.4 特别说明：XXL-JOB admin 镜像版本坑
Docker Hub 上 `xuxueli/xxl-job-admin:2.3.0` 镜像里的 admin 其实是旧版本，**没有** `/jobinfo/addAndStartJob`、`/jobinfo/startJob` 等接口（项目 dispatch 依赖这些），会 302 到登录页导致 `NullPointerException`。
解决办法：用仓库自带的 `xxl-job-master`（2.3.0 源码）构建 admin jar，挂载覆盖容器里的 `/app.jar`：

```powershell
cd xxl-job-master
$env:JAVA_HOME='D:\Develop\Java\jdk1.8.0_251'
& 'D:\Develop\apache-maven-3.9.9\bin\mvn.cmd' -B -DskipTests -pl xxl-job-admin -am package
Copy-Item xxl-job-admin\target\xxl-job-admin-2.3.0.jar ..\infra\artifacts\xxl-job-admin.jar
cd ..
docker compose -f infra\docker-compose.yml up -d xxl-job-admin
```

---

## 6. 验证与演示技巧

### 6.1 冒烟测试做了什么
1. 往 Redis 伪造登录态（**关键**：项目的 value 用 `GenericJackson2JsonRedisSerializer`，userId 必须存成 JSON 字符串 `"1"`，存成 `1` 会 `ClassCastException`）
2. 网关 + `@GuiguLogin` 鉴权：`/customer-api/customer/getCustomerLoginInfo`
3. 预估：`/customer-api/order/expectOrder`（腾讯地图路线 + Drools 计费 → 19.00 元）
4. 下单：`/customer-api/order/submitOrder`（落库 + Redisson 延迟队列 + dispatch 建 XXL-JOB 任务）
5. 抢单：`/driver-api/order/robNewOrder/{orderId}`（Redisson 分布式锁）→ 状态 1 → 2

### 6.2 为什么需要"伪造登录态"
真正登录要调 `api.weixin.qq.com/jscode2session`，而仓库里的 appId/secret 是课程共享的（且需要小程序端拿 code），本地无法走通。
所以测试用「直接往 Redis 写 token→userId」的方式绕过微信登录，这也是验证后端逻辑最省事的办法。
前端小程序可照旧把 `mp-weixin-customer` / `mp-weixin-driver` 导入微信开发者工具，但需把 `http/index.js` 里的 `http://localhost:8600` 换成可达地址，并且仍无法真正登录。

### 6.3 手工演示"派单给附近司机"
```powershell
# 司机上报位置（写入 Redis GEO）
POST http://127.0.0.1:8600/driver-api/location/updateDriverLocation   header: token=smoke-driver-token
{"driverId":1,"longitude":116.30749,"latitude":39.984154}
# 乘客下单后 1~5 秒，司机端拉新订单队列
GET  http://127.0.0.1:8600/driver-api/order/findNewOrderQueueData     header: token=smoke-driver-token
```

---

## 7. 已知限制（与本地环境无关的项目自身问题）

1. **微信相关能力不可用**：小程序登录、微信支付/分账、腾讯云 COS/人脸核身/OCR 都需要有效商户资质与密钥；`service-payment` 能启动是因为只校验了 pem 文件格式。
2. **MinIO 未启动**：`/driver-api/file/upload`（司机认证图片）会失败；需要时在 compose 里加 `minio/minio` 服务。
3. **Seata 未启用**：`@GlobalTransactional` 与相关依赖在源码里被注释，无分布式事务。
4. **管理端只有后端**：`web-mgr` 可跑（`/mgr-api/**`），但没有管理端前端工程。
5. **`service-mq` 是空壳**（教学样例），不影响主链路。
6. **司机后台定位未实现**：小程序只注册了权限，代码里是 5s 轮询 `getLocation`，切后台会断。
7. 教学演示代码：支付金额写死 1 分钱、实际里程"预估+5km"，真实使用需改。

---

## 8. 排错速查

| 现象 | 原因/处理 |
|---|---|
| 启动即报 `Could not resolve placeholder` / 连不上 Nacos | 没导入配置或 Nacos 未就绪；重跑 `scripts\nacos-import.ps1` |
| 接口返回 `code=208 未登陆` | 请求头没有 `token` 或 Redis 里没有 `user:login:<token>` |
| 接口返回 `code=201 失败` 且 err 日志有 `ClassCastException: Integer cannot be cast to String` | Redis 里 token 的值写成了 `1`，必须是 `"1"`（JSON 字符串） |
| 下单成功但司机收不到订单 | ①司机没上报位置（Redis GEO 为空）→ 派单搜不到人；②xxl admin 版本不对（见 5.4）；③`driver_set.accept_distance` 太小 |
| `xxl-job register jobhandler` 后任务不跑 | 执行器注册地址需能从 admin 容器访问（本项目实测为宿主局域网 IP，可用） |
| 中文日志乱码 | 脚本用 PowerShell 5.1 执行时需 UTF-8 **BOM**；仓库 `scripts/*.ps1` 已带 BOM |
| 内存吃紧 / 容器 OOM | 调小 `start-all.ps1` 的 `$heapTable` 与 compose 的 `mem_limit`；或关闭 `service-mq`/`web-mgr` 等非必需模块（`-Only` 参数） |
| 想重置数据库 | `docker compose -f infra\docker-compose.yml down -v` 后重新 up（会重跑 daijia.sql 与 xxl 建表脚本） |
