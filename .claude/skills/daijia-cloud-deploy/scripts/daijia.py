"""乐尚代驾云上环境(阿里云 SAE)一键脚本,项目 skill daijia-cloud-deploy / daijia-cloud-power 的唯一入口。
底层调用 ~/.claude/skills/sae-microservice-deploy/scripts/sae.py,配置来源 本 skill 的 config/sae.config.json(cloud 段)。

  deploy [--force]   完整部署:NAT → 构建有变化的镜像 → 配置/密钥 → 中间件 → CLB → 推 Nacos → 微服务 → 网关 → 小程序地址 → 验收
  up                 启动已部署的环境(不构建):NAT → 中间件 → CLB → 配置 → 微服务 → 网关 → 小程序地址 → 验收
  down               关停:解绑并删除 CLB、停全部应用、删 NAT 和 EIP、小程序改回局域网 IP、删本地直连配置
  status             应用、CLB、NAT/EIP 状态
  verify             后端验收:应用在运行、Nacos 注册且健康、网关经 CLB 可达、实例能经 NAT 访问微信接口
  build [应用...] [--force]   只构建并推送镜像(标签 <应用>-<源码哈希>,ACR 已有的跳过)
  seed               生成 MySQL 初始化 SQL 到本 skill 的 .generated/mysql-init/
  nacos-push [--host H] 把 daijia-parent/DEFAULT_GROUP/*.yaml 推到 Nacos(默认云上,本机用 --host 127.0.0.1)并回读核对
  mp cloud|lan       两个小程序 config/dev.js 指向云上 CLB / 本机局域网 IP
  devlink up|ip|down 本地 IDEA 直连云上依赖:up 启动中间件+CLB+推配置+生成 bootstrap-dev.properties;ip 白名单追加当前出口 IP;down 删除本地配置
  doc                按模板生成 docs/交付说明/使用说明.md

用法: python -X utf8 .claude/skills/daijia-cloud-deploy/scripts/daijia.py <子命令>
脚本不打印任何密钥的值。
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

SKILL = Path(__file__).resolve().parent.parent
ROOT = SKILL.parents[2]  # skill -> skills -> .claude -> 项目根
SAE = Path.home() / ".claude/skills/sae-microservice-deploy/scripts/sae.py"
SRC_CFG = SKILL / "config/sae.config.json"
GEN = SKILL / ".generated"
GEN_CFG = GEN / "sae.config.json"
STATE = GEN / "state.json"
PARENT = ROOT / "daijia-parent"
NACOS_DIR = PARENT / "DEFAULT_GROUP"
MP = [ROOT / "mp-weixin-customer/config/dev.js", ROOT / "mp-weixin-driver/config/dev.js"]
MP_LINE = re.compile(r'(const\s+baseUrl\s*=\s*"http://)([^:/"]+)(:\d+)')
LAN_SYNC = ROOT / ".claude/skills/mp-lan-ip-sync/scripts/sync-lan-ip.py"
DOC = ROOT / "docs/交付说明/使用说明.md"
# 这些目录被所有微服务依赖:任何一个变了,全部微服务的源码哈希都会变,即全部重建
SHARED = ["pom.xml", "common", "model", "service-client", "service/pom.xml", "web/pom.xml"]
XXL_TAG = "xxl-job-2.3.0"

CFG = json.loads(SRC_CFG.read_text(encoding="utf-8"))
CLOUD = CFG["cloud"]
REGION = CFG["region"]
JAVA = [k for k, v in CFG["apps"].items() if v.get("kind") == "java"]
MIDDLE = [k for k in CFG["apps"] if k not in JAVA]
NO_PROXY = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # CLB 白名单按直连源 IP 判断,不能走代理


class Fail(Exception):
    pass


# ---------------------------------------------------------------- 基础

def run(cmd, cwd=ROOT, shell=False, capture=False):
    print("$", cmd if isinstance(cmd, str) else " ".join(map(str, cmd)), flush=True)
    r = subprocess.run(cmd if shell else [str(c) for c in cmd], cwd=cwd, shell=shell,
                       capture_output=capture, text=capture, encoding="utf-8" if capture else None,
                       errors="replace" if capture else None)
    if r.returncode:
        raise Fail(f"命令失败(退出码 {r.returncode})" + (f": {(r.stderr or r.stdout)[-400:]}" if capture else ""))
    return r.stdout if capture else None


def sae(*args, capture=False):
    return run([sys.executable, "-X", "utf8", "-I", SAE, "--config", GEN_CFG, *args], capture=capture)


def aliyun(product, *args):
    cmd = ["aliyun", product, *args]
    if product == "sae":
        cmd += ["--header", "Content-Type=application/json"]
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    out = (p.stdout or "").strip() or (p.stderr or "").strip()
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return {"_raw": out[:500]}


def need(r, what):
    if "_raw" in r or r.get("Success") is False:
        raise Fail(f"{what} 失败: {r.get('_raw') or r.get('Message')}")
    return r


def wait(fn, ok, what, timeout=300, step=5):
    t0 = time.time()
    while time.time() - t0 < timeout:
        x = fn()
        if ok(x):
            return x
        time.sleep(step)
    raise Fail(f"等待{what}超过 {timeout} 秒")


def http(url, data=None, timeout=30):
    with NO_PROXY.open(urllib.request.Request(url, data=data), timeout=timeout) as r:
        return r.read().decode("utf-8")


def app_name(k):
    return CFG.get("app_prefix", "") + k


def load_state():
    return json.loads(STATE.read_text(encoding="utf-8")) if STATE.is_file() else {}


def save_state(**kw):
    st = load_state()
    st.update(kw)
    GEN.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")


def write_gen(tags=None, clb_ip=None):
    """sae.py 读的配置:源配置 + 本次镜像标签 + 支付回调地址(CLB IP 每次开 CLB 都会变)。生成物被 .gitignore 忽略。"""
    cfg = json.loads(SRC_CFG.read_text(encoding="utf-8"))
    cfg["project_root"] = os.path.relpath(ROOT, GEN).replace("\\", "/")
    tags = tags or load_state().get("tags", {})
    for k in JAVA:
        if k in tags:
            cfg["apps"][k]["image"] = f"{CLOUD['repo']}:{k}-{tags[k]}"
    if clb_ip:
        cfg["config"]["configmap_fixed"][CLOUD["notify_env"]] = \
            f"http://{clb_ip}:{CLOUD['gateway_port']}/payment/wxPay/notify"
    GEN.mkdir(parents=True, exist_ok=True)
    GEN_CFG.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    return cfg


def list_apps():
    out, page = {}, 1
    while True:
        d = need(aliyun("sae", "ListApplications", "--RegionId", REGION, "--NamespaceId", CFG["namespace"],
                        "--CurrentPage", str(page), "--PageSize", "50"), "列应用")["Data"]
        apps = d.get("Applications") or []
        for a in apps:
            out[a["AppName"]] = a
        if len(apps) < 50:
            return out
        page += 1


def image_of(aid):
    return need(aliyun("sae", "DescribeApplicationConfig", "--AppId", aid), "查应用配置")["Data"].get("ImageUrl")


def pull_url(image):
    return f"{CFG['registry']['pull_host']}/{image}"


# ---------------------------------------------------------------- NAT 网关(实例出公网:微信登录/支付、腾讯云 OCR/人脸/COS、地图)

def find_nat():
    r = need(aliyun("vpc", "DescribeNatGateways", "--RegionId", REGION, "--VpcId", CFG["network"]["vpc"],
                    "--Name", CLOUD["nat_name"]), "查 NAT")
    gws = r["NatGateways"]["NatGateway"]
    return gws[0] if gws else None


def find_eip():
    r = need(aliyun("vpc", "DescribeEipAddresses", "--RegionId", REGION, "--EipName", CLOUD["eip_name"]), "查 EIP")
    e = r["EipAddresses"]["EipAddress"]
    return e[0] if e else None


def snat_entries(table):
    r = need(aliyun("vpc", "DescribeSnatTableEntries", "--RegionId", REGION, "--SnatTableId", table), "查 SNAT")
    return r["SnatTableEntries"]["SnatTableEntry"]


def nat_up():
    net = CFG["network"]
    if not find_nat():
        r = need(aliyun("vpc", "CreateNatGateway", "--RegionId", REGION, "--VpcId", net["vpc"],
                        "--VSwitchId", net["vswitch"], "--NatType", "Enhanced", "--NetworkType", "internet",
                        "--InternetChargeType", "PayByLcu", "--Name", CLOUD["nat_name"]), "创建 NAT")
        print(f"NAT 网关 {r['NatGatewayId']} 创建中")
    nat = wait(find_nat, lambda n: n and n["Status"] == "Available", "NAT 可用")
    if not find_eip():
        need(aliyun("vpc", "AllocateEipAddress", "--RegionId", REGION, "--Bandwidth", "100",
                    "--InternetChargeType", "PayByTraffic", "--InstanceChargeType", "PostPaid",
                    "--Name", CLOUD["eip_name"]), "申请 EIP")
    eip = wait(find_eip, lambda e: e and e["Status"] in ("Available", "InUse"), "EIP 可用")
    if eip["Status"] == "Available":
        need(aliyun("vpc", "AssociateEipAddress", "--RegionId", REGION, "--AllocationId", eip["AllocationId"],
                    "--InstanceId", nat["NatGatewayId"], "--InstanceType", "Nat"), "EIP 绑定 NAT")
        eip = wait(find_eip, lambda e: e["Status"] == "InUse", "EIP 绑定")
    elif eip.get("InstanceId") != nat["NatGatewayId"]:
        raise Fail(f"EIP {CLOUD['eip_name']} 绑在别的实例 {eip.get('InstanceId')} 上")
    table = nat["SnatTableIds"]["SnatTableId"][0]
    if not any(e.get("SourceVSwitchId") == net["vswitch"] for e in snat_entries(table)):
        need(aliyun("vpc", "CreateSnatEntry", "--RegionId", REGION, "--SnatTableId", table,
                    "--SourceVSwitchId", net["vswitch"], "--SnatIp", eip["IpAddress"],
                    "--SnatEntryName", "daijia"), "创建 SNAT")
    wait(lambda: snat_entries(table), lambda es: any(e.get("SourceVSwitchId") == net["vswitch"]
                                                       and e["Status"] == "Available" for e in es), "SNAT 生效")
    print(f"NAT {nat['NatGatewayId']} 就绪,实例出口 IP {eip['IpAddress']}")


def nat_down():
    nat = find_nat()
    if nat:
        # --Force true 会一并删除 SNAT 条目
        need(aliyun("vpc", "DeleteNatGateway", "--RegionId", REGION, "--NatGatewayId", nat["NatGatewayId"],
                    "--Force", "true"), "删除 NAT")
        wait(find_nat, lambda n: n is None, "NAT 删除", timeout=600)
        print(f"NAT {nat['NatGatewayId']} 已删除")
    eip = find_eip()
    if eip:
        if eip["Status"] == "InUse":
            need(aliyun("vpc", "UnassociateEipAddress", "--RegionId", REGION, "--AllocationId", eip["AllocationId"],
                        "--InstanceId", eip["InstanceId"], "--InstanceType", "Nat"), "解绑 EIP")
        wait(find_eip, lambda e: e["Status"] == "Available", "EIP 解绑")
        need(aliyun("vpc", "ReleaseEipAddress", "--RegionId", REGION, "--AllocationId", eip["AllocationId"]),
             "释放 EIP")
        print(f"EIP {eip['IpAddress']} 已释放")


# ---------------------------------------------------------------- CLB(一个公网 CLB:网关端口对全网,中间件端口套白名单)

def find_clb():
    r = aliyun("slb", "DescribeLoadBalancers", "--RegionId", REGION, "--LoadBalancerName", CFG["expose"]["clb_name"])
    lbs = (r.get("LoadBalancers") or {}).get("LoadBalancer") or []
    return lbs[0] if lbs else None


def slb_of(aid):
    d = need(aliyun("sae", "DescribeApplicationSlbs", "--AppId", aid), "查应用 CLB")["Data"] or {}
    return d.get("InternetSlbId"), {x["Port"] for x in d.get("Internet") or []}


def clear_stale_bindings(apps, lbid):
    """SAE 里可能残留指向已删除 CLB 的绑定(SLB 侧 404),expose 会报"已绑定另一个 CLB"。先解绑。"""
    for k in list(CFG["expose"]["ports"]) + [CLOUD["gateway_app"]]:
        a = apps.get(app_name(k))
        if not a:
            continue
        cur, _ = slb_of(a["AppId"])
        if not cur or cur == lbid:
            continue
        r = aliyun("slb", "DescribeLoadBalancerAttribute", "--RegionId", REGION, "--LoadBalancerId", cur)
        if "LoadBalancerId" in r:
            raise Fail(f"{k} 绑着另一个仍存在的 CLB {cur},不是本脚本建的;先确认再处理")
        print(f"  {k} 残留已删除的 CLB {cur} 的绑定,解绑")
        sae("slb-unbind", k)


def network_up():
    """建/复用 CLB,中间件端口绑定并套白名单(本机直连、推 Nacos)。返回 CLB 公网 IP。"""
    lb = find_clb()
    clear_stale_bindings(list_apps(), lb["LoadBalancerId"] if lb else None)
    sae("expose", "--no-start")
    return find_clb()["Address"]


def bind_gateway():
    lb = find_clb()
    gw = list_apps()[app_name(CLOUD["gateway_app"])]
    cur, ports = slb_of(gw["AppId"])
    if cur == lb["LoadBalancerId"] and CLOUD["gateway_port"] in ports:
        print(f"网关已绑定 {lb['Address']}:{CLOUD['gateway_port']}")
        return
    # 不带 --acl:网关端口要让手机访问,对全网开放
    sae("slb-bind", CLOUD["gateway_app"], "--port", str(CLOUD["gateway_port"]), "--slb-id", lb["LoadBalancerId"])


# ---------------------------------------------------------------- 镜像

def files_hash(paths):
    h = hashlib.sha1()
    for base in paths:
        p = PARENT / base
        items = [p] if p.is_file() else sorted(x for x in p.rglob("*") if x.is_file())
        for f in items:
            rel = f.relative_to(PARENT).as_posix()
            if "/target/" in f"/{rel}" or "/.idea/" in f"/{rel}" or f.name == "bootstrap-dev.properties" \
                    or f.suffix == ".iml":
                continue
            h.update(rel.encode())
            h.update(f.read_bytes().replace(b"\r\n", b"\n"))
    return h.hexdigest()


def source_tags():
    shared = files_hash(SHARED)
    return {k: hashlib.sha1((shared + files_hash([CFG["apps"][k]["module"].removeprefix("daijia-parent/")])
                             + CFG["java_base"]).encode()).hexdigest()[:10] for k in JAVA}


def acr_tags():
    ns = CFG["registry"]["push_host"].split("/", 1)[1]
    have, page = set(), 1
    while True:
        r = aliyun("cr", "GET", f"/repos/{ns}/{CLOUD['repo']}/tags", "--version", "2016-06-07", "--force",
                   "--endpoint", f"cr.{REGION}.aliyuncs.com", "--PageSize", "100", "--Page", str(page))
        tags = (r.get("data") or {}).get("tags") or []
        have |= {t["tag"] for t in tags}
        if len(tags) < 100:
            return have
        page += 1


def dev_bootstraps():
    return sorted(PARENT.glob("**/src/main/resources/bootstrap-dev.properties"))


def package(modules):
    """mvn 打包。本地直连用的 bootstrap-dev.properties(含 CLB IP 和密码)不能进 jar:打包期间挪开,结束后放回。"""
    stash = GEN / "devlink-stash"
    moved = []
    for f in dev_bootstraps():
        dst = stash / f.relative_to(PARENT)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(f), dst)
        moved.append((dst, f))
    for f in PARENT.glob("**/target/classes/bootstrap-dev.properties"):
        f.unlink()  # 旧构建留下的副本,不删会被打进 jar
    try:
        cmd = "mvn -q -o -DskipTests -pl " + ",".join(modules) + " -am package"
        try:
            run(cmd, cwd=PARENT, shell=True)
        except Fail:
            run(cmd.replace(" -o ", " "), cwd=PARENT, shell=True)  # 离线缺依赖时联网再试
    finally:
        for src, dst in moved:
            shutil.move(str(src), dst)


def build(force=False, only=None):
    """按源码哈希构建:ACR 里已有 <应用>-<哈希> 标签的跳过。返回全部微服务的标签。"""
    write_gen()
    sae("acr-repo", CLOUD["repo"])  # 私有,已存在时跳过
    tags = source_tags()
    have = set() if force else acr_tags()
    todo = [k for k in (only or JAVA) if f"{k}-{tags[k]}" not in have]
    if XXL_TAG not in have and not only:
        sae("build-image", "xxl-job")
    if not todo:
        print("微服务镜像都是最新的,不用构建")
        return tags
    print("需要构建:", ", ".join(todo))
    package([CFG["apps"][k]["module"].removeprefix("daijia-parent/") for k in todo])
    write_gen({**load_state().get("tags", {}), **tags})
    sae("build-java", *todo)
    return tags


# ---------------------------------------------------------------- 配置

def seed():
    """MySQL 初始化 SQL(数据目录为空时由官方镜像执行)。只保留 schema 和必要种子数据。"""
    out = GEN / "mysql-init"
    out.mkdir(parents=True, exist_ok=True)

    def read(p):
        return p.read_text(encoding="utf-8-sig").replace("\r\n", "\n")

    nacos = "CREATE DATABASE IF NOT EXISTS `nacos` DEFAULT CHARACTER SET utf8mb4;\nUSE `nacos`;\n" \
            + read(SKILL / "config/mysql/nacos-schema.sql")
    skip = {"sys_oper_log", "sys_login_log"}  # 历史日志约 190KB,不需要
    daijia = "\n".join(l for l in read(ROOT / "daijia.sql").split("\n")
                       if not (m := re.match(r"INSERT INTO `(\w+)`", l)) or m.group(1) not in skip)
    xxl = "\n".join(l for l in read(ROOT / "xxl-job-master/doc/db/tables_xxl_job.sql").split("\n")
                    if not l.startswith("INSERT INTO `xxl_job_info`"))  # 示例任务
    for name, text in (("01-nacos.sql", nacos), ("02-daijia.sql", daijia), ("03-xxl-job.sql", xxl)):
        (out / name).write_text(text, encoding="utf-8", newline="\n")
        print(f"{name}: {len(text.encode('utf-8'))} 字节")


def nacos_push(host):
    base = f"http://{host}:8848/nacos/v1"
    wait(lambda: _try(lambda: http(f"{base}/console/health/readiness", timeout=5)),
         lambda r: r is not None, "Nacos 就绪", timeout=300)
    bad = []
    for f in sorted(NACOS_DIR.glob("*.yaml")):
        content = f.read_bytes().decode("utf-8")
        q = {"dataId": f.name, "group": "DEFAULT_GROUP"}
        r = http(f"{base}/cs/configs", urllib.parse.urlencode({**q, "type": "yaml", "content": content}).encode())
        # publish 返回 true 后回读可能短暂是旧值,重试几次再判定
        same = False
        for _ in range(6):
            back = _try(lambda: http(f"{base}/cs/configs?" + urllib.parse.urlencode(q)))
            if back is not None and back.replace("\r\n", "\n") == content.replace("\r\n", "\n"):
                same = True
                break
            time.sleep(2)
        print(f"  {f.name:<28} 推送 {r}  回读{'一致' if same else '不一致'}")
        if r != "true" or not same:
            bad.append(f.name)
    if bad:
        raise Fail(f"Nacos 配置推送/核对失败: {', '.join(bad)}")


def _try(fn):
    try:
        return fn()
    except Exception:
        return None


def config_digest(cfg):
    """配置变了(ConfigMap、应用环境变量、.env、Nacos yaml),运行中的微服务要重新部署才读到新值。"""
    h = hashlib.sha1(json.dumps([cfg["config"], {k: cfg["apps"][k].get("envs") for k in JAVA}],
                                sort_keys=True, ensure_ascii=False).encode())
    h.update((ROOT / ".env").read_bytes())
    for f in sorted(NACOS_DIR.glob("*.yaml")):
        h.update(f.read_bytes())
    return h.hexdigest()


# ---------------------------------------------------------------- 小程序地址、本地直连

def mp_cloud(ip):
    for p in MP:
        raw = p.read_bytes().decode("utf-8")
        new = MP_LINE.sub(lambda m: f"{m.group(1)}{ip}:{CLOUD['gateway_port']}", raw, count=1)
        p.write_bytes(new.encode("utf-8"))
        print(f"{p.relative_to(ROOT).as_posix()} -> http://{ip}:{CLOUD['gateway_port']}")
    print("开发者工具里重新编译后点「预览」,把二维码发给业务人员;手机上要打开调试模式。")


def mp_lan():
    run([sys.executable, "-X", "utf8", LAN_SYNC])


BOOTSTRAP = """# 由 .claude/skills/daijia-cloud-deploy/scripts/daijia.py devlink up 生成,已被 .gitignore 忽略,打包时会被挪开。
# 连接 SAE 上的 Nacos(CLB 公网 IP,白名单只放行已登记的出口 IP)。删除本文件即恢复连本机 127.0.0.1。
spring.cloud.nacos.discovery.server-addr={host}:8848
spring.cloud.nacos.config.server-addr={host}:8848
# Nacos 配置里的依赖服务地址是占位符(默认本机)。本机 IDEA 通过 CLB 公网地址访问;
# 部署到 SAE 时由 ConfigMap 的 DAIJIA_*_HOST 环境变量提供 K8s 服务名。
daijia.mysql.host={host}
daijia.redis.host={host}
daijia.mongo.host={host}
daijia.rabbitmq.host={host}
daijia.xxljob.host={host}
# Redis / Mongo 在 SAE 上开了密码(来自 .env,不进 Nacos)。
daijia.redis.password={redis_pw}
daijia.mongo.auth=root:{mongo_pw}@
"""


def env_file():
    env = {}
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return env


def devlink_write(host):
    env = env_file()
    mods = sorted(PARENT.glob("**/src/main/resources/bootstrap.properties"))
    for b in mods:
        if "/target/" in b.as_posix():
            continue
        b.with_name("bootstrap-dev.properties").write_text(
            BOOTSTRAP.format(host=host, redis_pw=env["SAE_REDIS_PASSWORD"], mongo_pw=env["SAE_MONGO_PASSWORD"]),
            encoding="utf-8", newline="\n")
    print(f"已生成 {len(mods)} 个模块的 bootstrap-dev.properties,指向 {host}")


def devlink_remove():
    fs = dev_bootstraps()
    for f in fs:
        f.unlink()
    print(f"已删除 {len(fs)} 个 bootstrap-dev.properties(本地服务恢复连本机)")


# ---------------------------------------------------------------- 验收

def verify(ip=None):
    lb = find_clb()
    ip = ip or (lb or {}).get("Address")
    if not ip:
        raise Fail("没有 CLB,环境没启动")
    bad = []
    apps = list_apps()
    stopped = [k for k in CFG["apps"] if (apps.get(app_name(k)) or {}).get("RunningInstances", 0) < 1]
    print("1. 应用运行:", "全部运行" if not stopped else f"未运行 {stopped}")
    bad += [f"未运行 {k}" for k in stopped]

    def unhealthy():
        out = []
        for k in JAVA:
            r = _try(lambda: json.loads(http(f"http://{ip}:8848/nacos/v1/ns/instance/list?serviceName={k}", timeout=10)))
            if not r or not any(h.get("healthy") for h in r.get("hosts", [])):
                out.append(k)
        return out
    left = _try(lambda: wait(unhealthy, lambda x: not x, "Nacos 注册", timeout=600, step=15)) or unhealthy()
    print("2. Nacos 注册且健康:", "13 个服务全部健康" if not left else f"缺少/不健康 {left}")
    bad += [f"Nacos 未注册 {k}" for k in left]

    for path in ("/customer-api/customer/login/daijia-verify", "/driver-api/driver/login/daijia-verify"):
        body = None
        for _ in range(4):  # 网关冷启动后首个请求可能很慢
            body = _try(lambda: http(f"http://{ip}:{CLOUD['gateway_port']}{path}", timeout=30))
            if body:
                break
            time.sleep(10)
        code = _try(lambda: json.loads(body).get("code"))
        print(f"3. 网关 {path}: " + (f"返回业务 JSON code={code}" if code is not None else f"失败 {body!r:.200}"))
        if code is None:
            bad.append(f"网关不可达 {path}")

    # 假 code 会让 service-customer 调微信 jscode2session:能拿到微信的 40029 说明经 NAT 出公网正常
    logs = _try(lambda: sae("logs", "service-customer", "--tail", "400", "--grep",
                            "40029|invalid code|timed out|UnknownHost|Connect to api.weixin", capture=True)) or ""
    if re.search(r"40029|invalid code", logs):
        print("4. 出公网: 微信接口返回 40029(假 code 被拒),NAT 正常")
    elif re.search(r"timed out|UnknownHost|Connect to api", logs):
        print("4. 出公网: 访问微信接口超时/解析失败,检查 NAT 和 SNAT")
        bad.append("实例不能访问公网")
    else:
        print("4. 出公网: 日志里没看到微信接口的结果,手动看 logs service-customer")
        bad.append("出公网未确认")
    if bad:
        raise Fail("验收未通过: " + "; ".join(bad))
    print(f"\n验收通过。小程序后端地址 http://{ip}:{CLOUD['gateway_port']}")


# ---------------------------------------------------------------- 子命令

def cmd_deploy(a):
    if not (GEN / "mysql-init").is_dir():
        seed()
    nat_up()
    tags = build(a.force)
    lb = find_clb()
    write_gen(tags, lb and lb["Address"])
    sae("sync-config")
    sae("create", *MIDDLE)
    sae("start", *MIDDLE)
    apps = list_apps()
    xxl = apps[app_name("xxl-job")]
    if image_of(xxl["AppId"]) != pull_url(f"{CLOUD['repo']}:{XXL_TAG}"):
        sae("redeploy", "xxl-job")
    ip = network_up()
    nacos_push(ip)
    cfg = write_gen(tags, ip)
    sae("sync-config")
    apps = list_apps()
    missing = [k for k in JAVA if app_name(k) not in apps]
    if missing:
        sae("create", *missing)
    existing = [k for k in JAVA if k not in missing]
    if existing:
        sae("start", *existing)
    digest = config_digest(cfg)
    conf_changed = digest != load_state().get("config_digest")
    stale = [k for k in existing
             if conf_changed or image_of(apps[app_name(k)]["AppId"]) != pull_url(cfg["apps"][k]["image"])]
    if stale:
        print("重新部署(镜像或配置有变化):", ", ".join(stale))
        sae("redeploy", *stale)
    save_state(tags=tags, config_digest=digest, clb_ip=ip)
    bind_gateway()
    mp_cloud(ip)
    verify(ip)


def cmd_up(a):
    nat_up()
    write_gen()
    sae("start", *MIDDLE)
    ip = network_up()
    cfg = write_gen(None, ip)
    sae("sync-config")  # 新 CLB IP 写进支付回调地址;停着的微服务启动时读到新值
    sae("start", *JAVA)  # 停着的应用启动时重新读取 ConfigMap,拿到新的回调地址
    save_state(config_digest=config_digest(cfg), clb_ip=ip)
    bind_gateway()
    mp_cloud(ip)
    verify(ip)


def cmd_down(a):
    write_gen()
    devlink_remove()
    apps = list_apps()
    gw = apps.get(app_name(CLOUD["gateway_app"]))
    if gw and slb_of(gw["AppId"])[0]:
        sae("slb-unbind", CLOUD["gateway_app"])
    sae("unexpose")  # 解绑中间件端口并删除 CLB;白名单 ACL 保留(不计费)
    sae("stop")
    nat_down()
    mp_lan()
    cmd_status(a)


def cmd_status(a):
    write_gen()
    sae("status")
    lb = find_clb()
    print("CLB:", f"{lb['LoadBalancerId']} {lb['Address']}" if lb else "无")
    nat, eip = find_nat(), find_eip()
    print("NAT:", f"{nat['NatGatewayId']} {nat['Status']}" if nat else "无",
          " EIP:", f"{eip['IpAddress']} {eip['Status']}" if eip else "无")
    running = [k for k, v in list_apps().items() if (v.get("RunningInstances") or 0) > 0]
    print("仍在计费的:", ", ".join(([f"{len(running)} 个运行中的应用"] if running else [])
                              + (["CLB"] if lb else []) + (["NAT"] if nat else []) + (["EIP"] if eip else [])) or "无")


def cmd_build(a):
    tags = build(a.force, a.apps or None)
    save_state(tags={**load_state().get("tags", {}), **tags})


def cmd_devlink(a):
    write_gen()
    if a.action == "up":
        sae("start", *MIDDLE)
        ip = network_up()
        nacos_push(ip)
        devlink_write(ip)
    elif a.action == "ip":
        sae("acl-sync")
    else:
        devlink_remove()


def cmd_doc(a):
    tpl = (SKILL / "templates/使用说明.md").read_text(encoding="utf-8")
    DOC.parent.mkdir(parents=True, exist_ok=True)
    DOC.write_text(tpl.replace("{{date}}", date.today().isoformat()), encoding="utf-8", newline="\n")
    print(f"已生成 {DOC.relative_to(ROOT).as_posix()}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sp = sub.add_parser("deploy")
    sp.add_argument("--force", action="store_true", help="全部微服务镜像重建")
    sp.set_defaults(fn=cmd_deploy)
    for name, fn in (("up", cmd_up), ("down", cmd_down), ("status", cmd_status), ("doc", cmd_doc)):
        sub.add_parser(name).set_defaults(fn=fn)
    sub.add_parser("verify").set_defaults(fn=lambda a: (write_gen(), verify()))
    sub.add_parser("seed").set_defaults(fn=lambda a: seed())
    sp = sub.add_parser("nacos-push")
    sp.add_argument("--host", help="Nacos 主机,默认云上 CLB;本机 Nacos 写 127.0.0.1")
    sp.set_defaults(fn=lambda a: nacos_push(a.host or find_clb()["Address"]))
    sp = sub.add_parser("build")
    sp.add_argument("apps", nargs="*")
    sp.add_argument("--force", action="store_true")
    sp.set_defaults(fn=cmd_build)
    sp = sub.add_parser("mp")
    sp.add_argument("target", choices=["cloud", "lan"])
    sp.set_defaults(fn=lambda a: mp_cloud(find_clb()["Address"]) if a.target == "cloud" else mp_lan())
    sp = sub.add_parser("devlink")
    sp.add_argument("action", choices=["up", "ip", "down"])
    sp.set_defaults(fn=cmd_devlink)
    a = ap.parse_args()
    try:
        a.fn(a)
    except Fail as e:
        print(f"错误: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
