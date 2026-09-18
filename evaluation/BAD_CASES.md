# Baseline Bad Cases

当前 Local baseline 在 120 条固定评测集中通过 118 条。保留失败用例，而不是把测试集改到全绿。

## BM080：区域总体与产品子趋势混合

- 问题：诊断华南总体业绩，同时说明 NovaFlow 最近三周变化。
- 当前行为：产品筛选过早作用于整个区域诊断，因此回答给出 NovaFlow 范围内的达成率，而不是先给华南总体达成率、再给产品子趋势。
- 根因：同一请求里存在两个不同粒度的分析 scope，当前单次 `region_diagnosis` 只维护一组 filters。
- 下一步：把计划拆成「区域总体 query_sales」和「产品 × 周 query_sales」两个调用，再在 synthesis 层分别标注口径。

## BM119：多轮实体与同区域 peer average

- 问题：先查 NM-H013 的销售历史，再追问它与西部区域平均机构销售额的比较。
- 当前行为：追问继承了区域，但没有同时保留原机构实体，也没有执行按机构分组后的区域平均计算。
- 根因：会话状态只保存简单筛选字段，尚未保存“当前主实体 + 比较基准”的关系状态。
- 下一步：增加 typed conversation state，并新增 peer-comparison workflow。

这两个问题均不会被描述为“已解决”。更换代码、数据或 benchmark 后必须重新运行评测。
