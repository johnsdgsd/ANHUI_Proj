"""
库存优化业务API蓝图
提供库存优化相关的业务接口
"""

import datetime
import logging
from flask import Blueprint, request, jsonify
from backend.inventory_optimization.RunOptimize import run_optimization_from_api
from backend.inventory_optimization.GetWeeklyThreshold import GenerateWeeklyThreshold
from backend.inventory_optimization.DailyReplenishmentPlan import AdjustDaliyDelivery,DailyReplenishmentPlan
from backend.inventory_optimization.HeuristicDeliveryPlan import AdjustDaliyDeliveryV2
from backend.inventory_optimization.SchedulingDeliveryAdapter import AdjustDaliyDeliveryV3
from backend.DelivPlanV4 import AdjustDaliyDeliveryV4
from backend.inventory_optimization.GetMonthlyOrder import GenerateMonthlyThresholdAndOrder
from backend.inventory_optimization.GaOptimization import GenerateMonthlyThresholdAndOrderGA
# 创建蓝图
inventory_opti_bp = Blueprint('inventory_opti', __name__, url_prefix='/inventory')

def optimize():
    """
    库存优化接口
    """
    try:
        data = request.get_json() or {}
        
        # 获取必需参数
        init_stock_month = data.get('preMonth')
        preConcId = data.get('preConcId')
        install_start_month = None
        install_end_month = None
        tag = 0
        
        # 参数校验
        if not all([init_stock_month,preConcId]):
            return jsonify({
                "success": False,
                "error": "缺少必需参数: preMonth,preConcId"
            }), 400
        
        # 获取可选参数
        n_iter = data.get('n_iter', 100)
        pop_size = data.get('pop_size', 200)
        epsilon = data.get('epsilon', 0.95)
        n_processor = data.get('n_processor', 10)
        
        # 运行优化
        InventoryThreshold,InventoryOrder = run_optimization_from_api(
            init_stock_month=init_stock_month,
            n_iter=n_iter,
            pop_size=pop_size,
            epsilon=epsilon,
            n_processor=n_processor,
            tag = datetime.datetime.now().strftime('%Y%m%d%H%M%S')
        )
        from backend.api.data_api.fetch_data import (
        insert_into_adam_plan_month_ias_pre,insert_into_adam_stock_month_limit_pre)
        result=insert_into_adam_stock_month_limit_pre(InventoryThreshold)
        result=insert_into_adam_plan_month_ias_pre(InventoryOrder)
        
        return jsonify(result)
        
    except Exception as e:
        logging.exception("库存优化接口异常")
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500

