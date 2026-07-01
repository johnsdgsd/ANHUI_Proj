# 库存优化与检定排程系统

计量设备（电能表、互感器、终端等）的库存优化与检定配送排程系统。基于需求预测、成本分析和启发式算法，生成月度补库计划、日配送方案、全局优化方案和紧急补库建议。

## 技术栈

- **语言**: Python 3
- **Web 框架**: Flask（蓝图路由）
- **数据库**: 达梦
- **优化算法**: 遗传算法（PyGAD）、ALNS 启发式搜索、泊松分布需求模型
- **距离计算**: geopy（geodesic）

## 项目结构

```
backend/
├── api/                        # API 层
│   ├── run.py                  # Flask 入口，注册所有蓝图
│   ├── business_api/           # 业务 API 蓝图
│   │   ├── InventoryOptiApi.py     # 库存优化接口
│   │   ├── GlobalOptimizationApi.py # 全局优化接口
│   │   ├── EmergencyApi.py         # 紧急补库接口
│   │   └── TransferApi.py          # 调拨计划接口
│   └── data_api/
│       └── fetch_data.py           # 数据库查询与写入封装
├── inventory_optimization/     # 库存优化模块
│   ├── RunOptimize.py              # 优化主流程（遗传算法求解 α）
│   ├── GetMonthlyOrder.py          # 月度补库计划生成
│   ├── DailyReplenishmentPlan.py   # 日补库计划与配送 V1
│   ├── HeuristicDeliveryPlan.py    # 日配送 ALNS 启发式算法 V2
│   ├── GetWeeklyThreshold.py       # 周阈值生成
│   ├── optimizer.py                # 优化器核心
│   ├── warehouse.py                # 仓库模型
│   ├── item.py                     # 设备/物料模型
│   └── demand_distribution.py      # 需求分布（泊松/正态/均匀）
├── Scheduling/                 # 检定排程模块
│   ├── main.py                     # 排程 API 蓝图
│   ├── GetArrPlan.py               # 到货计划
│   ├── GetDelivPlan.py             # 配送计划
│   ├── GetCheckDeliverPlan.py      # 检定配送计划
│   ├── Service_CheckDeliver.py     # 检定配送服务
│   └── GetPathDis.py               # 路径距离计算
├── global_optimization/        # 全局优化模块
│   └── multi_plan_generator.py     # 多方案生成（ε=0.99/0.995/0.999）
├── EmergReplenish/             # 紧急补库模块
│   ├── EmergReplenish.py           # 紧急补库 V1（月度需求驱动）
│   └── EmergReplenishV2.py         # 紧急补库 V2（日需求驱动）
├── Transfer/                   # 调拨计划模块
│   └── GetTransferScheme.py        # 调拨方案生成
├── demand_forecast/            # 需求预测模块
│   ├── forecast_model.py           # 预测模型
│   └── forecast_service.py         # 预测服务
├── data_cleaning/              # 数据清洗模块
│   ├── data_cleaner.py             # 数据清洗
│   ├── process_inventory_data.py   # 库存数据处理
│   ├── process_replenish_data.py   # 补库数据处理
│   └── extract_device_mapping.py   # 设备码映射提取
├── config/                     # 配置
│   ├── config.py                   # 数据库/服务器配置
│   └── scheme_config.py            # 方案参数配置
├── common/                     # 公共模块
│   ├── database.py                 # 数据库连接
│   └── logger.py                   # 日志
├── utils/                      # 工具
│   └── GetPathDis.py               # 距离矩阵工具
├── CLAUDE.md                   # AI 辅助配置
├── vision.js                   # 识图脚本
└── README.md
```

## 核心功能模块

### 1. 库存优化（inventory_optimization）

- **月度补库计划**：基于需求预测和服务水平 α，利用泊松分布计算安全库存，生成各单位的月度补库量
- **补库量取整**：按设备类别（互感器 36/箱、终端 20/箱、单相表 60/箱、三相表 20/箱）向上取整
- **日配送计划 V2**：ALNS 启发式算法，支持可配置站点数（默认 3 站）、750km 往返软约束、45° 扇形硬约束

### 2. 检定排程（Scheduling）

- 到货计划生成、检定计划编排、配送调度
- 支持自动检定线和人工检定线配置
- 基于省级库存的到货量/检定量公式计算

### 3. 全局优化（global_optimization）

- 按 ε = 0.99 / 0.995 / 0.999 生成三套对比方案
- 五项成本独立计算：采购、到货、检定、配送、仓储
- 方案分类：成本最低 → 成本优先(01)、剩余中周转最高 → 周转优先(02)、其余 → 均衡(03)
- 输出主表、周转明细表、环节计划表、成本明细表

### 4. 紧急补库（EmergReplenish）

- **V1**：基于月度需求总量和日补库计划，Poisson 分位数判断触发条件
- **V2**：基于日需求预测逐日累加，支持有/无补库计划两种场景，最多提前 7 天建议

### 5. 调拨计划（Transfer）

- 省级库存向地方仓库的调拨方案生成

## API 蓝图

| 蓝图 | URL 前缀 | 说明 |
|---|---|---|
| `inventory_opti_bp` | `/inventory` | 库存优化、日配送、月度计划 |
| `aps_scheduling_bp` | `/api/aps` | 检定排程调度 |
| `global_optimization_bp` | `/global` | 全局优化方案生成 |
| `transfer_bp` | `/transfer` | 调拨计划 |
| `emergency_bp` | `/emergency` | 紧急补库检查 |

## 快速开始

### 环境要求

```bash
pip install flask pandas numpy scipy geopy pygad
```

### 配置

编辑 `config/config.py` 中的数据库连接信息：

```python
API_CONFIG = {
    'server': {'host': '0.0.0.0', 'port': 5000},
    'database': {'host': 'localhost', 'port': 8080},
}
```

### 启动

```bash
cd backend
python api/run.py
```

### 健康检查

```bash
curl http://localhost:5000/health
```

## 关键算法参数

| 参数 | 默认值 | 说明 |
|---|---|---|
| 月度到货工时 | 160h | 20天 × 8h |
| 到货人数 | 1人 | 固定 |
| 日薪默认 | 200元 | 成本配置表无值时 |
| 配送单价 | 0.0695 元/箱·km | 固定 |
| 道路修正系数 | ×1.15 | 直线→实际里程 |
| 到货安全系数 | ×1.25（两次） | 排程公式 |
| 仓储年利率 | 5% | 可配置 |
| 二级市年仓储费 | 410,000 元 | 仅 ORG_NO 长度=5 |
| max_stops | 3 | 每车最多站点数 |
| 往返距离约束 | 750km | 软约束 |
| 扇形角度约束 | 45° | 硬约束 |
