"""
调拨场景二（缺货调拨）— 独立包

业务流（《调拨场景二_缺货调拨_方案设计.md》）:
    缺货判定 → 省中心分流（orchestrator 唯一业务逻辑）→ 紧急补库 / 贪心调拨

模块独立性（用户确认 2026-08-07）:
    stockout_detect  — 【独立】缺货判定：库存+日需求+日计划 → 缺货组合
    supplier_select  — 【独立】调出单位+调出数量判定：库存+库存上限 → 供应点可调出量
    emergency        — 【独立】紧急补库核心：写 ADAM_PLAN_DAY_IAS_PRE（REPLE_TASK_TYPE='02'）
    transfer         — 【独立】调拨核心：贪心分配（高库龄→距离），写 ADAM_ALLOT_DAY_PLAN_PRE
    orchestrator     — 【仅分流】编排：调各独立模块，省中心有货→紧急补库，无货→调拨

四个核心模块互不依赖，只依赖 common（公共工具）/ config（公共配置）/ fetch_data；
更换任一核心逻辑只需替换对应模块，编排与其余模块不受影响。

复用（不复制）:
    transfer.data_prep.prepare_transfer_network        — 87家单位 + 距离矩阵 + 完整性校验
    transfer.orchestrator._compute_14day_demand        — 库存上限（未来两周服务水平）当前实现

阶段二紧急补库与阶段一完全独立（用户确认 2026-08-07）:
    缺货检测逻辑自 EmergReplenishV2 **复制**进 stockout_detect._stockout_check，
    阶段二独立维护，不依赖 backend.EmergReplenish 包。
"""