# 月度补库和月度阈值（分析版，保留备用）
@inventory_opti_bp.route('/optimize-analytical', methods=['POST'])
def GetMonthThresholdAndOrderAnalytical():
    try:
        data = request.get_json() or {}
        
        # 获取必需参数
        yearMonth = data.get('preMonth')
        preConcId = data.get('preConcId')
        
        # 参数校验
        if not all([yearMonth,preConcId]):
            return jsonify({
                "success": False,
                "error": "缺少必需参数: preMonth,preConcId"
            }), 400
        
        year = yearMonth[:4]
        month = yearMonth[4:6]
        from backend.api.data_api.fetch_data import (
            query_adam_org_stock_sample_estimated,
            insert_into_adam_stock_month_limit_pre,
            insert_into_adam_plan_month_ias_pre,
            update_adam_pre_conc_stat,
            delete_adam_stock_month_limit_pre_by_ym,
            delete_adam_plan_month_ias_pre_by_ym)
        from backend.config.scheme_config import get_approved_scheme_config

        update_adam_pre_conc_stat(int(preConcId),'02')
        init_stock = query_adam_org_stock_sample_estimated(yearMonth)
        print(f'推算月初库存成功，数据量{len(init_stock)}条', flush=True)

        global_scheme_id, epsilon = get_approved_scheme_config(yearMonth)
        print(f'使用审批方案: GLOBAL_SCHEME_ID={global_scheme_id}, epsilon={epsilon}')
        Threshold,Order,_ = GenerateMonthlyThresholdAndOrder(year,month,init_stock,global_scheme_id,epsilon)
        print(f'生成月度阈值数据{len(Threshold)}条，生成月度补货量数据{len(Order)}条', flush=True)

        # 删除旧数据（防御性处理，删除失败不影响后续插入）
        try:
            del_res = delete_adam_stock_month_limit_pre_by_ym(year, month)
            print(f'删除月度阈值旧数据结果{del_res}', flush=True)
        except Exception as e:
            print(f'删除月度阈值旧数据失败（继续执行插入）: {e}', flush=True)

        from backend.api.data_api.fetch_data import query_pk_next
        Threshold['STOCK_MONTH_LIMIT_PRE_ID'] = [int(x) for x in query_pk_next("SEQ_ADAM_STOCK_MONTH_LIMIT_PRE", len(Threshold))]
        result=insert_into_adam_stock_month_limit_pre(Threshold)
        print(f'插入阈值数据结果{result}', flush=True)

        try:
            del_res = delete_adam_plan_month_ias_pre_by_ym(year, month)
            print(f'删除月度补库旧数据结果{del_res}', flush=True)
        except Exception as e:
            print(f'删除月度补库旧数据失败（继续执行插入）: {e}', flush=True)

        Order['PLAN_MONTH_IAS_PRE_ID'] = [int(x) for x in query_pk_next("SEQ_ADAM_PLAN_MONTH_IAS_PRE", len(Order))]
        result=insert_into_adam_plan_month_ias_pre(Order)
        print(f'插入补货量数据结果{result}', flush=True)
        update_adam_pre_conc_stat(int(preConcId),'03')
        
        return jsonify(result)
    
    except Exception as e:
        logging.exception("月度阈值接口异常")
        update_adam_pre_conc_stat(int(preConcId),'04')
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


# 月度补库和月度阈值（遗传算法版）
@inventory_opti_bp.route('/optimize', methods=['POST'])
def GetMonthThresholdAndOrder():
    try:
        data = request.get_json() or {}

        yearMonth = data.get('preMonth')
        preConcId = data.get('preConcId')

        if not all([yearMonth, preConcId]):
            return jsonify({
                "success": False,
                "error": "缺少必需参数: preMonth, preConcId"
            }), 400

        year = yearMonth[:4]
        month = yearMonth[4:6]

        # GA 可选参数
        n_iter = data.get('nIter', 10)
        pop_size = data.get('popSize', 200)
        n_processor = data.get('nProcessor', 10)
        verbose = data.get('verbose', False)

        import time
        t_start = time.time()

        from backend.api.data_api.fetch_data import (
            insert_into_adam_stock_month_limit_pre,
            insert_into_adam_plan_month_ias_pre,
            update_adam_pre_conc_stat,
            delete_adam_stock_month_limit_pre_by_ym,
            delete_adam_plan_month_ias_pre_by_ym)
        from backend.config.scheme_config import get_approved_scheme_config

        print('=' * 60, flush=True)
        print(f'[GA] 遗传算法优化开始: yearMonth={yearMonth}, preConcId={preConcId}', flush=True)
        print(f'[GA] 参数: n_iter={n_iter}, pop_size={pop_size}, n_processor={n_processor}', flush=True)

        # Step 1: 更新状态
        print('[GA] Step 1/6: 更新预处理状态...', flush=True)
        update_adam_pre_conc_stat(int(preConcId), '02')

        # Step 2: 获取方案配置
        print('[GA] Step 2/6: 获取审批方案配置...', flush=True)
        global_scheme_id, epsilon = get_approved_scheme_config(yearMonth)
        print(f'[GA] → GLOBAL_SCHEME_ID={global_scheme_id}, epsilon={epsilon}', flush=True)

        # Step 3: 运行 GA
        print('[GA] Step 3/6: 开始遗传算法寻优...', flush=True)
        t_ga_start = time.time()
        Threshold, Order, _ = GenerateMonthlyThresholdAndOrderGA(
            year=year,
            month=month,
            init_stock=None,
            tag=global_scheme_id,
            alpha=epsilon,
            n_iter=n_iter,
            pop_size=pop_size,
            n_processor=n_processor,
            verbose=verbose
        )
        t_ga_end = time.time()
        print(f'[GA] → 寻优完成, 耗时 {t_ga_end - t_ga_start:.1f}s', flush=True)
        print(f'[GA] → 阈值 {len(Threshold)} 条, 补货量 {len(Order)} 条', flush=True)

        # Step 4: 删旧阈值 + 插新
        print('[GA] Step 4/6: 写入月度阈值表...', flush=True)
        try:
            del_res = delete_adam_stock_month_limit_pre_by_ym(year, month)
            print(f'[GA] → 删除旧阈值: {del_res}', flush=True)
        except Exception as e:
            print(f'[GA] → 删除旧阈值失败(继续): {e}', flush=True)

        from backend.api.data_api.fetch_data import query_pk_next
        Threshold['STOCK_MONTH_LIMIT_PRE_ID'] = [int(x) for x in query_pk_next("SEQ_ADAM_STOCK_MONTH_LIMIT_PRE", len(Threshold))]
        result = insert_into_adam_stock_month_limit_pre(Threshold)
        print(f'[GA] → 插入阈值: {result}', flush=True)

        # Step 5: 删旧补货 + 插新
        print('[GA] Step 5/6: 写入月度补货量表...', flush=True)
        try:
            del_res = delete_adam_plan_month_ias_pre_by_ym(year, month)
            print(f'[GA] → 删除旧补货: {del_res}', flush=True)
        except Exception as e:
            print(f'[GA] → 删除旧补货失败(继续): {e}', flush=True)

        Order['PLAN_MONTH_IAS_PRE_ID'] = [int(x) for x in query_pk_next("SEQ_ADAM_PLAN_MONTH_IAS_PRE", len(Order))]
        result = insert_into_adam_plan_month_ias_pre(Order)
        print(f'[GA] → 插入补货: {result}', flush=True)

        # Step 6: 更新状态
        print('[GA] Step 6/6: 更新完成状态...', flush=True)
        update_adam_pre_conc_stat(int(preConcId), '03')

        t_end = time.time()
        print(f'[GA] 全部完成, 总耗时 {t_end - t_start:.1f}s', flush=True)
        print('=' * 60, flush=True)

        return jsonify(result)

    except Exception as e:
        logging.exception("[GA] 月度阈值接口异常")
        update_adam_pre_conc_stat(int(preConcId), '04')
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


