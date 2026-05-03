import os
import sys
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


def process_item_costs():
    base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    data_dir = os.path.join(base_dir, "数据", "疑问点数据重新统计结果")
    
    input_file = os.path.join(data_dir, "各类设备平均单价-安徽.xlsx")
    output_file = "物资价格.csv"
    
    print("开始处理物资价格数据")
    
    df = pd.read_excel(input_file)
    print(f"读取数据文件，共 {len(df)} 条记录")
    
    result_columns = [
        'COST_ID',
        'STAT_MONTH',
        'UNIT_CODE',
        'UNIT_NAME',
        'DEVICE_TYPE',
        'DEVICE_CODE',
        'HOLD_COST',
        'SHORTAGE_COST',
        'PRICE'
    ]
    
    result_df = pd.DataFrame(columns=result_columns)
    
    result_df['COST_ID'] = range(1, len(df) + 1)
    
    if '设备码' in df.columns:
        df['设备码'] = df['设备码'].astype(str).str.replace('.0', '', regex=False)
        result_df['DEVICE_CODE'] = df['设备码']
    
    if '含税平均单价' in df.columns:
        result_df['PRICE'] = df['含税平均单价']
    
    print(f"处理完成，共 {len(result_df)} 条记录")
    
    result_df.to_csv(output_file, index=False, encoding='utf-8-sig')
    print(f"结果已保存至: {output_file}")
    
    return result_df


if __name__ == '__main__':
    process_item_costs()
