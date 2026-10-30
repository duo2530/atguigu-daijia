# XXL-JOB 调度中心:官方 2.3.0 镜像只替换成本仓库 xxl-job-master 构建的 jar。
# 官方镜像里的 admin 缺少 dispatch 用到的 /jobinfo/addAndStartJob 等接口。
# 数据库地址、账号、密码、accessToken 全部由 SAE 的环境变量和 Secret 提供,镜像里不放任何环境信息。
# 构建上下文里放 xxl-job-admin.jar,由 sae.py build-image 准备。
ARG BASE
FROM ${BASE}
COPY xxl-job-admin.jar /app.jar