# 周度阈值
@inventory_opti_bp.route('/generate-weekly-threshold', methods=['POST'])
def GenerateWeeklyThresholdRoute():
    from backend.api.data_api.fetch_data import  update_adam_pre_conc_stat
    """生成周度库存阈值并插入数据库"""
    try:
        data = request.get_json() or {}
        yearMonth = data.get('preMonth')
        year = yearMonth[:4]
        month = yearMonth[4:6]
        preConcId = data.get('preConcId')

        if not year or not month or not preConcId:
            return jsonify({"success": False, "error": "缺少必需参数"}), 400
        update_adam_pre_conc_stat(int(preConcId), '02')
        dfThreshold,result = GenerateWeeklyThreshold(year, month)
        update_adam_pre_conc_stat(int(preConcId), '03')
        return jsonify(result)
    except Exception as e:
        logging.exception("周度阈值接口异常")
        update_adam_pre_conc_stat(int(preConcId), '04')
        return jsonify({"success": False, "error": str(e)}), 500


# @inventory_opti_bp.route('/adjust-daily-delivery', methods=['POST'])
def AdjustDailyDeliveryPlan():
    """
    调整日补库计划接口
    请求参数（JSON）:
        adjustDate: 调整日期，如 "2026-05-06"
    """
    from backend.api.data_api.fetch_data import (
        insert_into_adam_dist_scheme,insert_into_adam_dist_scheme_det,
        update_adam_pre_conc_stat)
    try:
        Data = request.get_json() or {}
        AdjustDate = Data.get('adjustDate')
        preConcId = Data.get('preConcId')
        if not AdjustDate:
            return jsonify({
                "success": False,
                "error": "缺少必需参数: adjustDate"
            }), 400
        update_adam_pre_conc_stat(int(preConcId), '02')
        MainScheme, DetailScheme = AdjustDaliyDelivery(AdjustDate)

        # 插入配送主表
        MainResult = insert_into_adam_dist_scheme(MainScheme)
        # 插入配送明细表
        DetailResult = insert_into_adam_dist_scheme_det(DetailScheme)

        TotalSuccess = MainResult.get('success_count', 0) + DetailResult.get('success_count', 0)
        TotalFailed = MainResult.get('failed_count', 0) + DetailResult.get('failed_count', 0)

        update_adam_pre_conc_stat(int(preConcId), '03')
        return jsonify({
            "success": TotalFailed == 0,
            "message": f"日补库计划调整完成",
            "mainResult": MainResult,
            "detailResult": DetailResult
        })

    except Exception as E:
        logging.exception("单日配送调整接口异常")
        update_adam_pre_conc_stat(int(preConcId), '04')
        return jsonify({
            "success": False,
            "error": str(E)
        }), 500


