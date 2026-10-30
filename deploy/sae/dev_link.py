"""本地 IDEA 直连云上依赖服务的一键脚本(封装 sae.py,配置见 deploy/sae.config.json 的 expose 段)。

  python -X utf8 deploy/sae/dev_link.py up        同步密钥 → 滚动更新 redis/mongo(带密码) → 启动依赖 → 建/复用一个 CLB 绑端口
                                                  → 白名单加入当前出口 IP → 推 Nacos 配置 → 生成各模块 bootstrap-dev.properties
  python -X utf8 deploy/sae/dev_link.py ip        换了网络(家/公司)后,把当前出口 IP 追加进白名单(保留其他地点)
  python -X utf8 deploy/sae/dev_link.py status    CLB、白名单、监听状态
  python -X utf8 deploy/sae/dev_link.py down      删除本地 bootstrap-dev.properties、解绑并删除 CLB(应用不停,停用 sae.py cleanup)
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
SAE = Path.home() / ".claude/skills/sae-microservice-deploy/scripts/sae.py"
CONFIG = ROOT / "deploy" / "sae.config.json"


def run(*cmd):
    print("$", " ".join(str(c) for c in cmd), flush=True)
    r = subprocess.run([str(c) for c in cmd], cwd=ROOT)
    if r.returncode:
        sys.exit(f"失败(退出码 {r.returncode}):{cmd[0]}")


def sae(*args):
    run(sys.executable, "-X", "utf8", "-I", SAE, "--config", CONFIG, *args)


def nacos_ip():
    import json
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    out = subprocess.run(["aliyun", "slb", "DescribeLoadBalancers", "--RegionId", cfg["region"],
                          "--LoadBalancerName", cfg["expose"]["clb_name"]],
                         capture_output=True, text=True, encoding="utf-8").stdout
    return json.loads(out)["LoadBalancers"]["LoadBalancer"][0]["Address"]


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    boot = ROOT / "deploy/sae/build_bootstrap.py"
    if cmd == "up":
        sae("sync-config")
        sae("redeploy", "redis", "mongo")  # 新增了密码和 Secret 挂载,已存在的应用要滚动更新才生效
        sae("expose")
        run(sys.executable, "-X", "utf8", "-I", boot, "--auto")
        run("pwsh", "-NoProfile", "-File", ROOT / "scripts/nacos-import.ps1", "-NacosAddr", f"http://{nacos_ip()}:8848")
    elif cmd == "ip":
        sae("acl-sync")
    elif cmd == "status":
        sae("expose-status")
    elif cmd == "down":
        run(sys.executable, "-X", "utf8", "-I", boot, "--remove")
        sae("unexpose")
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
