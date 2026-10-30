"""生成 MySQL 初始化脚本到 .generated/mysql-init/(被 .gitignore 忽略,sae.py sync-config 会把它们推成 ConfigMap)。

只保留 schema 和必要种子数据:
  - nacos:官方 2.1.0 建表脚本(schema + 默认账号)
  - daijia:daijia.sql 去掉操作日志、登录日志两张表的历史数据(约 190KB),其余种子(菜单/角色/管理员/部门/岗位/优惠券)保留
  - xxl_job:官方 2.3.0 建表脚本,保留执行器分组(dispatch 的 jobGroupId=1)、管理员账号、调度锁,去掉示例任务

用法: python -X utf8 deploy/sae/build_seed.py
"""
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
OUT = HERE / ".generated" / "mysql-init"
SKIP_DATA = {"sys_oper_log", "sys_login_log"}


def read(p):
    return p.read_text(encoding="utf-8-sig").replace("\r\n", "\n")


def build_nacos():
    return "CREATE DATABASE IF NOT EXISTS `nacos` DEFAULT CHARACTER SET utf8mb4;\nUSE `nacos`;\n" + read(HERE / "mysql" / "nacos-schema.sql")


def build_daijia():
    keep = []
    for line in read(ROOT / "daijia.sql").split("\n"):
        m = re.match(r"INSERT INTO `(\w+)`", line)
        if m and m.group(1) in SKIP_DATA:
            continue
        keep.append(line)
    return "\n".join(keep)


def build_xxl():
    keep = []
    for line in read(ROOT / "xxl-job-master" / "doc" / "db" / "tables_xxl_job.sql").split("\n"):
        if line.startswith("INSERT INTO `xxl_job_info`"):  # 示例任务
            continue
        keep.append(line)
    return "\n".join(keep)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    for name, fn in (("01-nacos.sql", build_nacos), ("02-daijia.sql", build_daijia), ("03-xxl-job.sql", build_xxl)):
        text = fn()
        (OUT / name).write_text(text, encoding="utf-8", newline="\n")
        print(f"{name}: {len(text.encode('utf-8'))} 字节")


if __name__ == "__main__":
    main()