@inventory_opti_bp.route('/adjust-daily-delivery-v2', methods=['POST'])
def AdjustDailyDeliveryPlanRange():
    """
    调整日补库计划接口
    请求参数（JSON）:
        dateList: 需要调整的日期列表，如 ["2026-01-01", "2026-01-02", "2026-02-03"]
        preConcId: 预测结果表ID
    """
    from backend.api.data_api.fetch_data import (
        insert_into_adam_dist_scheme, insert_into_adam_dist_scheme_det,
        update_adam_pre_conc_stat)

    try:
        Data = request.get_json() or {}
        date_list = Data.get('dateList')
        preConcId = Data.get('preConcId')
        if not date_list or not isinstance(date_list, list) or len(date_list) == 0:
            return jsonify({
                "success": False,
                "error": "参数错误，必须提供非空的 dateList 日期列表"
            }), 400

        # 更新状态为处理中
        update_adam_pre_conc_stat(int(preConcId), '02')

        all_main_results = []
        all_detail_results = []
        total_success = 0
        total_failed = 0
        failed_dates = []

        for date_str in date_list:
            try:
                MainScheme, DetailScheme = AdjustDaliyDelivery(date_str)
                # 插入配送主表
                main_res = insert_into_adam_dist_scheme(MainScheme)
                # 插入配送明细表
                detail_res = insert_into_adam_dist_scheme_det(DetailScheme)
                all_main_results.append(main_res)
                all_detail_results.append(detail_res)
                total_success += main_res.get('success_count', 0) + detail_res.get('success_count', 0)
                total_failed += main_res.get('failed_count', 0) + detail_res.get('failed_count', 0)
            except Exception as e:
                logging.exception(f"调整日配送失败, date={date_str}")
                total_failed += 1
                failed_dates.append(date_str)
                continue

        if total_failed == 0:
            update_adam_pre_conc_stat(int(preConcId), '03')
        else:
            update_adam_pre_conc_stat(int(preConcId), '04')

        return jsonify({
            "success": total_failed == 0,
            "message": f"日补库计划调整完成，共处理 {len(date_list)} 天，成功 {total_success} 条记录，失败 {total_failed} 条",
            "failed_dates": failed_dates,
            "mainResults": all_main_results,
            "detailResults": all_detail_results
        })

    except Exception as E:
        logging.exception("批量日配送调整接口异常")
        update_adam_pre_conc_stat(int(preConcId), '04')
        return jsonify({
            "success": False,
            "error": str(E)
        }), 500




@inventory_opti_bp.route('/generate-daily-replenishment', methods=['POST'])
def GenerateDailyReplenishmentPlan():
    """
    生成日度补库计划接口
    请求参数（JSON）:
        startDate: 计划起始日期，如 "2026-05-01"
        endDate: 计划结束日期，如 "2026-05-31"
    """
    try:
        Data = request.get_json() or {}
        StartDate = Data.get('start_date')
        EndDate = Data.get('end_date')

        if not StartDate or not EndDate:
            return jsonify({
                "success": False,
                "error": "缺少必需参数: start_date, end_date"
            }), 400

        # 生成日度补库计划
        DaliyReplPlan, result = DailyReplenishmentPlan(StartDate, EndDate)

        TotalSuccess = result.get('success_count', 0)
        TotalFailed = result.get('failed_count', 0)

        return jsonify({
            "success": TotalFailed == 0,
            "message": f"日度补库计划生成完成",
            "result": result
        })

    except Exception as E:
        logging.exception("日度补库计划接口异常")
        return jsonify({
            "success": False,
            "error": str(E)
        }), 500


