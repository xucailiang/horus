# 可选机器资源监控

监控由宿主 Agent/子 Agent 使用已有 SSH 能力完成，不为 horus 增加常驻监控服务或 SSH 管理层。
机器语言和后端语言无关。只在用户选择启用并提供已授权 SSH 目标后执行；凭据使用已有
SSH agent/配置，不向聊天索取密码，不安装远程软件或提权。

## 基线与并发建议

用户明确选择监控、授权 SSH 目标和只读采样后，可先采集短基线（默认约 20s），
了解 CPU/GPU 使用率、内存/显存余量及已有负载。该授权仅允许资源采样，不批准 API 测试。
将基线摘要、时间范围、缺口和并发建议理由写入 plan.supportingContext，随计划审核。

结合用户测试目标、接口行为、资源余量与平台上限建议固定并发和迭代预算。已有负载较高
时提出较保守的初始规模；空闲资源不等于已证明容量。GPU 仅在被测工作负载相关时作为依据。
不根据 CPU 百分比套用通用公式，不在计划批准前试跑未授权负载。

## 测试计划确认前

确认机器/SSH alias、可用指标、采样间隔（默认 2s）和时限。Linux 可读 /proc/stat、
/proc/meminfo；NVIDIA GPU 可用 nvidia-smi。其他系统由宿主选择等效只读命令，能力不足
说明缺失。将 targetLabel、commands、intervalSeconds、maxDurationSeconds 写入
plan.resourceMonitoring；关闭时留空。展示基线和并发设置理由、采样命令和访问目标，连同测试计划等待用户
回复“我确定”。变更目标、命令或预算需修改计划并重新确认。不能把用户
同意测试 API 自动视为同意访问另一台机器。

## 批准后并行采样

有子 Agent 能力时，交给一个专职子 Agent，主 Agent 保持推进测试和报告。子任务应包含：

> 仅使用已授权 SSH 目标执行展示过的只读资源采样。保持一个有界 SSH 会话，采样 CPU、
> GPU、内存，记录 UTC 时间、单位、错误及缺口。不得修改远程状态、安装软件、提权或
> 读取业务数据。凭据不进入输出。到约定截止时间或收到停止消息后终止采样并返回指标。
> 持续观察真实进程句柄，不因一次观察超时重新启动采样；不得把缺测当成 0。

启动时使用 BatchMode=yes 和连接超时，避免卡在密码提示。采样覆盖测试前、测试中和
结束附近；上限取已展示的运行/清理预算，不无限监听。主 Agent 保留子任务/进程引用，
运行终态后通知停止并等待采样任务真实退出。没有子 Agent 时，可用宿主的有界后台进程。

Linux 只读采样可基于以下输出格式，通过一次 SSH sh 会话循环执行；按已确认间隔和次数
加 sleep/循环，禁止无限循环。仅是命令参考，不代表默认已获授权：

```sh
date -u +%s
awk '$1=="cpu" {t=0; for(i=2;i<=9;i++)t+=$i; print "cpu_ticks",t,$5+$6}' /proc/stat
awk '/^MemTotal:/ {t=$2} /^MemAvailable:/ {a=$2} END {print "memory_bytes",(t-a)*1024,t*1024}' /proc/meminfo
nvidia-smi --query-gpu=index,utilization.gpu,memory.used,memory.total --format=csv,noheader,nounits
```

CPU 使用率用相邻两次 tick 差计算：100*(1-idleDelta/totalDelta)。第一次没有差分时留空。
内存以 total-available 计，/proc 的 kB 转 bytes；GPU memory 的 MiB 转 bytes。
GPU 不存在或命令不可用时注明缺测。主机指标不等于进程/容器独占资源，不将其直接归因给服务。

基线采样和执行期采样各自有界；等待用户审核期间不无限保持采样。执行中发现瓶颈时保存
观测、向主 Agent 汇报。当前固定负载不静默改变并发或自动升压；调整并发形成下一版计划，
重新展示并等待“我确定”。用户已要求中止或命中计划中明确的停止条件时按既定规则取消。

## 并入本轮报告

通过 horus_attach_resource_metrics 提交 runId、目标标签、采样来源/状态和有时间戳的数值
样本。source=ssh，status=collected/partial/unavailable；没有实际测量就不构造样本。
notes 包含基线摘要、并发设置依据、执行中瓶颈和后续调整建议，明确哪些是观测、哪些是推断。
报告展示 CPU、内存、GPU 的均值/峰值及时间范围，保留缺失说明。原始日志如包含敏感
内容先脱敏，不上传凭据或完整机器环境。资源观测不改变 HTTP 测试原有判定。

监控失败也完成 HTTP 测试和事实报告，标记 unavailable/partial 并说明原因。基础报告
由核心自动生成，不等待监控；Agent 在呈现最终报告前并入已取得的采样，不无限等待。
