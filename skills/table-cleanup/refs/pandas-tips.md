# pandas 分组与透视速查

- 分组聚合：`df.groupby("列").agg({"值列": ["mean", "sum", "count"]})`
- 透视表：`pd.pivot_table(df, index=..., columns=..., values=..., aggfunc="mean")`
- 去重：`df.drop_duplicates(subset=["列1", "列2"])`
- 缺失填充：`df["列"].fillna(df["列"].median())`
- 类型转换：`df["列"] = pd.to_numeric(df["列"], errors="coerce")`