@inventory_opti_bp.route('/adjust-daily-delivery-v3', methods=['POST'])
def AdjustDailyDeliveryPlanRangeV2():
    """
    调整日补库计划接口 V2（启发式算法）
    请求参数（JSON）:
        dateList: 需要调整的日期列表，如 ["2026-01-01", "2026-01-02"]
        preConcId: 预测结果表ID
        maxStops:   每车最多站点数（可选，默认 5）
        maxIter:    迭代次数（可选，默认 600）
    """
    from backend.api.data_api.fetch_data import (
        insert_into_adam_dist_scheme, insert_into_adam_dist_scheme_det,
        update_adam_pre_conc_stat)

    try:
        Data = request.get_json() or {}
        date_list = Data.get('dateList')
        preConcId = Data.get('preConcId')
        max_stops = Data.get('maxStops', 3)
        max_iter = Data.get('maxIter', 600)

        if not date_list or not isinstance(date_list, list) or len(date_list) == 0:
            return jsonify({
                "success": False,
                "error": "参数错误，必须提供非空的 dateList 日期列表"
            }), 400

        update_adam_pre_conc_stat(int(preConcId), '02')

        all_main_results = []
        all_detail_results = []
        total_success = 0
        total_failed = 0
        failed_dates = []

        for date_str in date_list:
            try:
                MainScheme, DetailScheme = AdjustDaliyDeliveryV2(
                    date_str, max_stops=max_stops, max_iter=max_iter
                )
                main_res = insert_into_adam_dist_scheme(MainScheme)
                detail_res = insert_into_adam_dist_scheme_det(DetailScheme)
                all_main_results.append(main_res)
                all_detail_results.append(detail_res)
                total_success += main_res.get('success_count', 0) + detail_res.get('success_count', 0)
                total_failed += main_res.get('failed_count', 0) + detail_res.get('failed_count', 0)
            except Exception as e:
                logging.exception(f"调整日配送V2失败, date={date_str}")
                total_failed += 1
                failed_dates.append(date_str)
                continue

        if total_failed == 0:
            update_adam_pre_conc_stat(int(preConcId), '03')
        else:
            update_adam_pre_conc_stat(int(preConcId), '04')

        return jsonify({
            "success": total_failed == 0,
            "message": f"日补库计划调整V2完成，共处理 {len(date_list)} 天，成功 {total_success} 条记录，失败 {total_failed} 条",
            "failed_dates": failed_dates,
            "mainResults": all_main_results,
            "detailResults": all_detail_results
        })

    except Exception as E:
        logging.exception("批量日配送调整V2接口异常")
        update_adam_pre_conc_stat(int(preConcId), '04')
        return jsonify({
            "success": False,
            "error": str(E)
        }), 500


