"""
仿真数据生成 — 主入口

用法:
    python -m data_cleaning.simulation.run

或:
    python run.py
"""

import logging
import os
import sys

# 路径设置
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJ_DIR = os.path.dirname(os.path.dirname(os.path.dirname(_BASE_DIR)))
sys.path.insert(0, os.path.join(_PROJ_DIR, 'backend'))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

from data_cleaning.simulation.simulator import run_simulation

if __name__ == "__main__":
    logging.info("仿真开始 — 1-6月补库数据生成")
    df = run_simulation()
    logging.info(f"仿真结束 — 共 {len(df)} 条记录")
