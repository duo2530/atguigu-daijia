"""把 daijia-parent/DEFAULT_GROUP/*.yaml 里写死的依赖服务地址改成占位符,默认值保持原样(本机运行不受影响)。

  redis.host / mongodb.host / rabbitmq.host  ->  ${daijia.redis.host:原值} 等
  jdbc:mysql://127.0.0.1:3306/...            ->  jdbc:mysql://${daijia.mysql.host:127.0.0.1}:3306/...
  http://localhost:8080/xxl-job-admin        ->  http://${daijia.xxljob.host:localhost}:8080/xxl-job-admin

占位符名 daijia.redis.host 会被 Spring 映射到环境变量 DAIJIA_REDIS_HOST,所以 SAE 里用 ConfigMap 的环境变量覆盖,
IDEA 里用 bootstrap-dev.properties(或环境变量)覆盖。可重复运行。
"""
import re
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent.parent / "daijia-parent" / "DEFAULT_GROUP"
SECTION = {"redis": "daijia.redis.host", "mongodb": "daijia.mongo.host", "rabbitmq": "daijia.rabbitmq.host"}
HOST_LINE = re.compile(r"^(\s+host:\s*)(localhost|127\.0\.0\.1)(\s*)$")


def parent_key(lines, i):
    indent = len(lines[i]) - len(lines[i].lstrip())
    for j in range(i - 1, -1, -1):
        s = lines[j]
        if not s.strip() or s.lstrip().startswith("#"):
            continue
        ind = len(s) - len(s.lstrip())
        if ind < indent:
            return s.strip().rstrip(":").split(":")[0]
    return None


def convert(text):
    nl = "\r\n" if "\r\n" in text else "\n"
    lines = text.split(nl)
    for i, line in enumerate(lines):
        m = HOST_LINE.match(line)
        if m:
            prop = SECTION.get(parent_key(lines, i))
            if not prop:
                raise SystemExit(f"无法确定 host 所属服务: {line!r} (上级 {parent_key(lines, i)})")
            lines[i] = f"{m.group(1)}${{{prop}:{m.group(2)}}}{m.group(3)}"
    text = nl.join(lines)
    text = re.sub(r"jdbc:mysql://(127\.0\.0\.1):3306/", r"jdbc:mysql://${daijia.mysql.host:\1}:3306/", text)
    text = text.replace("http://localhost:8080/xxl-job-admin", "http://${daijia.xxljob.host:localhost}:8080/xxl-job-admin")
    return text


def main():
    for f in sorted(SRC.glob("*.yaml")):
        old = f.read_bytes().decode("utf-8")
        new = convert(old)
        if new != old:
            f.write_bytes(new.encode("utf-8"))
            print("改写", f.name)
    left = []
    for f in sorted(SRC.glob("*.yaml")):
        for n, l in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r"127\.0\.0\.1|localhost", l) and "${" not in l and not l.strip().startswith("#"):
                left.append(f"{f.name}:{n}: {l.strip()}")
    print("仍含本地地址(不是依赖服务地址,保持不变):")
    for x in left:
        print("  ", x)


if __name__ == "__main__":
    main()
