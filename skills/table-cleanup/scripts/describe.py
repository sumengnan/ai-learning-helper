"""表格数据概览：用法 python describe.py <csv 路径>"""
import sys

import pandas as pd

path = sys.argv[1]
try:
    df = pd.read_csv(path)
except UnicodeDecodeError:
    df = pd.read_csv(path, encoding="gbk")

print("形状(行, 列):", df.shape)
print("\n每列缺失值:")
print(df.isna().sum())
print("\n每列类型:")
print(df.dtypes)
print("\n前 5 行:")
print(df.head())
