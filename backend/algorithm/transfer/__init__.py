"""
二阶段 调拨模块

调拨计划每月1次，由月度补库计划表差额触发，分两大业务场景：
    场景一 月初高库龄调拨：供应点=有高库龄富余的库房，需求点=有缺口的单位，
            整数线性规划最小化总运输距离（本包 ilp_solver 实现）。
    场景二 缺货调拨：实时监控缺货风险，省中心有货走紧急补库，无货走跨单位调拨。

核心算法各自独立成包：
    调拨算法 → backend.algorithm.transfer（本包）
    紧急补库算法 → backend.algorithm.emerg_replenish（另建包，待开发）
二者通过一个编排函数联动（后续实现）。

参考实现风格：DelivPlanV4（ilp_solver / config / orchestrator 分层，纯建模求解不含业务逻辑）。
"""
