"""真机调试用:把两个小程序 config/dev.js 里的后端地址同步成本机当前局域网 IP。

  python -X utf8 .claude/skills/mp-lan-ip-sync/scripts/sync-lan-ip.py            检测本机 IP 并改写 dev.js(只改主机部分,保留端口和路径)
  python -X utf8 .claude/skills/mp-lan-ip-sync/scripts/sync-lan-ip.py --check    只检查,不改;不一致时输出提示,退出码始终为 0(给 hook 用)
  python -X utf8 .claude/skills/mp-lan-ip-sync/scripts/sync-lan-ip.py --ip 1.2.3.4   手工指定 IP(检测错了时用)
  --quiet  一致时不输出

检测方式:取"有默认网关且已连接"的网卡上的私网 IPv4(排除 VPN/Docker/虚拟网卡)。
改完会探测 http://<ip>:8600 是否可达,不可达时提示常见原因。
"""
import argparse
import ipaddress
import json
import re
import socket
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]  # 项目根:scripts -> skill -> skills -> .claude -> 根
TARGETS = [ROOT / "mp-weixin-customer/config/dev.js", ROOT / "mp-weixin-driver/config/dev.js"]
LINE = re.compile(r'(const\s+baseUrl\s*=\s*"http://)([^:/"]+)(:\d+[^"]*")')
PORT = 8600


def detect_ip():
    ps = ("Get-NetIPConfiguration | Where-Object { $_.IPv4DefaultGateway -and $_.NetAdapter.Status -eq 'Up' } | "
          "ForEach-Object { [pscustomobject]@{ alias=$_.InterfaceAlias; ip=@($_.IPv4Address.IPAddress) } } | ConvertTo-Json -Compress")
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True,
                             encoding="utf-8", timeout=20).stdout.strip()
        rows = json.loads(out) if out else []
        rows = rows if isinstance(rows, list) else [rows]
        for r in rows:
            for ip in r.get("ip") or []:
                if ipaddress.ip_address(ip).is_private and not ip.startswith("169.254."):
                    return ip, r["alias"]
    except Exception:
        pass
    try:  # 兜底:UDP 连一下取出口地址(不会真发包)
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]
        s.close()
        if ipaddress.ip_address(ip).is_private:
            return ip, "default-route"
    except Exception:
        pass
    return None, None


def current(path):
    m = LINE.search(path.read_text(encoding="utf-8"))
    return m.group(2) if m else None


def reachable(ip):
    try:
        with socket.create_connection((ip, PORT), timeout=3):
            return True
    except OSError:
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--ip")
    a = ap.parse_args()
    ip, nic = (a.ip, "手工指定") if a.ip else detect_ip()
    if not ip:
        if not a.quiet:
            print("没检测到可用的局域网 IP(没连 Wi-Fi/网线?),可用 --ip 手工指定")
        return 0
    stale = [(p, current(p)) for p in TARGETS if p.exists() and current(p) != ip]
    cloud = [(p, h) for p, h in stale if h and not ipaddress.ip_address(h).is_private] if all(
        re.fullmatch(r"[\d.]+", h or "") for _, h in stale) else []
    if a.check and cloud:
        # daijia-cloud-deploy 把地址切到了云上 CLB(给业务人员扫预览码用),这是预期状态,不报不一致
        if not a.quiet or len(cloud) == len(stale):
            print(f"小程序当前指向云上后端 {cloud[0][1]}(daijia-cloud-power down 后会改回本机局域网 IP)。")
        return 0
    if a.check:
        if stale:
            print(f"小程序真机调试地址与本机当前 IP 不一致:本机 {ip}({nic}),"
                  + ";".join(f"{p.parent.parent.name} 写的是 {old}" for p, old in stale)
                  + "。真机调试前运行 `python -X utf8 .claude/skills/mp-lan-ip-sync/scripts/sync-lan-ip.py` 同步(或让 agent 用 mp-lan-ip-sync skill)。")
        elif not a.quiet:
            print(f"小程序地址已是本机当前 IP {ip}")
        return 0
    for p, old in stale:
        raw = p.read_text(encoding="utf-8")
        p.write_text(LINE.sub(lambda m: m.group(1) + ip + m.group(3), raw, count=1), encoding="utf-8", newline="")
        print(f"{p.relative_to(ROOT)}: {old} -> {ip}")
    if not stale:
        print(f"已是本机当前 IP {ip},无需修改")
    print(f"本机 {ip}({nic}) 网关 {PORT} " + ("可达" if reachable(ip) else
          "不可达:确认后端已启动(8600 监听 0.0.0.0)、防火墙放行 java、网络类型不是被隔离的访客网络"))
    print("下一步:开发者工具重新编译/重新扫码;手机要和电脑在同一 WLAN,且关掉代理/VPN。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
