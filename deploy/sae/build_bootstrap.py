"""给每个微服务的 src/main/resources 生成 bootstrap-dev.properties,让它连 SAE 上的 Nacos。

原理:各模块 bootstrap.properties 里有 spring.profiles.active=dev,bootstrap-dev.properties 会自动加载并覆盖
nacos 地址,IDEA 里直接点运行即可,不需要任何启动参数。Nacos 里的 dataId 仍是 <服务名>-dev.yaml。

用法:
  python -X utf8 deploy/sae/build_bootstrap.py --auto                   自动查名为 expose.clb_name 的 CLB 公网 IP 后生成
  python -X utf8 deploy/sae/build_bootstrap.py --host 1.2.3.4           手工指定 CLB 公网 IP 生成
  python -X utf8 deploy/sae/build_bootstrap.py --remove                删除,恢复连本机 127.0.0.1
生成的文件被 .gitignore 忽略;jar 打包时会带进去,要连回本机先 --remove 再重新打包。
"""
import argparse
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
TEMPLATE = """# 由 deploy/sae/build_bootstrap.py 生成,已被 .gitignore 忽略。
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


def load_env():
    env = {}
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return env


def clb_ip():
    cfg = json.loads((ROOT / "deploy" / "sae.config.json").read_text(encoding="utf-8"))
    out = subprocess.run(["aliyun", "slb", "DescribeLoadBalancers", "--RegionId", cfg["region"],
                          "--LoadBalancerName", cfg["expose"]["clb_name"]],
                         capture_output=True, text=True, encoding="utf-8").stdout
    lbs = json.loads(out).get("LoadBalancers", {}).get("LoadBalancer", [])
    if not lbs:
        raise SystemExit("找不到 CLB,先运行 sae.py expose")
    return lbs[0]["Address"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host")
    ap.add_argument("--auto", action="store_true", help="自动查 CLB 公网 IP")
    ap.add_argument("--remove", action="store_true")
    a = ap.parse_args()
    if not a.remove:
        a.host = a.host or (clb_ip() if a.auto else None)
        if not a.host:
            ap.error("需要 --host 或 --auto")
        env = load_env()
        pw = {"redis_pw": env["SAE_REDIS_PASSWORD"], "mongo_pw": env["SAE_MONGO_PASSWORD"]}
    mods = sorted(ROOT.glob("daijia-parent/**/src/main/resources/bootstrap.properties"))
    for b in mods:
        target = b.with_name("bootstrap-dev.properties")
        name = target.parents[3].name
        if a.remove:
            if target.exists():
                target.unlink()
                print(f"删除 {name}")
        else:
            target.write_text(TEMPLATE.format(host=a.host, **pw), encoding="utf-8", newline="\n")
            print(f"生成 {name}")
    print(f"共 {len(mods)} 个模块")


if __name__ == "__main__":
    main()