@inventory_opti_bp.route('/adjust-daily-delivery-v4', methods=['POST'])
def AdjustDailyDeliveryPlanV3():
    """
    调整日补库计划接口 V3（ALNS 算法，复用 Scheduling 模块）

    请求参数（JSON）:
        dateList:   需要调整的日期列表，如 ["2026-01-01"]
        preConcId:  预测结果表ID
        maxStops:   每车最多站点数（可选，默认 3）
        maxIter:    迭代次数（可选，默认 600）
    """
    from backend.api.data_api.fetch_data import (
        insert_into_adam_dist_scheme, insert_into_adam_dist_scheme_det,
        update_adam_pre_conc_stat)

    preConcId = None

    try:
        Data = request.get_json(silent=True) or {}
        date_list = Data.get('dateList')
        preConcId = Data.get('preConcId')
        max_stops = Data.get('maxStops', 3)
        max_iter = Data.get('maxIter', 600)

        if not preConcId:
            return jsonify({"success": False, "error": "缺少必需参数: preConcId"}), 400
        if not date_list or not isinstance(date_list, list) or len(date_list) == 0:
            return jsonify({"success": False, "error": "参数错误，必须提供非空的 dateList 日期列表"}), 400

        update_adam_pre_conc_stat(int(preConcId), '02')

        all_main_results = []
        all_detail_results = []
        total_success = 0
        total_failed = 0
        failed_dates = []

        for date_str in date_list:
            try:
                MainScheme, DetailScheme = AdjustDaliyDeliveryV3(
                    date_str, max_stops=max_stops, max_iter=max_iter
                )
                main_res = insert_into_adam_dist_scheme(MainScheme)
                detail_res = insert_into_adam_dist_scheme_det(DetailScheme)
                all_main_results.append(main_res)
                all_detail_results.append(detail_res)
                total_success += main_res.get('success_count', 0) + detail_res.get('success_count', 0)
                total_failed += main_res.get('failed_count', 0) + detail_res.get('failed_count', 0)
            except Exception as e:
                logging.exception(f"调整日配送V3失败, date={date_str}")
                total_failed += 1
                failed_dates.append(date_str)
                continue

        if total_failed == 0:
            update_adam_pre_conc_stat(int(preConcId), '03')
        else:
            update_adam_pre_conc_stat(int(preConcId), '04')

        return jsonify({
            "success": total_failed == 0,
            "message": f"日补库计划调整V3完成，共处理 {len(date_list)} 天，成功 {total_success} 条记录，失败 {total_failed} 条",
            "failed_dates": failed_dates,
            "mainResults": all_main_results,
            "detailResults": all_detail_results
        })

    except Exception as E:
        logging.exception("日配送调整V3接口异常")
        if preConcId is not None:
            update_adam_pre_conc_stat(int(preConcId), '04')
        return jsonify({"success": False, "error": str(E)}), 500


@inventory_opti_bp.route('/adjust-daily-delivery', methods=['POST'])
def AdjustDailyDeliveryPlanV4():
    """
    调整日补库计划接口 V4（集合划分 ILP 算法）

    请求参数（JSON）:
        dateList:   需要调整的日期列表，如 ["2026-01-01"]
        preConcId:  预测结果表ID
    """
    from backend.api.data_api.fetch_data import (
        insert_into_adam_dist_scheme, insert_into_adam_dist_scheme_det,
        update_adam_pre_conc_stat)

    preConcId = None

    try:
        Data = request.get_json(silent=True) or {}
        date_list = Data.get('dateList')
        preConcId = Data.get('preConcId')

        if not preConcId:
            return jsonify({"success": False, "error": "缺少必需参数: preConcId"}), 400
        if not date_list or not isinstance(date_list, list) or len(date_list) == 0:
            return jsonify({"success": False, "error": "参数错误，必须提供非空的 dateList 日期列表"}), 400

        update_adam_pre_conc_stat(int(preConcId), '02')

        all_main_results = []
        all_detail_results = []
        total_success = 0
        total_failed = 0
        failed_dates = []

        for date_str in date_list:
            try:
                MainScheme, DetailScheme = AdjustDaliyDeliveryV4(date_str)
                main_res = insert_into_adam_dist_scheme(MainScheme)
                detail_res = insert_into_adam_dist_scheme_det(DetailScheme)
                all_main_results.append(main_res)
                all_detail_results.append(detail_res)
                total_success += main_res.get('success_count', 0) + detail_res.get('success_count', 0)
                total_failed += main_res.get('failed_count', 0) + detail_res.get('failed_count', 0)
            except Exception as e:
                logging.exception(f"调整日配送V4失败, date={date_str}")
                total_failed += 1
                failed_dates.append(date_str)
                continue

        if total_failed == 0:
            update_adam_pre_conc_stat(int(preConcId), '03')
        else:
            update_adam_pre_conc_stat(int(preConcId), '04')

        return jsonify({
            "success": total_failed == 0,
            "message": f"日补库计划调整V4完成，共处理 {len(date_list)} 天，成功 {total_success} 条记录，失败 {total_failed} 条",
            "failed_dates": failed_dates,
            "mainResults": all_main_results,
            "detailResults": all_detail_results
        })

    except Exception as E:
        logging.exception("日配送调整V4接口异常")
        if preConcId is not None:
            update_adam_pre_conc_stat(int(preConcId), '04')
        return jsonify({"success": False, "error": str(E)}), 500